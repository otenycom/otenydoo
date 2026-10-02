"""
Monkey-patch for Odoo's test loader to enable parallel test execution.

Applied on module import (via __init__.py). Patches loader.run_suite to:
  - In master mode: detect multiple test classes, clone DB, write the work
    queue (one entry per class, longest first), spawn workers
  - In worker mode: pull one class at a time from the queue and run it,
    until the queue is empty

Only parallelizes during the post-install test phase (not at-install),
detected by checking the call stack for load_module_graph.
"""

import inspect
import json
import logging
import os
import re
import sys
import tempfile
import time

from . import config
from .discovery import class_key, get_test_class_key, group_tests_by_class, order_classes_longest_first
from .queue import claim_next, create_queue

_logger = logging.getLogger(__name__)

# The original run_suite function, saved before patching
_original_run_suite = None

# Matches Odoo's test failure log lines produced by OdooTestResult.logError:
#   TIMESTAMP PID ERROR DB LOGGER: FAIL: TestClass.test_method
#   TIMESTAMP PID ERROR DB LOGGER: ERROR: TestClass.test_method
_TEST_FAIL_RE = re.compile(r" ERROR \S+ \S+: (FAIL|ERROR): (\S+\.\S+)")


# ---------------------------------------------------------------------------
# Phase detection
# ---------------------------------------------------------------------------


def _is_post_install_phase():
    """
    Determine whether run_suite is being called from the post-install test
    phase (directly from preload_registries) vs the at-install phase (from
    inside load_module_graph during module loading).

    At-install call stack:
      preload_registries → Registry.new → load_modules → load_module_graph
        → loader.run_suite

    Post-install call stack:
      preload_registries → loader.run_suite

    We walk the call stack looking for load_module_graph. If found, we are
    in at-install and must NOT parallelize (the DB is still being modified).
    """
    frame = inspect.currentframe()
    try:
        frame = frame.f_back  # _patched_run_suite
        while frame:
            if frame.f_code.co_name == "load_module_graph":
                return False
            frame = frame.f_back
        return True
    finally:
        del frame


# ---------------------------------------------------------------------------
# Decision logic
# ---------------------------------------------------------------------------


def _should_parallelize(suite):
    """Check config and suite contents to decide if we should parallelize."""
    mode = config.get_parallel_mode()
    if mode == "never":
        return False

    classes = set()
    for test in suite:
        classes.add(get_test_class_key(test))

    if mode == "always":
        return len(classes) >= 1

    # auto: parallelize when there are multiple test classes
    return len(classes) > 1


# ---------------------------------------------------------------------------
# Worker mode: filter suite to the assigned batch and run
# ---------------------------------------------------------------------------


def _worker_run(suite, queue_dir, global_report):
    """
    Called in a worker subprocess. Pulls one test class at a time from the
    shared queue, runs that class with the original run_suite, and repeats
    until nothing claimable is left. Writes the summed results (and the
    per-class timing the master feeds back into the stats file) to a JSON
    file for the master to read.
    """
    from odoo.tests.suite import OdooSuite
    from odoo.tests.result import OdooTestResult
    from .stats import extract_class_durations

    worker_id = os.environ.get("ODOO_PARALLEL_WORKER", "0")
    by_key = {class_key(cls): tests for cls, tests in group_tests_by_class(suite).items()}

    total = OdooTestResult()
    class_durations = {}
    classes_run = []
    while True:
        key = claim_next(queue_dir, worker_id, wanted=set(by_key))
        if key is None:
            break
        result = _original_run_suite(OdooSuite(by_key[key]), global_report=global_report)
        total.failures_count += result.failures_count
        total.errors_count += result.errors_count
        total.testsRun += result.testsRun
        total.skipped += result.skipped
        class_durations.update(extract_class_durations(result))
        classes_run.append(key)

    _logger.info(
        "Worker %s ran %d tests from %d classes", worker_id, total.testsRun, len(classes_run)
    )

    result_file = os.environ.get("ODOO_PARALLEL_RESULT")
    if result_file:
        with open(result_file, "w") as f:
            json.dump(
                {
                    "failures_count": total.failures_count,
                    "errors_count": total.errors_count,
                    "testsRun": total.testsRun,
                    "skipped": total.skipped,
                    "class_durations": class_durations,
                    "classes_run": classes_run,
                },
                f,
            )

    return total


# ---------------------------------------------------------------------------
# Master mode: orchestrate parallel execution
# ---------------------------------------------------------------------------


