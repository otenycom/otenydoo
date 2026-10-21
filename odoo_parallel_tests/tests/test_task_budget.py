"""The worker count fits the task limits (cgroup pids.max, RLIMIT_NPROC)."""

import os
import tempfile

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from .. import tasks


@tagged("odoo_parallel_tests", "post_install", "-at_install", "test_task_budget")
class TestTaskBudget(TransactionCase):
    def _write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def test_workers_are_capped_to_the_free_tasks(self):
        per = tasks.TASKS_PER_WORKER
        self.assertEqual(tasks.cap_workers_to_tasks(16, 4 * per + 10), (4, True))
        self.assertEqual(tasks.cap_workers_to_tasks(16, 100 * per), (16, False))
        # Less room than one worker needs still runs one worker.
        self.assertEqual(tasks.cap_workers_to_tasks(16, 10), (1, True))
        # No limit known: leave the count alone.
        self.assertEqual(tasks.cap_workers_to_tasks(16, None), (16, False))

    def test_cgroup_v2_takes_the_tightest_ancestor(self):
        with tempfile.TemporaryDirectory() as root:
            proc = os.path.join(root, "proc_cgroup")
            self._write(proc, "0::/build/odoo\n")
            fs = os.path.join(root, "cg")
            self._write(os.path.join(fs, "build/odoo/pids.max"), "max\n")
            self._write(os.path.join(fs, "build/odoo/pids.current"), "300\n")
            self._write(os.path.join(fs, "build/pids.max"), "1024\n")
            self._write(os.path.join(fs, "build/pids.current"), "400\n")
            self.assertEqual(tasks.cgroup_task_headroom(fs, proc), 624)

    def test_cgroup_v1_reads_the_pids_controller(self):
        with tempfile.TemporaryDirectory() as root:
            proc = os.path.join(root, "proc_cgroup")
            self._write(proc, "5:cpu,cpuacct:/x\n3:pids:/docker/abc\n")
            fs = os.path.join(root, "cg")
            self._write(os.path.join(fs, "pids/docker/abc/pids.max"), "512\n")
            self._write(os.path.join(fs, "pids/docker/abc/pids.current"), "12\n")
            self.assertEqual(tasks.cgroup_task_headroom(fs, proc), 500)

    def test_no_cgroup_means_no_limit(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(tasks.cgroup_task_headroom(root, os.path.join(root, "missing")))
