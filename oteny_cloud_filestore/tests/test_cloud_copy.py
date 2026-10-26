import datetime
import errno
import hashlib
import json
import os
import shutil
import tempfile
from unittest.mock import patch

from odoo import fields
from odoo.cli.command import commands, load_addons_commands
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from ..models import cloud_copy as cloud_copy_module
from .common import FakeCloudStore, patch_read_cache

STATE_FILE = ".cloud_copy_state.json"


def _name_of(data):
    digest = hashlib.sha1(data).hexdigest()
    return f"{digest[:2]}/{digest}"


@tagged("oteny_cloud_filestore", "post_install", "-at_install")
class TestCloudFilestoreCopy(TransactionCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeCloudStore()
        self.mode = "production"
        # The local filestore of the test is a temporary folder: a save puts the file in the fake
        # bucket and removes the local copy, like in production mode.
        filestore = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, filestore, ignore_errors=True)
        model = type(self.env["ir.attachment"])
        for patcher in (
            patch.object(model, "_filestore", return_value=filestore),
            patch.object(model, "_storage", return_value="file"),
            patch.object(model, "_cloud_filestore_mode", side_effect=lambda: self.mode),
            patch.object(model, "_cloud_filestore_store", return_value=self.fake),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        patch_read_cache(self)
        self.folder = tempfile.mkdtemp()
        self.checklist = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.checklist, ignore_errors=True)
        # Only the rows of the test count: files of rows that exist already are never in the bucket.
        self.env["ir.attachment"].with_context(skip_res_field_check=True).search(
            [("store_fname", "!=", False)]
        ).cloud_local_only = True

    def _row(self, data, local_only=False, in_bucket=True):
        """Save a file through the ORM; return its name. A local-only file never goes to the bucket."""
        attachment = self.env["ir.attachment"].create(
            {"name": "row", "raw": data, "cloud_local_only": local_only}
        )
        name = attachment.store_fname
        if not in_bucket:
            self.fake.objects.pop(name, None)
        return name

    def _copy(self):
        return self.env["ir.attachment"]._cloud_filestore_copy_into(self.folder, self.checklist)

    def _put_file(self, name, data=b"old"):
        path = os.path.join(self.folder, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as file:
            file.write(data)
        return path

    def _state(self):
        with open(os.path.join(self.folder, STATE_FILE)) as state_file:
            return json.load(state_file)

    def _set_state(self, state):
        with open(os.path.join(self.folder, STATE_FILE), "w") as state_file:
            json.dump(state, state_file)

    def test_copies_skips_and_reads_checklist(self):
        wanted = self._row(b"bucket only")
        present = self._row(b"already here")
        self._put_file(present, b"already here")
        local_only = self._row(b"local only", local_only=True)
        marker_data = b"removed by production"
        marker = _name_of(marker_data)
        self.fake.objects[marker] = marker_data
        os.makedirs(os.path.join(self.checklist, marker[:2]))
        open(os.path.join(self.checklist, marker), "w").close()
        gets = []
        original_get = self.fake.get
        self.fake.get = lambda name: gets.append(name) or original_get(name)

        result = self._copy()

        self.assertEqual(
            result,
            {
                "written": 2,
                "seeded": 0,
                "present": 1,
                "missing": 0,
                "deleted": 0,
                "removed": 0,
                "stopped": False,
            },
        )
        with open(os.path.join(self.folder, wanted), "rb") as file:
            self.assertEqual(file.read(), b"bucket only")
        self.assertTrue(os.path.exists(os.path.join(self.folder, marker)))
        self.assertFalse(os.path.exists(os.path.join(self.folder, local_only)))
        self.assertNotIn(present, gets)
        self.assertNotIn(local_only, gets)

    def test_missing_and_damaged_objects_do_not_stop_the_run(self):
        self._row(b"not in bucket", in_bucket=False)
        damaged = self._row(b"damaged")
        self.fake.objects[damaged] = b"other bytes"
        good = self._row(b"good")
        result = self._copy()
        self.assertEqual(result["missing"], 2)
        self.assertEqual(result["written"], 1)
        self.assertFalse(result["stopped"])
        self.assertTrue(os.path.exists(os.path.join(self.folder, good)))
        self.assertFalse(os.path.exists(os.path.join(self.folder, damaged)))

    def test_marker_only_name_not_in_bucket_is_deleted_not_missing(self):
        """A deleted local-only file has a marker but was never uploaded: no warning."""
        gone = _name_of(b"module image of an uninstalled module")
        os.makedirs(os.path.join(self.checklist, gone[:2]))
        open(os.path.join(self.checklist, gone), "w").close()
        self._row(b"row file not in bucket", in_bucket=False)
        with self.assertLogs("odoo.addons.oteny_cloud_filestore.models.cloud_copy", "WARNING") as logs:
            result = self._copy()
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(result["missing"], 1)
        self.assertFalse(any(gone in line for line in logs.output))
        self.assertFalse(os.path.exists(os.path.join(self.folder, gone)))

    def test_store_outage_stops_the_download_but_not_the_removal(self):
        self._row(b"wanted")
        extra = _name_of(b"extra")
        self._put_file(extra)
        self.fake.down = True
        result = self._copy()
        self.assertTrue(result["stopped"])
        self.assertEqual(result["written"], 0)
        self.assertEqual(self._state(), {extra: fields.Date.today().isoformat()})

    def test_removal_waits_30_days(self):
        self._row(b"wanted")
        today = fields.Date.today()
        extra = _name_of(b"extra")
        old = _name_of(b"old extra")
        again = self._row(b"wanted again")
        extra_path = self._put_file(extra)
        old_path = self._put_file(old)
        self._set_state(
            {
                old: (today - datetime.timedelta(days=30)).isoformat(),
                again: (today - datetime.timedelta(days=5)).isoformat(),
            }
        )

        result = self._copy()

        self.assertEqual(result["removed"], 1)
        self.assertTrue(os.path.exists(extra_path))
        self.assertFalse(os.path.exists(old_path))
        self.assertEqual(self._state(), {extra: today.isoformat()})

    def test_empty_wanted_set_removes_nothing(self):
        extra = _name_of(b"extra")
        extra_path = self._put_file(extra)
        self._set_state({extra: "2000-01-01"})
        result = self._copy()
        self.assertEqual(result["removed"], 0)
        self.assertTrue(os.path.exists(extra_path))
        self.assertEqual(self._state(), {extra: "2000-01-01"})

    def test_unusable_state_file_starts_the_wait_again(self):
        self._row(b"wanted")
        extra = _name_of(b"extra")
        self._put_file(extra)
        self._set_state({extra: "not a date"})
        self.assertEqual(self._copy()["removed"], 0)
        self.assertEqual(self._state(), {extra: fields.Date.today().isoformat()})

    def test_mode_off_raises(self):
        self.mode = "off"
        with self.assertRaises(UserError):
            self._copy()

    def _seed_file(self, seed, name, data):
        path = os.path.join(seed, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as file:
            file.write(data)
        return path

    def test_seed_folder_is_linked_not_downloaded(self):
        """A file that the Odoo.sh mirror holds already is hard-linked: no file is stored twice (M1)."""
        seed = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, seed, ignore_errors=True)
        in_seed = self._row(b"in the mirror")
        seed_path = self._seed_file(seed, in_seed, b"in the mirror")
        wrong = self._row(b"right bytes")
        self._seed_file(seed, wrong, b"wrong bytes")  # another SHA-1: downloaded, not linked
        only_bucket = self._row(b"only in the bucket")
        gets = []
        original_get = self.fake.get
        self.fake.get = lambda name: gets.append(name) or original_get(name)

        result = self.env["ir.attachment"]._cloud_filestore_copy_into(self.folder, self.checklist, seed)

        self.assertEqual((result["seeded"], result["written"], result["stopped"]), (1, 2, False))
        self.assertEqual(sorted(gets), sorted([wrong, only_bucket]))
        self.assertEqual(os.stat(os.path.join(self.folder, in_seed)).st_ino, os.stat(seed_path).st_ino)
        with open(os.path.join(self.folder, wrong), "rb") as file:
            self.assertEqual(file.read(), b"right bytes")
        # The next night finds every file present.
        again = self.env["ir.attachment"]._cloud_filestore_copy_into(self.folder, self.checklist, seed)
        self.assertEqual((again["present"], again["seeded"], again["written"]), (3, 0, 0))

    def test_seed_on_another_mount_is_copied(self):
        """A link across two mounts fails with EXDEV: the job copies the seed file and logs it once (M1)."""
        seed = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, seed, ignore_errors=True)
        names = [self._row(data) for data in (b"first", b"second")]
        for name, data in zip(names, (b"first", b"second")):
            self._seed_file(seed, name, data)
        real_link = os.link

        def link_across_mounts(source, target, *args, **kwargs):
            if str(source).startswith(seed):
                raise OSError(errno.EXDEV, "Invalid cross-device link")
            return real_link(source, target, *args, **kwargs)

        with patch.object(cloud_copy_module.os, "link", side_effect=link_across_mounts), self.assertLogs(
            cloud_copy_module.__name__, "WARNING"
        ) as logs:
            result = self.env["ir.attachment"]._cloud_filestore_copy_into(self.folder, self.checklist, seed)
        self.assertEqual((result["seeded"], result["written"], result["stopped"]), (2, 0, False))
        self.assertEqual(len([line for line in logs.output if "another file system" in line]), 1)
        for name, data in zip(names, (b"first", b"second")):
            with open(os.path.join(self.folder, name), "rb") as file:
                self.assertEqual(file.read(), data)

    def test_command_skips_when_the_cloud_filestore_is_not_in_use(self):
        """The nightly standby run must tell "not in use" (exit 2) from a failed copy (M4)."""
        load_addons_commands("cloud_dump")
        dump = commands["cloud_dump"].dump
        self._row(b"wanted")
        with self.assertLogs("odoo.cli.cloud_dump", "INFO") as logs:
            self.assertEqual(dump(self.env, self.folder, self.checklist), 0)
        self.assertIn("Cloud dump result: {", logs.output[-1])
        self.mode = "off"
        with self.assertLogs("odoo.cli.cloud_dump", "INFO") as logs:
            self.assertEqual(dump(self.env, self.folder, self.checklist), 2)
        self.assertIn("Cloud dump result: skipped", logs.output[-1])
        self.mode = "production"
        # On, but without a usable store (N1): a fault the nightly run must report, never "skipped".
        model = type(self.env["ir.attachment"])
        with patch.object(model, "_cloud_filestore_store", return_value=None):
            with self.assertLogs("odoo.cli.cloud_dump", "ERROR") as logs:
                self.assertEqual(dump(self.env, self.folder, self.checklist), 4)
        self.assertIn("Cloud dump result: failed, no usable store", logs.output[-1])
        module =self.env["ir.module.module"].search([("name", "=", "oteny_cloud_filestore")])
        module.state = "uninstalled"
        with self.assertLogs("odoo.cli.cloud_dump", "INFO") as logs:
            self.assertEqual(dump(self.env, self.folder, self.checklist), 2)
        self.assertIn("skipped, the module oteny_cloud_filestore is not installed", logs.output[-1])
