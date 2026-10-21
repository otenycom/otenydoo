import datetime
import logging
import os
import time

from odoo import api, fields, models
from odoo.tools import split_every

from ..tools.cloud_store import CloudStoreError
from .ir_attachment import UPLOAD_CALL_BUDGET_SECONDS

_logger = logging.getLogger(__name__)

# A file read in the last minutes may be in a download that is still running: never evict it.
EVICT_PROTECT_MINUTES = 5
# An eviction run stops at this share of the limit; the room stops a run on every new file (D21).
EVICT_TARGET = 0.9
# One call handles at most this many candidates and stops after the same time budget as one call of the
# upload job. In a cron run, each call commits (and so frees its row locks) and reports its progress.
EVICT_CALL_BUDGET_SECONDS = UPLOAD_CALL_BUDGET_SECONDS
# The databases whose eviction run goes on: the previous call stopped before the cache was at the target.
# A call that finds the cache under the limit (its own predecessor made it so) must still continue to 90%.
_EVICTING = set()

# The read count halves every day (D21): a read of 3 days ago weighs 1/8. The
# fade is applied when a count is written, so no daily job rolls counters over.
# `cache` is the alias of the table in the statements below; %(now)s is the
# time of the write.
FADED_READS = (
    "COALESCE(cache.reads, 0) * power(0.5::float8,"
    " GREATEST(EXTRACT(EPOCH FROM (%(now)s - COALESCE(cache.reads_date, %(now)s))), 0)::float8 / 86400)"
)


