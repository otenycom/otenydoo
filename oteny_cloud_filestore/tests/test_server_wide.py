from unittest.mock import patch

from odoo.addons.base.models.ir_attachment import IrAttachment as BaseIrAttachment
from odoo.tests import TransactionCase, tagged
from odoo.tools import config

from odoo.addons import oteny_cloud_filestore
from ..models import ir_attachment as ir_attachment_module
from .test_read import CloudFilestoreReadMixin

LOGGER = "odoo.addons.oteny_cloud_filestore.models.ir_attachment"


@tagged("oteny_cloud_filestore", "post_install", "-at_install")
class TestCloudFilestoreServerWide(CloudFilestoreReadMixin, TransactionCase):
    def test_post_load_is_idempotent(self):
        """A second post_load (a registry load after the server-wide import) wraps nothing again."""
        oteny_cloud_filestore.post_load()
        wrapper = BaseIrAttachment._file_read
        oteny_cloud_filestore.post_load()
        self.assertIs(BaseIrAttachment._file_read, wrapper)
        self.assertTrue(getattr(wrapper, ir_attachment_module.WRAPPED_MARKER, False))
        # The wrapped method is Odoo's own, not a wrapper around a wrapper.
        self.assertFalse(getattr(wrapper.__wrapped__, ir_attachment_module.WRAPPED_MARKER, False))
        self.assertEqual(wrapper.__wrapped__.__module__, "odoo.addons.base.models.ir_attachment")

    def test_wrapped_base_read_reads_bucket(self):
        """The base class method reads the bucket, as it does while modules before this one load."""
        self._patch_cloud_filestore()
        attachment = self._bucket_only_attachment(b"read by the base method")
        model = self.env["ir.attachment"]
        self.assertEqual(
            BaseIrAttachment._file_read(model, attachment.store_fname), b"read by the base method"
        )
        self.assertEqual(BaseIrAttachment._file_read(model, attachment.store_fname, size=4), b"read")
        # Mode off: Odoo's own behaviour, empty bytes for a file that is not local.
        self.mode = "off"
        with patch.object(self.fake, "get", wraps=self.fake.get) as get:
            self.assertEqual(BaseIrAttachment._file_read(model, attachment.store_fname), b"")
        get.assert_not_called()

    def test_not_server_wide_warns(self):
        model_class = type(self.env["ir.attachment"])
        self.startPatcher(patch.object(model_class, "_cloud_filestore_mode", return_value="production"))
        alert = self.startPatcher(patch.object(model_class, "_cloud_filestore_alert"))
        self.startPatcher(patch.object(ir_attachment_module, "_NOT_SERVER_WIDE_WARNED", set()))
        with patch.dict(config.options, {"server_wide_modules": ["base", "web"]}):
            with self.assertLogs(LOGGER, "WARNING") as logs:
                self.env["ir.attachment"]._register_hook()
            # One warning per database and process: a second registry setup stays silent.
            with self.assertNoLogs(LOGGER, "WARNING"):
                self.env["ir.attachment"]._register_hook()
        self.assertEqual(len(logs.output), 1)
        self.assertIn("not a server-wide module", logs.output[0])
        self.assertEqual([call.args[0] for call in alert.call_args_list], ["_not_server_wide"])

        alert.reset_mock()
        ir_attachment_module._NOT_SERVER_WIDE_WARNED.clear()
        with patch.dict(config.options, {"server_wide_modules": ["base", "web", "oteny_cloud_filestore"]}):
            with self.assertNoLogs(LOGGER, "WARNING"):
                self.env["ir.attachment"]._register_hook()
        alert.assert_not_called()

        # A database that is not in production mode never warns.
        with patch.object(model_class, "_cloud_filestore_mode", return_value="clone"), patch.dict(
            config.options, {"server_wide_modules": ["base", "web"]}
        ):
            with self.assertNoLogs(LOGGER, "WARNING"):
                self.env["ir.attachment"]._register_hook()
        alert.assert_not_called()


@tagged("oteny_cloud_filestore", "post_install", "-at_install")
class TestCloudFilestoreNotServerWideAlert(TransactionCase):
    def test_alert_posts_and_keeps_outage_state(self):
        channel = self.env["discuss.channel"].create({"name": "Cloud filestore alerts"})
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("oteny_cloud_filestore.alert_channel_id", str(channel.id))
        params.set_param("oteny_cloud_filestore.outage_since", "")
        # The separate cursor of the alert shares the test transaction.
        self.env.flush_all()
        self.registry_enter_test_mode(register_cleanup=True)
        self.env["oteny.cloud.filestore.alert"]._not_server_wide()
        channel.invalidate_recordset()
        messages = channel.message_ids.filtered(lambda message: message.message_type == "comment")
        self.assertEqual(len(messages), 1)
        self.assertIn("server-wide", messages.body)
        self.assertFalse(params.get_param("oteny_cloud_filestore.outage_since"))
