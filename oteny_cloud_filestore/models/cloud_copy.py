import datetime
import errno
import hashlib
import json
import logging
import os
from uuid import uuid4

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..tools.cloud_store import CloudStoreError

_logger = logging.getLogger(__name__)

STATE_FILE = ".cloud_copy_state.json"
# A file that no database row and no checklist marker wants stays in the copy this long (D23).
REMOVAL_WAIT_DAYS = 30


def _names_under(folder):
    """Every `<xx>/<sha1>` file name below `folder`; a missing folder has none."""
    names = set()
    if not os.path.isdir(folder):
        return names
    for directory in os.listdir(folder):
        sub_folder = os.path.join(folder, directory)
        if len(directory) != 2 or not os.path.isdir(sub_folder):
            continue
        for name in os.listdir(sub_folder):
            if not name.startswith(".") and os.path.isfile(os.path.join(sub_folder, name)):
                names.add(f"{directory}/{name}")
    return names


def _file_sha1(path):
    """The SHA-1 of a file, read in chunks."""
    digest = hashlib.sha1()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_chunks(path):
    with open(path, "rb") as file:
        yield from iter(lambda: file.read(1024 * 1024), b"")


def _is_date(text):
    try:
        datetime.date.fromisoformat(text)
    except (TypeError, ValueError):
        return False
    return True


