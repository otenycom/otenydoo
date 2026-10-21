"""
Fit the worker count to the task limits of the machine.

Linux counts every process and every thread as a task. Two limits stop a new
task: the pids controller of the cgroup the run lives in (pids.max, the whole
container on a hosted build) and RLIMIT_NPROC (all tasks of the user). When
either is reached, Python raises "RuntimeError: can't start new thread" and
Chrome crashes at startup, on whichever worker asks next.

A worker that runs a browser test holds its Odoo process and a headless
Chrome: measured on 2026-10-21 (macOS, one browser test class) at 175 tasks
for the worker and its Chrome processes together. The default worker count
follows the CPU count of the host, which knows nothing of these limits, so a
host with many cores and a container with a small pids.max start more Chromes
than the container may hold.
"""

import logging
import os
import resource

_logger = logging.getLogger(__name__)

# Tasks one worker may hold at its peak, with a headless Chrome open
# (measured 175 on macOS, rounded up for Linux's zygote processes).
TASKS_PER_WORKER = 250

_CGROUP_ROOT = "/sys/fs/cgroup"


def _read_int(path):
    """The integer in ``path``, or None when it is absent or reads "max"."""
    try:
        with open(path) as f:
            value = f.read().strip()
    except OSError:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _cgroup_dirs(cgroup_root=_CGROUP_ROOT, proc_cgroup="/proc/self/cgroup"):
    """The pids-controller directories of this process, own cgroup first.

    cgroup v2 names the path on the "0::" line, cgroup v1 on the line of the
    "pids" controller. Each ancestor up to the root can hold its own limit.
    """
    try:
        with open(proc_cgroup) as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    for line in lines:
        hierarchy, controllers, path = line.split(":", 2)
        if hierarchy == "0" and controllers == "":
            base = cgroup_root
        elif "pids" in controllers.split(","):
            base = os.path.join(cgroup_root, "pids")
        else:
            continue
        dirs = []
        rel = path.strip("/")
        while True:
            dirs.append(os.path.join(base, rel) if rel else base)
            if not rel:
                return dirs
            rel = os.path.dirname(rel)
    return []


def cgroup_task_headroom(cgroup_root=_CGROUP_ROOT, proc_cgroup="/proc/self/cgroup"):
    """Tasks this cgroup may still start (smallest over its ancestors), or None."""
    headroom = None
    for directory in _cgroup_dirs(cgroup_root, proc_cgroup):
        limit = _read_int(os.path.join(directory, "pids.max"))
        current = _read_int(os.path.join(directory, "pids.current"))
        if limit is None or current is None:
            continue
        free = max(0, limit - current)
        headroom = free if headroom is None else min(headroom, free)
    return headroom


def nproc_task_headroom():
    """Tasks this user may still start under RLIMIT_NPROC, or None when unlimited."""
    soft, _hard = resource.getrlimit(resource.RLIMIT_NPROC)
    if soft == resource.RLIM_INFINITY:
        return None
    import psutil

    uid = os.getuid()
    used = 0
    for proc in psutil.process_iter(["uids", "num_threads"]):
        info = proc.info
        if info["uids"] and info["uids"].real == uid:
            used += info["num_threads"] or 0
    return max(0, soft - used)


def cap_workers_to_tasks(requested, headroom):
    """Return (workers, capped): at most one worker per TASKS_PER_WORKER free tasks.

    An unknown headroom leaves the count alone; at least one worker runs.
    """
    if headroom is None:
        return requested, False
    fit = max(1, headroom // TASKS_PER_WORKER)
    if fit >= requested:
        return requested, False
    return fit, True


def fit_workers(requested):
    """Cap ``requested`` to the task limits and log the inputs of the choice."""
    cgroup = cgroup_task_headroom()
    nproc = nproc_task_headroom()
    known = [h for h in (cgroup, nproc) if h is not None]
    headroom = min(known) if known else None
    workers, _capped = cap_workers_to_tasks(requested, headroom)
    # INFO, not WARNING: a hosted build logs a warning to ir_logging and
    # turns red, and a capped run is a handled case.
    _logger.info(
        "Task limits: cgroup pids headroom %s, RLIMIT_NPROC headroom %s, "
        "%d CPUs; %d workers requested, %d run (about %d tasks each)",
        cgroup if cgroup is not None else "unlimited",
        nproc if nproc is not None else "unlimited",
        os.cpu_count() or 0,
        requested,
        workers,
        TASKS_PER_WORKER,
    )
    return workers
