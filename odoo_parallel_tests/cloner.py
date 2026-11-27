"""
Database cloning utilities for parallel test execution.

Uses PostgreSQL's createdb -T (template clone) which is fast because
it does a file-level copy. Requires no active connections on the
template database during cloning.

Clone reuse: after a parallel run, clones are kept as a POOL. On the next
run four fingerprints detect whether the base DB has changed:
1. Schema fingerprint — md5 of all public table columns + types
2. XML-ID fingerprint — md5 of ir_model_data rows (module.name=model:res_id)
3. Module-version fingerprint — md5 of ir_module_module name=latest_version
4. Asset fingerprint — md5 of /web/assets/% rows in ir_attachment
If all four match, every clone in the pool that still exists is valid: a
run needing N clones reuses the first N and creates only the ones that are
missing. A run with fewer classes (fewer workers) never rebuilds the pool,
and a run with more workers adds to it. The XML-ID fingerprint catches the
common case where noupdate=1 data records are added without schema changes.
The module-version fingerprint catches a value-only migration: the schema
and XML-ID set stay the same, but latest_version moves, so clones refresh.
The asset fingerprint ensures reused clones match the pregenerated web asset
bundles on the base DB, so workers can safely skip asset pregeneration.

Copy strategy: on PostgreSQL 15+ a clone is made with
``CREATE DATABASE ... STRATEGY = FILE_COPY``. The default WAL_LOG strategy
writes every block of the template through the WAL, and 19 concurrent
copies of a 115 MB database took 60 s that way; FILE_COPY copies the files
and takes about 1.5 s for six concurrent copies. Older servers keep
``createdb -T``.

Filestore: a clone's ir_attachment rows point at files under the BASE DB's
filestore (asset bundles, install-time fixture PDFs, ...), but Odoo resolves
a database's filestore by database name, so a fresh clone used to start with
an empty filestore. A browser test (HttpCase.start_tour) then never booted
its web client, and any test rendering an installed fixture raised
FileNotFoundError. Every fresh clone now gets a replica of the base
filestore, hardlinked (no bytes copied; a copy is the fallback), and the
replica is removed with the clone. Hardlinks are safe against Odoo's
end-of-run filestore GC: unlinking a worker's link never touches the base.
"""

from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
import shutil
import subprocess
import threading

_logger = logging.getLogger(__name__)

CLONE_STATE_PATH = "/tmp/odoo_parallel_test_clones.json"

# Empirical sweet spot for PostgreSQL STRATEGY = FILE_COPY: 3 concurrent copies
# minimizes disk I/O contention while letting worker 0 start ~1.9s into cloning
# instead of waiting ~11s when all 18 copies compete simultaneously.
DEFAULT_CLONE_CONCURRENCY = 3


def _get_clone_concurrency():
    """Maximum number of concurrent CREATE DATABASE clone operations."""
    raw = os.environ.get("ODOO_TEST_CLONE_CONCURRENCY")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return DEFAULT_CLONE_CONCURRENCY


# ---------------------------------------------------------------------------
# PostgreSQL helpers
# ---------------------------------------------------------------------------


def _get_pg_env():
    """Build environment dict for PostgreSQL CLI commands (password via PGPASSWORD)."""
    from odoo import tools

    env = os.environ.copy()
    if tools.config["db_password"]:
        env["PGPASSWORD"] = tools.config["db_password"]
    return env


def _get_pg_args():
    """Build common PostgreSQL CLI arguments for user/host/port."""
    from odoo import tools

    args = []
    if tools.config["db_user"]:
        args.extend(["-U", tools.config["db_user"]])
    if tools.config["db_host"]:
        args.extend(["-h", tools.config["db_host"]])
    if tools.config.get("db_port"):
        args.extend(["-p", str(tools.config["db_port"])])
    return args


_server_version_num_cache = None