def _write_atomic(path, data):
    """Replace `path` by `data` complete or not at all (temporary file, then os.replace)."""
    temp_path = f"{path}.{uuid4().hex}.tmp"
    try:
        with open(temp_path, "wb") as temp_file:
            temp_file.write(data)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    @api.model
    def _cloud_filestore_copy_into(self, folder, checklist_folder=None, seed_folder=None):
        """Make `folder` a full copy of the bucket files that the database wants.

        The disaster-recovery standby has only the read key of the bucket, so
        the names come from the restored database, plus the checklist markers:
        production keeps a removed object for the hold time, and the copy must
        keep it too. The job never writes into the local filestore of the
        database. Files that nobody wants any more leave the copy only after
        they stayed unwanted for REMOVAL_WAIT_DAYS nights.

        A name that only a checklist marker wants and that is not in the bucket
        is a deleted file that never reached the bucket (a local-only file such
        as a module image or an asset bundle). It counts as `deleted`, without a
        warning; only a name that a database row uses counts as `missing`.

        `seed_folder` is a folder that may hold the same files already (the
        standby's mirror of the Odoo.sh backup filestore). A wanted name whose
        seed file has the right SHA-1 is hard-linked from it instead of
        downloaded, so no file is stored twice. A link across two file systems
        or mounts (EXDEV) falls back to a copy of the seed file.
        """
        store = self._cloud_filestore_store() if self._cloud_filestore_mode() != "off" else None
        if store is None:
            raise UserError("The cloud filestore is off or has no bucket: there is nothing to copy.")
        if checklist_folder is None:
            checklist_folder = self._full_path("checklist")
        if not os.path.isdir(checklist_folder):
            _logger.warning("Cloud filestore: checklist folder %s does not exist", checklist_folder)
        self.flush_model(["store_fname", "cloud_local_only"])
        self.env.cr.execute(
            "SELECT DISTINCT store_fname FROM ir_attachment"
            " WHERE store_fname IS NOT NULL AND cloud_local_only IS NOT TRUE"
        )
        used = {row[0] for row in self.env.cr.fetchall()}
        wanted = used | _names_under(checklist_folder)
        os.makedirs(folder, exist_ok=True)
        written = present = missing = deleted = seeded = 0
        stopped = exdev_logged = False
        for name in sorted(wanted):
            path = os.path.join(folder, name)
            if os.path.exists(path):
                present += 1
                continue
            seed_path = os.path.join(seed_folder, name) if seed_folder else None
            try:
                if seed_path and os.path.isfile(seed_path) and _file_sha1(seed_path) == name.split("/")[-1]:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    try:
                        os.link(seed_path, path)
                    except FileExistsError:
                        present += 1
                        continue
                    except OSError as error:
                        if error.errno != errno.EXDEV:
                            raise
                        if not exdev_logged:
                            exdev_logged = True
                            _logger.warning(
                                "Cloud filestore: the seed folder %s is on another file system or mount, "
                                "the copy job copies its files instead of linking them",
                                seed_folder,
                            )
                        if not self._cloud_filestore_write_new_file(path, _file_chunks(seed_path)):
                            present += 1
                            continue
                    seeded += 1
                    continue
            except OSError as error:
                _logger.warning(
                    "Cloud filestore: copy job stopped at %s, seed file not linked: %s", name, error
                )
                stopped = True
                break
            try:
                data = store.get(name)
            except CloudStoreError as error:
                _logger.warning("Cloud filestore: copy job stopped at %s: %s", name, error)
                stopped = True
                break
            if data is None:
                if name not in used:
                    deleted += 1
                    continue
                missing += 1
                _logger.warning("Cloud filestore: %s is not in the bucket, skipped", name)
                continue
            if hashlib.sha1(data).hexdigest() != name.split("/")[-1]:
                # Names are content hashes: other bytes mean a damaged object.
                missing += 1
                _logger.warning("Cloud filestore: bytes of %s do not match its name, skipped", name)
                continue
            try:
                if self._cloud_filestore_write_new_file(path, data):
                    written += 1
                else:
                    present += 1
            except OSError as error:
                _logger.warning("Cloud filestore: copy job stopped at %s, file not written: %s", name, error)
                stopped = True
                break
        removed = 0
        if wanted:
            try:
                removed = self._cloud_filestore_copy_remove_unwanted(folder, wanted)
            except OSError as error:
                # The state file is replaced atomically, so the previous one is still there.
                _logger.warning("Cloud filestore: copy job could not write its state file: %s", error)
                stopped = True
        else:
            # An empty or failed restore must never mark the whole copy for removal.
            _logger.warning("Cloud filestore: the database wants no files, the copy removes nothing")
        _logger.info(
            "Cloud filestore copy job: %d written, %d linked from the seed folder, %d present, "
            "%d missing in the bucket, %d deleted and never in the bucket, %d removed%s",
            written,
            seeded,
            present,
            missing,
            deleted,
            removed,
            ", stopped by an error" if stopped else "",
        )
        return {
            "written": written,
            "seeded": seeded,
            "present": present,
            "missing": missing,
            "deleted": deleted,
            "removed": removed,
            "stopped": stopped,
        }

    @api.model
    def _cloud_filestore_copy_remove_unwanted(self, folder, wanted):
        """Date the unwanted files of the copy and delete those unwanted for 30 nights; return the count."""
        state_path = os.path.join(folder, STATE_FILE)
        try:
            with open(state_path) as state_file:
                state = json.load(state_file)
        except (OSError, ValueError):
            state = {}
        if not isinstance(state, dict) or not all(_is_date(day) for day in state.values()):
            # Unreadable content only delays a removal: start the waiting again.
            state = {}
        today = fields.Date.today()
        removed = 0
        # A name that is wanted again starts its wait over.
        new_state = {name: day for name, day in state.items() if name not in wanted}
        for name in sorted(_names_under(folder) - wanted):
            first_night = new_state.setdefault(name, today.isoformat())
            if (today - datetime.date.fromisoformat(first_night)).days >= REMOVAL_WAIT_DAYS:
                try:
                    os.unlink(os.path.join(folder, name))
                except OSError as error:
                    _logger.warning("Cloud filestore: %s not removed from the copy: %s", name, error)
                    continue
                del new_state[name]
                removed += 1
        # A state entry whose file is gone has nothing left to wait for.
        new_state = {
            name: day for name, day in new_state.items() if os.path.exists(os.path.join(folder, name))
        }
        _write_atomic(state_path, json.dumps(new_state, sort_keys=True).encode())
        return removed