class CloudFilestoreCache(models.Model):
    """One row per file name that the read cache knows (D21).

    A local file whose row has the "in bucket" mark is a cache copy: the
    bucket holds the same file, so the local copy may be evicted later. Every
    other local file must stay (a clone's own file, a file waiting for
    upload). The read counts tell which cache copies are worth keeping.
    Rows are written with SQL upserts, because two workers can mark or count
    the same name at the same time and a duplicate must never fail a save.
    """

    _name = "oteny.cloud.filestore.cache"
    _description = "Cloud filestore cache copy"
    # The rows change on almost every read of a bucket file and on every eviction run. They
    # hold no business data (only a file name, a size and read counts), so an audit row per
    # change would only grow the audit log. A plain class attribute that oteny_audit reads,
    # so this module does not depend on it.
    _oteny_audit_ignore = True

    name = fields.Char(required=True, help="The file name in the local filestore and in the bucket.")
    size = fields.Integer(help="The size of the file in bytes.")
    reads = fields.Float(
        help="The read count; it halves every day, so it approximates the reads of the last 3 days."
    )
    reads_date = fields.Datetime(help="When the read count was last updated.")
    last_read = fields.Datetime()
    in_bucket = fields.Boolean(
        string="In Bucket",
        help="The bucket holds this file, so the local file is a cache copy that may be evicted.",
    )

    _name_unique = models.Constraint("UNIQUE(name)", "A file name has only one cache row.")

    @api.model
    def _cloud_filestore_cache_execute(self, query, params):
        """Run a cache write; quiet in the read path's own transaction.

        There a lock timeout of one second drops the write on purpose (a hint
        that waits on the request's own lock), and the caller logs a warning.
        Odoo would also log the cancelled statement as an `odoo.sql_db` ERROR,
        which reads like a fault in the server log, so that log is switched off
        there. Everywhere else Odoo logs as usual.
        """
        quiet = self.env.context.get("cloud_filestore_quiet_sql", False)
        self.env.cr.execute(query, params, log_exceptions=not quiet)

    @api.model
    def _cloud_filestore_cache_mark(self, fname, size):
        """Set the "in bucket" mark: the bucket confirmed the file, the local copy stays as a cache copy.

        A new row starts with one read, made now (GDSF counts the insertion as
        the first reference), so a new file, which is often read soon, does
        not go before every file that was read once.

        A name that a local-only file uses is never marked, and a mark it has
        is cleared: the local-only file must never depend on the bucket.
        """
        if self.env["ir.attachment"]._cloud_filestore_local_only_uses(fname):
            self._cloud_filestore_cache_unmark([fname])
            return
        now = fields.Datetime.now()
        self.flush_model()
        self._cloud_filestore_cache_execute(
            f"""
            INSERT INTO {self._table} AS cache
                (name, size, reads, reads_date, last_read, in_bucket,
                 create_uid, create_date, write_uid, write_date)
            VALUES (%(name)s, %(size)s, 1, %(now)s, %(now)s, TRUE, %(uid)s, %(now)s, %(uid)s, %(now)s)
            ON CONFLICT (name) DO UPDATE SET
                reads = {FADED_READS},
                reads_date = %(now)s,
                last_read = %(now)s,
                size = EXCLUDED.size,
                in_bucket = TRUE,
                write_uid = %(uid)s,
                write_date = %(now)s
            """,
            {"name": fname, "size": size, "now": now, "uid": self.env.uid},
        )
        # The rows changed under the ORM: a later read must see them.
        self.invalidate_model()

    @api.model
    def _cloud_filestore_cache_flush(self, counts):
        """Write the read counts of a worker; `counts` maps a file name to its number of reads.

        Only names with a row are counted: a local file without a row is not
        a cache copy, so it gets none. The mark itself is never written here.
        """
        if not counts:
            return
        now = fields.Datetime.now()
        names, reads = zip(*counts.items())
        self.flush_model()
        self._cloud_filestore_cache_execute(
            f"""
            UPDATE {self._table} AS cache SET
                reads = {FADED_READS} + counted.reads,
                reads_date = %(now)s,
                last_read = %(now)s,
                write_uid = %(uid)s,
                write_date = %(now)s
              FROM unnest(%(names)s::varchar[], %(reads)s::float8[]) AS counted(name, reads)
             WHERE cache.name = counted.name
            """,
            {"names": list(names), "reads": list(reads), "now": now, "uid": self.env.uid},
        )
        # The rows changed under the ORM (raw SQL): a later read must see them.
        self.invalidate_model()

    @api.model
    def _cloud_filestore_cache_unmark(self, names):
        """Clear the "in bucket" mark: these local files must stay, they are no cache copies (any more)."""
        self.flush_model()
        for batch in split_every(self.env.cr.IN_MAX, names):
            self._cloud_filestore_cache_execute(
                f"UPDATE {self._table} SET in_bucket = FALSE, write_uid = %s, write_date = %s"
                " WHERE name IN %s AND in_bucket",
                [self.env.uid, fields.Datetime.now(), tuple(batch)],
            )
        # The rows changed under the ORM (raw SQL): a later read must see them.
        self.invalidate_model()

    @api.model
    def _cloud_filestore_cache_evict(self, limit=100):
        """Evict cache copies until the cache is at 90% of its limit; return the number of evicted files (D21).

        The cron runs this every hour. The eviction order is the keep value
        (GDSF): the recent reads (the same fading count as in FADED_READS)
        times a size factor that favours small files. Before a local file is
        removed, the bucket must hold it with the same size: else the local
        file is the only copy, and it stays. A name that a local-only file
        also uses loses its mark and stays: that file never depends on the
        bucket.

        A call handles at most `limit` candidates and stops after
        EVICT_CALL_BUDGET_SECONDS, because every candidate costs one request
        to the bucket and the row locks of the call block the read counts. A
        cron run reports its progress and commits after the call, and Odoo
        calls the job again at once while candidates are left. Every call
        starts from the database, so no state passes between calls.
        """
        started = time.monotonic()
        attachment = self.env["ir.attachment"]
        if attachment._cloud_filestore_mode() == "off":
            return 0
        store = attachment._cloud_filestore_store()
        if store is None:
            return 0
        self.flush_model()
        self.env.cr.execute(f"SELECT id, name FROM {self._table} WHERE in_bucket")
        vanished = [row[0] for row in self.env.cr.fetchall() if not os.path.exists(attachment._full_path(row[1]))]
        # Odoo's own cleanup or a delete removed the file: the row has nothing left to describe.
        for batch in split_every(self.env.cr.IN_MAX, vanished):
            self.env.cr.execute(f"DELETE FROM {self._table} WHERE id IN %s", [tuple(batch)])
        # A name that a local-only file uses stays local for good: never evict it, and clear its mark.
        attachment.flush_model(["store_fname", "cloud_local_only"])
        self.env.cr.execute(
            f"""
            UPDATE {self._table} AS cache SET in_bucket = FALSE, write_uid = %s, write_date = %s
             WHERE cache.in_bucket
               AND EXISTS (SELECT 1 FROM ir_attachment
                            WHERE store_fname = cache.name AND cloud_local_only IS TRUE)
            """,
            [self.env.uid, fields.Datetime.now()],
        )
        local_only = self.env.cr.rowcount
        limit_bytes = attachment._cloud_filestore_cache_limit()
        now = fields.Datetime.now()
        self.env.cr.execute(
            f"""
            SELECT cache.name, cache.size, cache.last_read,
                   {FADED_READS} * (1 + (%(limit)s / 100.0) / GREATEST(cache.size, 1)) AS keep_value,
                   SUM(cache.size) OVER () AS total
              FROM {self._table} AS cache
             WHERE cache.in_bucket
             ORDER BY keep_value, cache.id
            """,
            {"limit": float(limit_bytes), "now": now},
        )
        rows = self.env.cr.fetchall()
        total = int(rows[0][4]) if rows else 0
        protected_since = now - datetime.timedelta(minutes=EVICT_PROTECT_MINUTES)
        evicted = cleared = 0
        stopped = out_of_time = False
        remaining = 0
        dbname = self.env.cr.dbname
        if total > (limit_bytes * EVICT_TARGET if dbname in _EVICTING else limit_bytes):
            for index, (name, size, last_read, _keep_value, _total) in enumerate(rows):
                if total <= limit_bytes * EVICT_TARGET:
                    break
                if last_read and last_read > protected_since:
                    continue
                if evicted + cleared >= limit or time.monotonic() - started > EVICT_CALL_BUDGET_SECONDS:
                    out_of_time = evicted + cleared < limit
                    # The candidates that the next call needs to reach the target; protected rows never count.
                    still_over = total - limit_bytes * EVICT_TARGET
                    for _name, later_size, later_read, _later_keep, _later_total in rows[index:]:
                        if still_over <= 0:
                            break
                        if not (later_read and later_read > protected_since):
                            remaining += 1
                            still_over -= later_size
                    break
                try:
                    bucket_size = store.head(name)
                except CloudStoreError as error:
                    # No eviction while the bucket cannot be reached: the cache may grow above the limit.
                    _logger.warning("Cloud filestore: cache eviction stopped at %s: %s", name, error)
                    stopped = True
                    break
                if bucket_size != size:
                    # The bucket does not hold this file (any more): the local file is the only copy and
                    # stays. Without the mark, the upload job picks it up again.
                    self._cloud_filestore_cache_unmark([name])
                    cleared += 1
                    total -= size
                    continue
                try:
                    os.unlink(attachment._full_path(name))
                except FileNotFoundError:
                    pass
                except OSError as error:
                    _logger.warning("Cloud filestore: cache copy %s not removed: %s", name, error)
                    continue
                self.env.cr.execute(f"DELETE FROM {self._table} WHERE name = %s", [name])
                evicted += 1
                total -= size
        if remaining:
            _EVICTING.add(dbname)
        else:
            _EVICTING.discard(dbname)
        # The rows changed under the ORM (raw SQL): a later read must see them.
        self.invalidate_model()
        _logger.info(
            "Cloud filestore cache eviction: %d evicted, %d cleared, %d kept for a local-only file, "
            "%d vanished, %d bytes left of %d, %d candidates left for the next call%s%s",
            evicted,
            cleared,
            local_only,
            len(vanished),
            total,
            limit_bytes,
            remaining,
            ", stopped by a bucket error" if stopped else "",
            ", stopped by the time budget" if out_of_time else "",
        )
        if self.env.context.get("cron_id"):
            # Commits this call and frees its row locks; work left makes Odoo call the job again at once.
            self.env["ir.cron"]._commit_progress(evicted + cleared + len(vanished), remaining=remaining)
        return evicted