def _server_version_num():
    """PostgreSQL ``server_version_num`` as an int (0 when it cannot be read)."""
    global _server_version_num_cache
    if _server_version_num_cache is None:
        env = _get_pg_env()
        args = _get_pg_args()
        cmd = ["psql", "-d", "postgres"] + args + ["-t", "-A", "-c", "SHOW server_version_num"]
        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        try:
            _server_version_num_cache = int(result.stdout.strip()) if result.returncode == 0 else 0
        except ValueError:
            _server_version_num_cache = 0
    return _server_version_num_cache


def _create_database_cmd(base_db, clone_name):
    """The command that clones ``base_db`` into ``clone_name``.

    PostgreSQL 15+ gets ``STRATEGY = FILE_COPY``: it copies the template's
    files instead of logging every block through the WAL, which is what made
    a batch of concurrent clones slow. Older servers use ``createdb -T``.
    """
    pg_args = _get_pg_args()
    if _server_version_num() >= 150000:
        sql = f'CREATE DATABASE "{clone_name}" TEMPLATE "{base_db}" STRATEGY = FILE_COPY;'
        return ["psql", "-d", "postgres"] + pg_args + ["-q", "-v", "ON_ERROR_STOP=1", "-c", sql]
    return ["createdb"] + pg_args + ["-T", base_db, clone_name]


def _terminate_connections(db_name):
    """Terminate all PostgreSQL connections to a database via psql."""
    env = _get_pg_env()
    args = _get_pg_args()
    sql = (
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{db_name}' AND pid <> pg_backend_pid();"
    )
    cmd = ["psql", "-d", "postgres"] + args + ["-c", sql]
    subprocess.run(cmd, env=env, capture_output=True)


