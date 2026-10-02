"""
Fit the worker count to the PostgreSQL lock table.

PostgreSQL keeps every lock of every connection in one shared table of about
max_locks_per_transaction x (max_connections + max_prepared_transactions)
slots. A test class runs in one transaction, so each worker holds a lock on
every table and index its tests touched until the class ends. When the table
is full, PostgreSQL raises "ERROR: out of shared memory" on whichever
connection asks next. That error says nothing about the machine's RAM, and
it fails random tests on random workers.

Measured on 2026-10-02: 18 workers on the riverflow, rivercreds, wilma and
cuneus suites peaked at 16,311 locks, about 900 per worker. A stock server
(64 x 100 = 6,400 slots) fits that only because PostgreSQL lends the lock
table spare shared memory, and a restore in one transaction beside the run
(~5,000 locks) took the rest. The run then failed with 7 failures and 77
errors. Raising max_locks_per_transaction (1024 gives 102,400 slots) is the
fix; capping the workers keeps an untuned server green meanwhile.
"""

import logging
import subprocess

_logger = logging.getLogger(__name__)

# Lock slots one worker may hold at its peak (measured ~900, rounded up).
LOCKS_PER_WORKER = 1000

_SLOTS_SQL = (
    "SELECT current_setting('max_locks_per_transaction')::int"
    " * (current_setting('max_connections')::int"
    " + current_setting('max_prepared_transactions')::int)"
)


def lock_table_slots(db_name):
    """Nominal size of the server's lock table, or None when psql fails."""
    from .cloner import _get_pg_args, _get_pg_env

    cmd = ["psql", "-d", db_name] + _get_pg_args() + ["-t", "-A", "-c", _SLOTS_SQL]
    result = subprocess.run(cmd, env=_get_pg_env(), capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def cap_workers_to_lock_table(requested, slots):
    """Return (workers, capped): at most one worker per LOCKS_PER_WORKER slots.

    An unknown table size leaves the count alone; at least one worker runs.
    """
    if not slots:
        return requested, False
    fit = max(1, slots // LOCKS_PER_WORKER)
    if fit >= requested:
        return requested, False
    return fit, True


def fit_workers(db_name, requested):
    """Cap ``requested`` to the server's lock table and warn when it bites."""
    slots = lock_table_slots(db_name)
    workers, capped = cap_workers_to_lock_table(requested, slots)
    if capped:
        _logger.warning(
            "PostgreSQL lock table has %d slots, too few for %d workers "
            "(about %d locks each): running %d workers. A full lock table raises "
            "'out of shared memory' on random tests. Fix: set "
            "max_locks_per_transaction = 1024 on the server and restart it.",
            slots,
            requested,
            LOCKS_PER_WORKER,
            workers,
        )
    return workers