def _parallel_run(suite, global_report):
    """
    Master-side parallel execution. Discovers test classes, clones the
    database for each worker, spawns worker subprocesses, waits for them,
    and aggregates results back into an OdooTestResult.
    """
    from .cloner import clone_databases, drop_databases
    from .locks import fit_workers
    from .runner import spawn_workers, wait_for_workers
    from odoo.tests.result import OdooTestResult
    from odoo import tools

    db_name = tools.config["db_name"]
    # Odoo stores db_name as a list; take the first (and typically only) entry
    if isinstance(db_name, (list, tuple)):
        db_name = db_name[0]
    worker_count = config.get_worker_count()

    # Discover test classes and distribute into batches
    class_groups = group_tests_by_class(suite)
    total_tests = suite.countTestCases()
    _logger.info(
        "Parallel test runner: %d classes, %d tests, requesting %d workers",
        len(class_groups),
        total_tests,
        worker_count,
    )

    # Prior timing stats decide the queue order (longest first); the queue
    # itself decides who runs what, so a stale estimate costs little.
    from .stats import load_stats, save_stats

    class_durations = load_stats()
    ordered = order_classes_longest_first(class_groups, class_durations)

    actual_workers = min(worker_count, len(ordered))
    if actual_workers < worker_count:
        _logger.info("Using %d workers (fewer classes than requested)", actual_workers)
    actual_workers = fit_workers(db_name, actual_workers)

    tmpdir = tempfile.mkdtemp(prefix="odoo_parallel_tests_")
    queue_dir = create_queue(tmpdir, [class_key(cls) for cls, _tests in ordered])

    # Clone databases
    t_clone = time.time()
    clone_names = clone_databases(db_name, actual_workers)
    _logger.info("Cloning took %.1fs", time.time() - t_clone)

    # Spawn workers
    t_run = time.time()
    workers = spawn_workers(clone_names, queue_dir, tmpdir)

    # Wait and collect
    worker_results = wait_for_workers(workers)
    elapsed = time.time() - t_run

    # Print worker output to master stdout so `| tee` captures everything
    for wr in worker_results:
        idx = wr["index"]
        output = wr["output"]
        if output.strip():
            sys.stdout.write(f"\n{'=' * 70}\n")
            sys.stdout.write(f"=== Parallel Worker {idx} Output ===\n")
            sys.stdout.write(f"{'=' * 70}\n")
            sys.stdout.write(output)
            sys.stdout.write("\n")
            sys.stdout.flush()

    # Aggregate results
    aggregated = OdooTestResult(global_report=global_report)
    for wr in worker_results:
        tr = wr["test_result"]
        if tr:
            aggregated.failures_count += tr["failures_count"]
            aggregated.errors_count += tr["errors_count"]
            aggregated.testsRun += tr["testsRun"]
            aggregated.skipped += tr["skipped"]
        elif wr["returncode"] != 0:
            # Worker crashed without writing results — count as error
            aggregated.errors_count += 1
            _logger.error(
                "Worker %d crashed (exit code %d) without writing results",
                wr["index"],
                wr["returncode"],
            )

    # Extract failed/errored test names from worker output for the summary.
    # OdooTestResult only keeps counts (not names), so we parse the log lines
    # that logError() emits at ERROR level.
    all_failed_tests = []
    for wr in worker_results:
        idx = wr["index"]
        for line in wr["output"].splitlines():
            m = _TEST_FAIL_RE.search(line)
            if m:
                all_failed_tests.append((m.group(1), m.group(2), idx))

    per_worker = ", ".join(
        f"W{wr['index']}:{len((wr.get('test_result') or {}).get('classes_run', []))}"
        for wr in worker_results
    )
    _logger.info("Classes per worker: %s", per_worker)
    _logger.info(
        "Parallel run complete in %.1fs: %s",
        elapsed,
        aggregated,
    )

    if all_failed_tests:
        summary_lines = [
            f"  {flavour}: {test_name} (worker {widx})" for flavour, test_name, widx in all_failed_tests
        ]
        _logger.error(
            "FAILED TESTS:\n%s",
            "\n".join(summary_lines),
        )

    # Merge per-class durations from all workers into the persistent stats
    # file so the next run can use LPT balancing
    all_durations = {}
    for wr in worker_results:
        tr = wr.get("test_result")
        if tr and "class_durations" in tr:
            all_durations.update(tr["class_durations"])
    if all_durations:
        save_stats(all_durations)

    # Keep clones for reuse on the next run (default). Stale clones are
    # cleaned up at the start of the next run inside clone_databases().
    if not config.reuse_clones():
        drop_databases(clone_names)

    return aggregated


# ---------------------------------------------------------------------------
# The patched run_suite
# ---------------------------------------------------------------------------


def _patched_run_suite(suite, global_report=None):
    """
    Drop-in replacement for odoo.tests.loader.run_suite.

    Three paths:
    1. Worker mode (ODOO_PARALLEL_QUEUE set): pull classes from the queue
    2. Post-install phase with multiple classes: parallelize
    3. Everything else: run sequentially with the original function
    """
    # Path 1: worker subprocess — pull classes from the shared queue
    queue_dir = os.environ.get("ODOO_PARALLEL_QUEUE")
    if queue_dir:
        return _worker_run(suite, queue_dir, global_report)

    # Path 2: master, post-install, conditions met — parallelize
    if _is_post_install_phase() and _should_parallelize(suite):
        try:
            return _parallel_run(suite, global_report)
        except Exception as exc:
            _logger.info("Parallel execution unavailable, running sequentially (%s)", exc)

    # Path 3: sequential (at-install, single class, or fallback)
    return _original_run_suite(suite, global_report=global_report)


# ---------------------------------------------------------------------------
# Patch application (called from __init__.py)
# ---------------------------------------------------------------------------


def _apply():
    global _original_run_suite

    from odoo.tests import loader

    _original_run_suite = loader.run_suite
    loader.run_suite = _patched_run_suite
    _logger.info(
        "Parallel test runner patch applied (mode: %s, workers: %d)",
        config.get_parallel_mode(),
        config.get_worker_count(),
    )


# Guard: only patch when running tests. Importing odoo.tests outside of test
# mode triggers an ERROR log in odoo.tests.common (Odoo 19 safeguard).
from odoo.tools import config as odoo_config

if odoo_config["test_enable"]:
    _apply()