def _can_createdb():
    """
    Pre-flight check: verify that createdb will work by testing the
    pg_catalog.set_config permission that createdb uses internally.
    On managed PostgreSQL hosts (e.g. Odoo.sh) this permission is
    sometimes revoked, causing createdb to fail with zombie processes.
    """
    env = _get_pg_env()
    args = _get_pg_args()
    sql = "SELECT pg_catalog.set_config('search_path', '', false);"
    cmd = ["psql", "-d", "postgres"] + args + ["-t", "-A", "-c", sql]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        _logger.info(
            "Parallel cloning unavailable: pg_catalog.set_config permission denied"
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Schema fingerprint & clone state persistence
# ---------------------------------------------------------------------------


def _get_db_fingerprint(db_name):
    """
    Compute an md5 hash of the database's public schema (all table columns
    and their types). Changes whenever -u adds, removes, or changes any
    column — even without a manifest version bump.
    """
    env = _get_pg_env()
    args = _get_pg_args()
    sql = (
        "SELECT md5(string_agg("
        "schemaname || '.' || tablename || ':' || attname || '=' || typname, "
        "',' ORDER BY schemaname, tablename, attname)) "
        "FROM ("
        "  SELECT s.nspname AS schemaname, c.relname AS tablename, "
        "         a.attname, t.typname "
        "  FROM pg_attribute a "
        "  JOIN pg_class c ON a.attrelid = c.oid "
        "  JOIN pg_namespace s ON c.relnamespace = s.oid "
        "  JOIN pg_type t ON a.atttypid = t.oid "
        "  WHERE s.nspname = 'public' AND c.relkind = 'r' "
        "    AND a.attnum > 0 AND NOT a.attisdropped"
        ") sub"
    )
    cmd = ["psql", "-d", db_name] + args + ["-t", "-A", "-c", sql]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def _get_xmlid_fingerprint(db_name):
    """
    Compute an md5 hash of ir_model_data rows (XML IDs). Detects when
    -u or -i adds/removes/changes data records without altering the schema.

    This catches the common case where noupdate=1 records are added to XML
    data files: the schema fingerprint stays the same (no new columns), but
    the clones are stale because they lack the new data records.
    """
    env = _get_pg_env()
    args = _get_pg_args()
    sql = (
        "SELECT md5(string_agg("
        "module || '.' || name || '=' || model || ':' || res_id::text, "
        "',' ORDER BY module, name)) "
        "FROM ir_model_data"
    )
    cmd = ["psql", "-d", db_name] + args + ["-t", "-A", "-c", sql]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def _get_module_version_fingerprint(db_name):
    """
    Compute an md5 hash of installed module versions.

    A migration that only rewrites a field value on an existing record
    changes neither the schema nor the XML-ID set. It does bump
    ir_module_module.latest_version after -u, so this hash moves and
    stale worker clones are discarded.
    """
    env = _get_pg_env()
    args = _get_pg_args()
    sql = (
        "SELECT md5(string_agg("
        "name || '=' || COALESCE(latest_version, ''), "
        "',' ORDER BY name)) "
        "FROM ir_module_module "
        "WHERE state = 'installed'"
    )
    cmd = ["psql", "-d", db_name] + args + ["-t", "-A", "-c", sql]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def _get_asset_fingerprint(db_name):
    """
    Compute an md5 hash of pregenerated web asset bundles in ir_attachment.

    Parallel workers skip _pregenerate_assets_bundles() because master already
    pregenerated the bundles on base_db before cloning. Including this hash in
    the reuse key ensures reused clones are rebuilt whenever asset bundles are
    generated for the first time or regenerated after JS/SCSS changes.
    """
    env = _get_pg_env()
    args = _get_pg_args()
    sql = (
        "SELECT COALESCE(md5(string_agg("
        "url || '=' || COALESCE(checksum, '') || ':' || COALESCE(store_fname, ''), "
        "',' ORDER BY url, id)), 'empty') "
        "FROM ir_attachment "
        "WHERE url LIKE '/web/assets/%'"
    )
    cmd = ["psql", "-d", db_name] + args + ["-t", "-A", "-c", sql]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def _reuse_key_matches(
    state,
    *,
    base_db,
    fingerprint,
    xmlid_fingerprint,
    version_fingerprint,
    asset_fingerprint,
):
    """True when the saved pool was cloned from this base at these fingerprints.

    The worker count is deliberately not part of the key: the pool is reused
    whatever the count, and only missing clones are created."""
    if (
        not fingerprint
        or not xmlid_fingerprint
        or not version_fingerprint
        or not asset_fingerprint
    ):
        return False
    return (
        state.get("base_db") == base_db
        and state.get("fingerprint") == fingerprint
        and state.get("xmlid_fingerprint") == xmlid_fingerprint
        and state.get("version_fingerprint") == version_fingerprint
        and state.get("asset_fingerprint") == asset_fingerprint
    )


def _reuse_miss_reasons(
    state, fingerprint, xmlid_fingerprint, version_fingerprint, asset_fingerprint
):
    """Human-readable reasons the saved pool does not match the current key."""
    reasons = []
    if state.get("fingerprint") != fingerprint:
        reasons.append("schema changed")
    if state.get("xmlid_fingerprint") != xmlid_fingerprint:
        reasons.append("XML IDs changed")
    if state.get("version_fingerprint") != version_fingerprint:
        reasons.append("module versions changed")
    if state.get("asset_fingerprint") != asset_fingerprint:
        reasons.append("assets changed")
    return reasons


def _load_clone_state():
    """Load the clone state file. Returns dict or empty dict on any error."""
    if not os.path.exists(CLONE_STATE_PATH):
        return {}
    try:
        with open(CLONE_STATE_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_clone_state(
    base_db,
    fingerprint,
    xmlid_fingerprint,
    version_fingerprint,
    clone_names,
    asset_fingerprint=None,
):
    """Persist the clone state so the next run can attempt reuse."""
    try:
        with open(CLONE_STATE_PATH, "w") as f:
            json.dump(
                {
                    "base_db": base_db,
                    "fingerprint": fingerprint,
                    "xmlid_fingerprint": xmlid_fingerprint,
                    "version_fingerprint": version_fingerprint,
                    "asset_fingerprint": asset_fingerprint,
                    "clone_names": clone_names,
                    "worker_count": len(clone_names),
                },
                f,
            )
    except OSError as exc:
        _logger.warning("Could not save clone state: %s", exc)


def _existing_databases():
    """The names of all databases on the server (empty set when unreadable)."""
    env = _get_pg_env()
    args = _get_pg_args()
    cmd = ["psql", "-d", "postgres"] + args + ["-t", "-A", "-c",
           "SELECT datname FROM pg_database"]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        return set()
    return set(result.stdout.strip().splitlines())


# ---------------------------------------------------------------------------
# Filestore replication
# ---------------------------------------------------------------------------

# Odoo's filestore GC keeps its to-delete markers here; a clone starts clean.
FILESTORE_SKIP_DIRS = ("checklist",)
# Written into a replica once it is complete, so a reused clone from before
# the replication existed (an empty or partial filestore) is topped up.
FILESTORE_MARKER = ".hh-filestore-replica"


def _filestore_dir(db_name):
    """Where Odoo keeps this database's attachments (data_dir/filestore/<db>)."""
    from odoo import tools

    return tools.config.filestore(db_name)


def replicate_filestore(base_dir, clone_dir, replace=True):
    """Give a clone the base database's filestore.

    Every file is hardlinked (metadata only, no bytes copied); a file that
    cannot be linked (another volume, a permission quirk) is copied instead.
    With ``replace`` (a fresh clone) a stale ``clone_dir`` is replaced.
    Without it (a reused clone that lacks the marker) the base is merged in
    and the clone's own files are kept. A missing ``base_dir`` is a no-op.

    Returns ``(files, linked, copied)``; ``files`` counts the base files
    handled, including those the merge left as they were.
    """
    if not os.path.isdir(base_dir):
        return 0, 0, 0
    if replace and os.path.lexists(clone_dir):
        shutil.rmtree(clone_dir, ignore_errors=True)
    files = linked = copied = 0
    for root, dirs, names in os.walk(base_dir):
        rel = os.path.relpath(root, base_dir)
        if rel == ".":
            rel = ""
            dirs[:] = [d for d in dirs if d not in FILESTORE_SKIP_DIRS]
            names = [n for n in names if n != FILESTORE_MARKER]
        target_root = os.path.join(clone_dir, rel) if rel else clone_dir
        os.makedirs(target_root, exist_ok=True)
        for name in names:
            src = os.path.join(root, name)
            dst = os.path.join(target_root, name)
            files += 1
            if os.path.lexists(dst):
                continue
            try:
                os.link(src, dst)
                linked += 1
            except OSError:
                shutil.copy2(src, dst)
                copied += 1
    with open(os.path.join(clone_dir, FILESTORE_MARKER), "w") as marker:
        marker.write(base_dir + "\n")
    return files, linked, copied


def has_filestore_replica(clone_dir):
    """True once ``replicate_filestore`` completed for this clone."""
    return os.path.isfile(os.path.join(clone_dir, FILESTORE_MARKER))


def remove_filestore(clone_dir):
    """Remove a clone's filestore replica; a missing directory is fine."""
    if os.path.lexists(clone_dir):
        shutil.rmtree(clone_dir, ignore_errors=True)


def _replicate_filestore_for(base_db, clone_name, replace=True):
    files, linked, copied = replicate_filestore(
        _filestore_dir(base_db), _filestore_dir(clone_name), replace=replace
    )
    _logger.info(
        "Filestore for %s: %d base files (%d hardlinked, %d copied)",
        clone_name, files, linked, copied,
    )


# ---------------------------------------------------------------------------
# Clone / reuse
# ---------------------------------------------------------------------------


def clone_databases(base_db, count, on_ready=None):
    """
    Provide ``count`` worker databases from the clone pool.

    When the base DB's schema, XML IDs, module versions, and pregenerated
    assets are unchanged, every pool clone that still exists is reused and only
    the missing ones are created (a run needing fewer clones than the pool
    holds creates nothing). When the base changed, the whole pool is dropped
    and rebuilt.

    When ``on_ready`` is provided, it is called as ``on_ready(worker_index, clone_name)``
    as soon as each individual clone (database + filestore replica) is ready,
    allowing workers to start before the full batch finishes cloning.

    Returns the list of clone db names to use, in worker order.
    Raises RuntimeError if cloning is not possible (e.g. permission issues).
    """
    from . import config

    prefix = config.get_clone_prefix().replace("{db}", base_db)
    needed = [f"{prefix}{i}" for i in range(count)]
    index_by_name = {name: i for i, name in enumerate(needed)}

    # Pre-flight: verify we have the permissions that createdb needs.
    # Avoids spawning N doomed processes that become zombies on failure.
    if not _can_createdb():
        raise RuntimeError(
            "Database cloning unavailable: insufficient PostgreSQL permissions"
        )

    fingerprint = xmlid_fingerprint = version_fingerprint = asset_fingerprint = None
    stale = []
    if config.reuse_clones():
        fingerprint = _get_db_fingerprint(base_db)
        xmlid_fingerprint = _get_xmlid_fingerprint(base_db)
        version_fingerprint = _get_module_version_fingerprint(base_db)
        asset_fingerprint = _get_asset_fingerprint(base_db)
        state = _load_clone_state()
        pool = [c for c in state.get("clone_names", []) if c.startswith(prefix)]
        if _reuse_key_matches(
            state,
            base_db=base_db,
            fingerprint=fingerprint,
            xmlid_fingerprint=xmlid_fingerprint,
            version_fingerprint=version_fingerprint,
            asset_fingerprint=asset_fingerprint,
        ):
            existing = _existing_databases()
            valid = [c for c in pool if c in existing]
            reuse = [c for c in needed if c in valid]
            to_create = [c for c in needed if c not in valid]
            _logger.info(
                "Clone pool: reusing %d of %d valid clone(s), creating %d "
                "(schema, XML IDs, module versions, and assets unchanged)",
                len(reuse), len(valid), len(to_create),
            )
            # A reused clone keeps its own filestore between runs. A clone
            # from before replication existed, or a replica removed by hand,
            # is topped up from the base without touching the clone's own files.
            for clone_name in reuse:
                if not has_filestore_replica(_filestore_dir(clone_name)):
                    _replicate_filestore_for(base_db, clone_name, replace=False)
                if on_ready is not None:
                    on_ready(index_by_name[clone_name], clone_name)
            if to_create:
                if on_ready is not None:
                    _fresh_clone(
                        base_db,
                        to_create,
                        on_ready=lambda name: on_ready(index_by_name[name], name),
                    )
                else:
                    _fresh_clone(base_db, to_create)
            _save_clone_state(
                base_db,
                fingerprint,
                xmlid_fingerprint,
                version_fingerprint,
                sorted(set(valid) | set(needed)),
                asset_fingerprint=asset_fingerprint,
            )
            return needed
        if state:
            reasons = _reuse_miss_reasons(
                state, fingerprint, xmlid_fingerprint, version_fingerprint, asset_fingerprint
            )
            _logger.info(
                "Clone cache miss — %s",
                ", ".join(reasons) if reasons else "creating fresh clones",
            )
            stale = [c for c in pool if c not in needed]
        else:
            _logger.info("Clone cache miss — no prior state")

    # The base changed (or reuse is off): the pool is stale. Drop the part of
    # it this run will not recreate, then clone fresh.
    if stale:
        drop_databases(stale)
    if on_ready is not None:
        _fresh_clone(
            base_db,
            needed,
            on_ready=lambda name: on_ready(index_by_name[name], name),
        )
    else:
        _fresh_clone(base_db, needed)

    # Save state for future reuse
    if config.reuse_clones():
        if not fingerprint:
            fingerprint = _get_db_fingerprint(base_db)
        if not xmlid_fingerprint:
            xmlid_fingerprint = _get_xmlid_fingerprint(base_db)
        if not version_fingerprint:
            version_fingerprint = _get_module_version_fingerprint(base_db)
        if not asset_fingerprint:
            asset_fingerprint = _get_asset_fingerprint(base_db)
        if fingerprint and xmlid_fingerprint and version_fingerprint and asset_fingerprint:
            _save_clone_state(
                base_db,
                fingerprint,
                xmlid_fingerprint,
                version_fingerprint,
                needed,
                asset_fingerprint=asset_fingerprint,
            )

    return needed


def _fresh_clone(base_db, clone_names, on_ready=None):
    """
    Drop any existing clones and create fresh ones from the base database.

    Closes Odoo's connection pool first so the template copy can acquire
    exclusive access to the template. Runs clones with bounded concurrency
    (see ``_get_clone_concurrency``) and replicates each clone's filestore
    immediately so ``on_ready(clone_name)`` can start workers in a pipeline.
    """
    import odoo.sql_db

    if not clone_names:
        return

    odoo.sql_db.close_db(base_db)
    _terminate_connections(base_db)

    env = _get_pg_env()
    pg_args = _get_pg_args()

    # Phase 1: drop only clones that actually exist on the server (avoids
    # spawning N no-op dropdb processes after a clean deploy-tool reset).
    existing = _existing_databases()
    to_drop = [c for c in clone_names if c in existing] if existing else list(clone_names)
    drop_procs = []
    for clone_name in to_drop:
        proc = subprocess.Popen(
            ["dropdb", "--if-exists"] + pg_args + [clone_name],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        drop_procs.append(proc)
    for proc in drop_procs:
        proc.wait()

    # Phase 2: clone with bounded concurrency, hardlink each clone's filestore
    # immediately, and notify on_ready so workers boot while cloning continues.
    concurrency = min(len(clone_names), _get_clone_concurrency())
    cancel_event = threading.Event()
    state_lock = threading.Lock()
    created = []
    first_error = None
    callback_error = None

    def _clone_one(clone_name):
        nonlocal first_error, callback_error
        if cancel_event.is_set():
            return
        proc = subprocess.Popen(
            _create_database_cmd(base_db, clone_name),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode().strip()
            _logger.info("Cannot clone %s -> %s: %s", base_db, clone_name, err)
            cancel_event.set()
            with state_lock:
                if first_error is None:
                    first_error = err
            return

        with state_lock:
            created.append(clone_name)
        _logger.info("Cloned database: %s", clone_name)
        _replicate_filestore_for(base_db, clone_name)
        if on_ready is not None and not cancel_event.is_set():
            try:
                on_ready(clone_name)
            except Exception as exc:
                cancel_event.set()
                with state_lock:
                    if callback_error is None:
                        callback_error = exc

    # Wait for all submitted clone tasks to finish before handling errors so no
    # psql/createdb subprocesses linger as zombies.
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(_clone_one, name) for name in clone_names]
        for fut in futures:
            fut.result()

    if first_error is not None:
        drop_databases(created)
        raise RuntimeError(f"Database cloning failed: {first_error}")
    if callback_error is not None:
        raise callback_error


def drop_databases(clone_names):
    """Drop cloned worker databases concurrently, terminating connections first."""
    env = _get_pg_env()
    pg_args = _get_pg_args()

    # Terminate connections to all clones in parallel
    term_procs = []
    for name in clone_names:
        sql = (
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{name}' AND pid <> pg_backend_pid();"
        )
        cmd = ["psql", "-d", "postgres"] + pg_args + ["-c", sql]
        proc = subprocess.Popen(
            cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        term_procs.append(proc)
    for proc in term_procs:
        proc.wait()

    # Drop all clones in parallel (independent databases, no lock contention)
    drop_procs = []
    for name in clone_names:
        proc = subprocess.Popen(
            ["dropdb", "--if-exists"] + pg_args + [name],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        drop_procs.append((name, proc))

    for name, proc in drop_procs:
        _stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            _logger.warning(
                "Failed to drop database %s: %s", name, stderr.decode().strip()
            )
        else:
            _logger.debug("Dropped database: %s", name)
            remove_filestore(_filestore_dir(name))
