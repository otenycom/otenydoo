import os
import shutil
import tempfile
from unittest.mock import patch

from odoo.addons.base.models.ir_attachment import IrAttachment as BaseIrAttachment
from odoo.tests import TransactionCase, tagged
from odoo.tools import config

from odoo.addons import oteny_cloud_filestore
from ..models import ir_attachment as ir_attachment_module
from .common import FakeCloudStore
from .test_read import CloudFilestoreReadMixin

LOGGER = "odoo.addons.oteny_cloud_filestore.models.ir_attachment"
ALERTED_PARAM = "oteny_cloud_filestore.not_server_wide_alerted"


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

    def test_not_server_wide_hook_only_logs(self):
        """The registry setup logs the missing server-wide load and posts nothing."""
        model_class = type(self.env["ir.attachment"])
        self.startPatcher(patch.object(model_class, "_cloud_filestore_mode", return_value="production"))
        alert = self.startPatcher(patch.object(model_class, "_cloud_filestore_alert"))
        with patch.dict(config.options, {"server_wide_modules": ["base", "web"]}):
            with self.assertLogs(LOGGER, "WARNING") as logs:
                self.env["ir.attachment"]._register_hook()
        self.assertEqual(len(logs.output), 1)
        self.assertIn("not a server-wide module", logs.output[0])
        alert.assert_not_called()

        with patch.dict(config.options, {"server_wide_modules": ["base", "web", "oteny_cloud_filestore"]}):
            with self.assertNoLogs(LOGGER, "WARNING"):
                self.env["ir.attachment"]._register_hook()

        # A database that is not in production mode never warns.
        with patch.object(model_class, "_cloud_filestore_mode", return_value="clone"), patch.dict(
            config.options, {"server_wide_modules": ["base", "web"]}
        ):
            with self.assertNoLogs(LOGGER, "WARNING"):
                self.env["ir.attachment"]._register_hook()
        alert.assert_not_called()

    def test_upload_job_alerts_once_when_not_server_wide(self):
        """The upload job posts the alert once, and again only after the line was fixed and lost."""
        self._patch_cloud_filestore()
        alert = self.startPatcher(patch.object(type(self.env["ir.attachment"]), "_cloud_filestore_alert"))
        params = self.env["ir.config_parameter"].sudo()
        params.set_param(ALERTED_PARAM, False)
        not_server_wide = {"server_wide_modules": ["base", "web"]}
        server_wide = {"server_wide_modules": ["base", "web", "oteny_cloud_filestore"]}

        def run_job(options):
            alert.reset_mock()
            with patch.dict(config.options, options):
                self.env["ir.attachment"]._cloud_filestore_upload_pending()
            return [call.args[0] for call in alert.call_args_list]

        self.assertEqual(run_job(not_server_wide), ["_not_server_wide"])
        self.assertTrue(params.get_param(ALERTED_PARAM))
        # The next run stays silent while the line is still missing.
        self.assertEqual(run_job(not_server_wide), [])
        # The line is fixed: the job clears the parameter and posts nothing.
        self.assertEqual(run_job(server_wide), [])
        self.assertFalse(params.get_param(ALERTED_PARAM))
        # A later regression alerts again.
        self.assertEqual(run_job(not_server_wide), ["_not_server_wide"])
        # A database that is not in production mode never alerts.
        params.set_param(ALERTED_PARAM, False)
        self.mode = "clone"
        self.assertEqual(run_job(not_server_wide), [])
        self.assertFalse(params.get_param(ALERTED_PARAM))

    def test_read_before_module_loads(self):
        """The early branch: a registry where this module is not loaded yet reads the bucket.

        The model of such a registry has no `_cloud_filestore_*` methods, so
        the mode and the store come from the module-level functions, and a
        failure only logs: the alert model does not exist there.
        """
        filestore = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, filestore, ignore_errors=True)
        fake = FakeCloudStore()
        fname = "ab/ab12"
        fake.objects[fname] = b"bucket only"
        early_model = EarlyAttachmentModel(self.env, filestore)
        self.startPatcher(patch.object(ir_attachment_module, "cloud_filestore_mode", return_value="production"))
        self.startPatcher(patch.object(ir_attachment_module, "cloud_filestore_store", return_value=fake))
        bucket_failed = self.startPatcher(
            patch.object(type(self.env["oteny.cloud.filestore.alert"]), "_bucket_failed")
        )
        self.assertEqual(ir_attachment_module.cloud_filestore_read(early_model, fname), (b"bucket only", False))
        fake.down = True
        with self.assertLogs(LOGGER, "WARNING") as logs:
            self.assertEqual(ir_attachment_module.cloud_filestore_read(early_model, fname), (b"", True))
        self.assertIn("read of ab/ab12 failed", logs.output[0])
        bucket_failed.assert_not_called()


class EarlyAttachmentModel:
    """An `ir.attachment` of a registry where this module is not loaded yet.

    It offers only what `cloud_filestore_read` needs: the environment, the
    local path of a name, and iteration over its (no) records.
    """

    def __init__(self, env, filestore):
        self.env = env
        self.filestore = filestore

    def _full_path(self, fname):
        return os.path.join(self.filestore, fname)

    def __iter__(self):
        return iter(())


@tagged("oteny_cloud_filestore", "post_install", "-at_install")
class TestCloudFilestoreNotServerWideAlert(TransactionCase):
    def test_alert_posts_and_keeps_outage_state(self):
        channel = self.env["discuss.channel"].create({"name": "Cloud filestore alerts"})
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("oteny_cloud_filestore.alert_channel_id", str(channel.id))
        params.set_param("oteny_cloud_filestore.outage_since", "")
        # The alert posts through a separate cursor in the test transaction: flush so it sees the channel.
        self.env.flush_all()
        self.registry_enter_test_mode(register_cleanup=True)
        self.env["oteny.cloud.filestore.alert"]._not_server_wide()
        # The message came from the separate cursor, so the cache of this cursor does not hold it yet.
        channel.invalidate_recordset()
        messages = channel.message_ids.filtered(lambda message: message.message_type == "comment")
        self.assertEqual(len(messages), 1)
        self.assertIn("server-wide", messages.body)
        self.assertFalse(params.get_param("oteny_cloud_filestore.outage_since"))
