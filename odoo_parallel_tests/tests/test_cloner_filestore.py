"""A worker clone gets the base database's filestore, and loses it on drop.

Without this, a clone's ir_attachment rows point at files that only exist
under the base database's filestore: a browser test never boots its web
client (asset bundles 500), and a test that renders an install-time fixture
raises FileNotFoundError. The replica is hardlinked, so it is free and safe
against Odoo's end-of-run filestore GC on the worker.
"""

import os
import shutil
import tempfile
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from .. import cloner


def _seed(base):
    os.makedirs(os.path.join(base, "ab"))
    os.makedirs(os.path.join(base, "cd"))
    os.makedirs(os.path.join(base, "checklist", "ab"))
    with open(os.path.join(base, "ab", "abc123"), "wb") as f:
        f.write(b"bundle bytes")
    with open(os.path.join(base, "cd", "cde456"), "wb") as f:
        f.write(b"fixture pdf")
    with open(os.path.join(base, "checklist", "ab", "abc123"), "wb") as f:
        f.write(b"")


class _FakeProc:
    """Stands in for a createdb / dropdb / psql subprocess that succeeded."""

    returncode = 0

    def communicate(self):
        return b"", b""

    def wait(self):
        return 0


@tagged("odoo_parallel_tests", "post_install", "-at_install", "test_cloner_filestore")
class TestCloneFilestore(TransactionCase):
    def setUp(self):
        super().setUp()
        self.root = tempfile.mkdtemp(prefix="hh-filestore-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.base = os.path.join(self.root, "cr-test")
        self.clone = os.path.join(self.root, "cr-test-worker-0")
        _seed(self.base)

    def test_replicates_as_hardlinks_and_skips_the_gc_checklist(self):
        files, linked, copied = cloner.replicate_filestore(self.base, self.clone)
        self.assertEqual((files, linked, copied), (2, 2, 0))
        src = os.stat(os.path.join(self.base, "ab", "abc123"))
        dst = os.stat(os.path.join(self.clone, "ab", "abc123"))
        self.assertEqual(src.st_ino, dst.st_ino, "a hardlink shares the inode")
        with open(os.path.join(self.clone, "cd", "cde456"), "rb") as f:
            self.assertEqual(f.read(), b"fixture pdf")
        self.assertFalse(os.path.exists(os.path.join(self.clone, "checklist")))

    def test_merge_keeps_the_clone_own_files_and_tops_up_the_base(self):
        # A clone from before replication existed: it has its own files and
        # lacks the marker. The reuse path merges the base in.
        os.makedirs(os.path.join(self.clone, "ef"))
        with open(os.path.join(self.clone, "ef", "own789"), "wb") as f:
            f.write(b"worker-made attachment")
        self.assertFalse(cloner.has_filestore_replica(self.clone))
        files, linked, copied = cloner.replicate_filestore(self.base, self.clone, replace=False)
        self.assertEqual((files, linked, copied), (2, 2, 0))
        self.assertTrue(os.path.exists(os.path.join(self.clone, "ef", "own789")))
        self.assertTrue(os.path.exists(os.path.join(self.clone, "ab", "abc123")))
        self.assertTrue(cloner.has_filestore_replica(self.clone))
        # A second merge links nothing new.
        self.assertEqual(cloner.replicate_filestore(self.base, self.clone, replace=False), (2, 0, 0))

    def test_replaces_a_stale_clone_directory(self):
        os.makedirs(os.path.join(self.clone, "zz"))
        with open(os.path.join(self.clone, "zz", "stale"), "wb") as f:
            f.write(b"old worker output")
        cloner.replicate_filestore(self.base, self.clone)
        self.assertFalse(os.path.exists(os.path.join(self.clone, "zz")))
        self.assertTrue(os.path.exists(os.path.join(self.clone, "ab", "abc123")))

    def test_missing_base_is_a_noop(self):
        missing = os.path.join(self.root, "nope")
        self.assertEqual(cloner.replicate_filestore(missing, self.clone), (0, 0, 0))
        self.assertFalse(os.path.exists(self.clone))

    def test_falls_back_to_a_copy_when_linking_fails(self):
        with patch.object(cloner.os, "link", side_effect=OSError("EXDEV")):
            files, linked, copied = cloner.replicate_filestore(self.base, self.clone)
        self.assertEqual((files, linked, copied), (2, 0, 2))
        src = os.stat(os.path.join(self.base, "ab", "abc123"))
        dst = os.stat(os.path.join(self.clone, "ab", "abc123"))
        self.assertNotEqual(src.st_ino, dst.st_ino)
        with open(os.path.join(self.clone, "ab", "abc123"), "rb") as f:
            self.assertEqual(f.read(), b"bundle bytes")

    def test_unlinking_in_the_clone_leaves_the_base_intact(self):
        # What Odoo's end-of-run filestore GC does on a worker.
        cloner.replicate_filestore(self.base, self.clone)
        os.unlink(os.path.join(self.clone, "ab", "abc123"))
        with open(os.path.join(self.base, "ab", "abc123"), "rb") as f:
            self.assertEqual(f.read(), b"bundle bytes")

    def test_remove_filestore(self):
        cloner.replicate_filestore(self.base, self.clone)
        cloner.remove_filestore(self.clone)
        self.assertFalse(os.path.exists(self.clone))
        cloner.remove_filestore(self.clone)  # a second removal is fine

    def _filestore_dir(self, db_name):
        return os.path.join(self.root, db_name)

    def test_fresh_clone_replicates_after_createdb_and_notifies_on_ready(self):
        ready = []
        popen_cmds = []

        def fake_popen(cmd, *args, **kwargs):
            popen_cmds.append(cmd)
            return _FakeProc()

        def on_ready(clone_name):
            # Filestore replica must already be present when worker is spawned.
            self.assertTrue(
                os.path.exists(os.path.join(self._filestore_dir(clone_name), "ab", "abc123"))
            )
            ready.append(clone_name)

        with (
            patch.object(cloner, "_server_version_num", return_value=180003),
            patch.object(cloner, "_existing_databases", return_value={"postgres", "cr-test"}),
            patch.object(cloner, "_filestore_dir", side_effect=self._filestore_dir),
            patch.object(cloner, "_terminate_connections"),
            patch.object(cloner, "_get_pg_env", return_value={}),
            patch.object(cloner, "_get_pg_args", return_value=[]),
            patch.object(cloner.subprocess, "Popen", side_effect=fake_popen),
            patch("odoo.sql_db.close_db"),
        ):
            cloner._fresh_clone("cr-test", ["cr-test-worker-0"], on_ready=on_ready)
        self.assertTrue(os.path.exists(os.path.join(self.clone, "ab", "abc123")))
        self.assertEqual(ready, ["cr-test-worker-0"])
        # Non-existent clone skips dropdb completely.
        self.assertFalse(any(cmd[0] == "dropdb" for cmd in popen_cmds))

    def test_drop_databases_removes_the_replica(self):
        cloner.replicate_filestore(self.base, self.clone)
        with (
            patch.object(cloner, "_filestore_dir", side_effect=self._filestore_dir),
            patch.object(cloner, "_get_pg_env", return_value={}),
            patch.object(cloner, "_get_pg_args", return_value=[]),
            patch.object(cloner.subprocess, "Popen", return_value=_FakeProc()),
        ):
            cloner.drop_databases(["cr-test-worker-0"])
        self.assertFalse(os.path.exists(self.clone))

    def test_reused_clone_without_the_marker_is_topped_up(self):
        state = {
            "base_db": "cr-test",
            "fingerprint": "schema-a",
            "xmlid_fingerprint": "xmlid-a",
            "version_fingerprint": "ver-a",
            "asset_fingerprint": "assets-a",
            "worker_count": 1,
            "clone_names": ["cr-test-worker-0"],
        }
        from .. import config as parallel_config

        with (
            patch.object(parallel_config, "reuse_clones", return_value=True),
            patch.object(parallel_config, "get_clone_prefix", return_value="{db}-worker-"),
            patch.object(cloner, "_can_createdb", return_value=True),
            patch.object(cloner, "_get_db_fingerprint", return_value="schema-a"),
            patch.object(cloner, "_get_xmlid_fingerprint", return_value="xmlid-a"),
            patch.object(cloner, "_get_module_version_fingerprint", return_value="ver-a"),
            patch.object(cloner, "_get_asset_fingerprint", return_value="assets-a"),
            patch.object(cloner, "_load_clone_state", return_value=state),
            patch.object(cloner, "_existing_databases", return_value={"cr-test-worker-0"}),
            patch.object(cloner, "_filestore_dir", side_effect=self._filestore_dir),
            patch.object(cloner, "_save_clone_state"),
            patch.object(cloner, "_fresh_clone") as fresh,
        ):
            names = cloner.clone_databases("cr-test", 1)
        fresh.assert_not_called()
        self.assertEqual(names, ["cr-test-worker-0"])
        self.assertTrue(os.path.exists(os.path.join(self.clone, "ab", "abc123")))
