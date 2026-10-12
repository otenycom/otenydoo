from unittest.mock import patch

from odoo.tests import TransactionCase, tagged
from odoo.tools import config

from ..models import ir_attachment as ir_attachment_module

WRITE_OPTIONS = {
    "cloud_filestore_write_access_key_id": "write-id",
    "cloud_filestore_write_secret_access_key": "write-secret",
}


@tagged("oteny_cloud_filestore", "post_install", "-at_install")
class TestCloudFilestoreMode(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.attachment = cls.env["ir.attachment"]

    def _configure_bucket(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("oteny_cloud_filestore.endpoint_url", "http://store.test:3900")
        params.set_param("oteny_cloud_filestore.region", "garage")
        params.set_param("oteny_cloud_filestore.bucket", "test-bucket")
        params.set_param("oteny_cloud_filestore.read_access_key_id", "read-id")
        params.set_param("oteny_cloud_filestore.read_secret_access_key", "read-secret")

    def test_mode_off_without_bucket(self):
        with patch.dict(config.options, WRITE_OPTIONS):
            self.assertEqual(self.attachment._cloud_filestore_mode(), "off")
            self.assertIsNone(self.attachment._cloud_filestore_store())

    def test_mode_production(self):
        self._configure_bucket()
        self.env["ir.config_parameter"].sudo().set_param("database.is_neutralized", "")
        with patch.dict(config.options, WRITE_OPTIONS):
            self.assertEqual(self.attachment._cloud_filestore_mode(), "production")
            self.assertIsNotNone(self.attachment._cloud_filestore_store())

    def test_mode_clone_without_key(self):
        self._configure_bucket()
        with patch.dict(
            config.options,
            {k: None for k in WRITE_OPTIONS},
        ):
            self.assertEqual(self.attachment._cloud_filestore_mode(), "clone")

    def test_mode_neutralized_with_key_is_clone(self):
        self._configure_bucket()
        self.env["ir.config_parameter"].sudo().set_param("database.is_neutralized", "True")
        with patch.dict(config.options, WRITE_OPTIONS):
            self.assertEqual(self.attachment._cloud_filestore_mode(), "clone")

    def test_store_uses_read_key_in_clone(self):
        self._configure_bucket()
        with patch.dict(config.options, {k: None for k in WRITE_OPTIONS}):
            with patch.object(ir_attachment_module, "CloudStore") as store_class:
                self.attachment._cloud_filestore_store()
        kwargs = store_class.call_args.kwargs
        self.assertEqual(kwargs["access_key_id"], "read-id")
        self.assertEqual(kwargs["secret_access_key"], "read-secret")

    def test_store_uses_write_key_in_production_and_is_cached(self):
        self._configure_bucket()
        self.env["ir.config_parameter"].sudo().set_param("database.is_neutralized", "")
        ir_attachment_module._STORE_CACHE.clear()
        with patch.dict(config.options, WRITE_OPTIONS):
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
