import os
import shutil
import tempfile
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged
from odoo.tools import config

from ..models import ir_attachment as ir_attachment_module

WRITE_KEY = ("write-id", "write-secret")
NO_WRITE_KEY = (None, None)


def write_key(value):
    """Patch the write key that odoo.conf gives the module."""
    return patch.object(ir_attachment_module, "_cloud_filestore_write_key", return_value=value)


@tagged("oteny_cloud_filestore", "post_install", "-at_install")
class TestCloudFilestoreMode(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.attachment = cls.env["ir.attachment"]

    def setUp(self):
        super().setUp()
        # The store cache is module-level: start clean and never let a mock
        # outlive a test.
        ir_attachment_module._STORE_CACHE.clear()
        self.addCleanup(ir_attachment_module._STORE_CACHE.clear)

    def _configure_bucket(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("oteny_cloud_filestore.endpoint_url", "http://store.test:3900")
        params.set_param("oteny_cloud_filestore.region", "garage")
        params.set_param("oteny_cloud_filestore.bucket", "test-bucket")
        params.set_param("oteny_cloud_filestore.read_access_key_id", "read-id")
        params.set_param("oteny_cloud_filestore.read_secret_access_key", "read-secret")

    def test_mode_off_without_bucket(self):
        with write_key(WRITE_KEY):
            self.assertEqual(self.attachment._cloud_filestore_mode(), "off")
            self.assertIsNone(self.attachment._cloud_filestore_store())

    def test_mode_production(self):
        self._configure_bucket()
        self.env["ir.config_parameter"].sudo().set_param("database.is_neutralized", "")
        with write_key(WRITE_KEY):
            self.assertEqual(self.attachment._cloud_filestore_mode(), "production")
            self.assertIsNotNone(self.attachment._cloud_filestore_store())

    def test_mode_clone_without_key(self):
        self._configure_bucket()
        with write_key(NO_WRITE_KEY):
            self.assertEqual(self.attachment._cloud_filestore_mode(), "clone")

    def test_mode_neutralized_with_key_is_clone(self):
        self._configure_bucket()
        self.env["ir.config_parameter"].sudo().set_param("database.is_neutralized", "True")
        with write_key(WRITE_KEY):
            self.assertEqual(self.attachment._cloud_filestore_mode(), "clone")
            # The write key in odoo.conf must never reach the store.
            with patch.object(ir_attachment_module, "CloudStore") as store_class:
                self.attachment._cloud_filestore_store()
        kwargs = store_class.call_args.kwargs
        self.assertEqual(kwargs["access_key_id"], "read-id")
        self.assertNotEqual(kwargs["access_key_id"], "write-id")
        self.assertNotEqual(kwargs["secret_access_key"], "write-secret")

    def test_clone_without_read_keys_has_no_store(self):
        self._configure_bucket()
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("oteny_cloud_filestore.read_access_key_id", "")
        params.set_param("oteny_cloud_filestore.read_secret_access_key", "")
        with write_key(NO_WRITE_KEY):
            with patch.object(ir_attachment_module, "CloudStore") as store_class:
                self.assertIsNone(self.attachment._cloud_filestore_store())
        store_class.assert_not_called()

    def test_store_uses_read_key_in_clone(self):
        self._configure_bucket()
        with write_key(NO_WRITE_KEY):
            with patch.object(ir_attachment_module, "CloudStore") as store_class:
                self.attachment._cloud_filestore_store()
        kwargs = store_class.call_args.kwargs
        self.assertEqual(kwargs["access_key_id"], "read-id")
        self.assertEqual(kwargs["secret_access_key"], "read-secret")

    def test_store_uses_write_key_in_production_and_is_cached(self):
        self._configure_bucket()
        self.env["ir.config_parameter"].sudo().set_param("database.is_neutralized", "")
        ir_attachment_module._STORE_CACHE.clear()
        with write_key(WRITE_KEY):
            with patch.object(ir_attachment_module, "CloudStore") as store_class:
                first = self.attachment._cloud_filestore_store()
                second = self.attachment._cloud_filestore_store()
                self.assertIs(first, second)
                self.assertEqual(store_class.call_count, 1)
                self.assertEqual(store_class.call_args.kwargs["access_key_id"], "write-id")
                # A settings change builds a new store.
                self.env["ir.config_parameter"].sudo().set_param("oteny_cloud_filestore.bucket", "other")
                self.attachment._cloud_filestore_store()
                self.assertEqual(store_class.call_count, 2)

    def test_bad_endpoint_never_fails_a_save_or_a_read(self):
        """boto3 raises ValueError for an endpoint without scheme: the store is then disabled."""
        self._configure_bucket()
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("oteny_cloud_filestore.endpoint_url", "fsn1.example.invalid")
        params.set_param("database.is_neutralized", "")
        filestore = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, filestore, ignore_errors=True)
        model = type(self.attachment)
        self.startPatcher(patch.object(model, "_filestore", return_value=filestore))
        self.startPatcher(patch.object(model, "_storage", return_value="file"))
        # The alert runs in its own cursor: keep it out of this test database.
        alert = self.startPatcher(patch.object(model, "_cloud_filestore_alert"))
        self.startPatcher(write_key(WRITE_KEY))
        self.assertEqual(self.attachment._cloud_filestore_mode(), "production")
        with self.assertLogs("odoo.addons.oteny_cloud_filestore.models.ir_attachment", "WARNING") as logs:
            attachment = self.attachment.create({"name": "user.bin", "raw": b"stays local"})
        self.assertIn("ValueError", "\n".join(logs.output))
        path = attachment._full_path(attachment.store_fname)
        self.assertTrue(os.path.exists(path))
        self.assertEqual([call.args[0] for call in alert.call_args_list], ["_bucket_failed"])
        # A file that is not local reads as empty bytes, never as an exception.
        os.unlink(path)
        with self.assertLogs("odoo.addons.oteny_cloud_filestore.models.ir_attachment", "WARNING"):
            self.assertEqual(attachment._file_read(attachment.store_fname), b"")
        self.assertEqual(ir_attachment_module._STORE_CACHE, {})

    def test_write_key_comes_from_its_own_section(self):
        """Odoo warns about an unknown key in [options], so the write key has its own section."""
        rcfile = self._odoo_conf(
            "[options]\n"
            "cloud_filestore_write_access_key_id = old-id\n"
            "cloud_filestore_write_secret_access_key = old-secret\n"
            "\n"
            "[oteny_cloud_filestore]\n"
            "write_access_key_id = write-id\n"
            "write_secret_access_key = write-secret\n"
        )
        with patch.dict(config.options, {"config": rcfile}):
            self.assertEqual(ir_attachment_module._cloud_filestore_write_key(), WRITE_KEY)

    def test_write_key_in_options_is_not_read(self):
        rcfile = self._odoo_conf(
            "[options]\n"
            "cloud_filestore_write_access_key_id = old-id\n"
            "cloud_filestore_write_secret_access_key = old-secret\n"
        )
        with patch.dict(config.options, {"config": rcfile}):
            self.assertEqual(ir_attachment_module._cloud_filestore_write_key(), NO_WRITE_KEY)
            self._configure_bucket()
            self.assertEqual(self.attachment._cloud_filestore_mode(), "clone")

    def _odoo_conf(self, text):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        rcfile = os.path.join(folder, "odoo.conf")
        with open(rcfile, "w") as handle:
            handle.write(text)
        return rcfile
