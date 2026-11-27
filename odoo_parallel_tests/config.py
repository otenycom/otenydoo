"""
Configuration for the parallel test runner, read from environment variables.

All settings can be overridden per invocation via env vars.
"""

import multiprocessing
import os


def get_worker_count():
    """
    Number of parallel test workers. Defaults to ~80% of CPU cores (14 on an
    18-core Apple Silicon machine), clamped to [2, 32].

    Each test worker drives both a Python odoo-bin process and a PostgreSQL
    backend (plus headless Chrome during HttpCase tests). With the shared work
    queue keeping every worker busy until the end, running 100% of cores
    oversubscribes the CPU and PostgreSQL shared buffers: on an 18-core M5 Max
    (6 P-cores + 12 E-cores), the 6,302-test suite ran in 91.8 s (1,236 s
    summed worker time) at 14 workers versus 105.8 s (1,825 s summed, +48%
    contention) at 18 workers, and 97.6 s at 12 workers.
    """
    cores = multiprocessing.cpu_count()
    default = min(32, max(2, round(cores * 0.8)))
    return int(os.environ.get("ODOO_TEST_WORKERS", default))


def get_parallel_mode():
    """
    When to engage parallel mode:
      auto   — parallel if more than 1 test class (default)
      always — force parallel even for single class (for debugging the runner)
      never  — disable parallel, run sequentially as normal
    """
    return os.environ.get("ODOO_TEST_PARALLEL", "auto")


def get_clone_prefix():
    """Database name prefix for worker clones. {db} is replaced with the base db name."""
    return os.environ.get("ODOO_TEST_CLONE_PREFIX", "{db}-worker-")


def reuse_clones():
    """
    Whether to keep clone databases between runs and reuse them when the
    base DB schema, XML IDs, and module versions have not changed.
    Defaults to True — saves ~9s on repeated runs. Set to false to
    always create fresh clones.
    """
    return os.environ.get("ODOO_TEST_REUSE_CLONES", "true").lower() not in ("false", "0", "no")


def keep_clones():
    """Legacy alias — reuse_clones supersedes this."""
    return reuse_clones()
