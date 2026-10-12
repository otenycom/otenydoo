from odoo.tests import TransactionCase, tagged
from odoo.tools import SQL

from .. import post_init_hook

# A valid 1x1 PNG, so a partner image is accepted by the image field.
TINY_PNG = (
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


@tagged("oteny_cloud_filestore", "post_install", "-at_install")
class TestCloudFilestoreLocalOnly(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.attachment = cls.env["ir.attachment"]
        cls.partner = cls.env["res.partner"].create({"name": "Local only test"})

    def _create(self, **vals):
        vals.setdefault("name", "test.bin")
        vals.setdefault("raw", b"content")
        return self.attachment.create(vals)

    def test_asset_bundle_is_local_only(self):
        bundle = self._create(
            name="web.assets_web.min.js",
            url="/web/assets/1/web.assets_web.min.js",
            res_model="ir.ui.view",
        )
        self.assertTrue(bundle.cloud_local_only)

    def test_install_mode_is_local_only(self):
        attachment = self.attachment.with_context(install_mode=True).create(
            {"name": "module.bin", "raw": b"content"}
        )
        self.assertTrue(attachment.cloud_local_only)

    def test_user_file_is_not_local_only(self):
        attachment = self._create(res_model="res.partner", res_id=self.partner.id)
        self.assertFalse(attachment.cloud_local_only)

    def test_explicit_value_never_turns_bundle_into_bucket_file(self):
        bundle = self._create(url="/web/assets/2/x.min.css", res_model="ir.ui.view", cloud_local_only=False)
        self.assertTrue(bundle.cloud_local_only)
        # An explicit True is kept for a plain file.
        forced = self._create(res_model="res.partner", res_id=self.partner.id, cloud_local_only=True)
        self.assertTrue(forced.cloud_local_only)

    def test_batch_context_only_when_every_vals_is_local_only(self):
        seen = []
        original = type(self.attachment)._file_write

        def spy(model, bin_value, checksum):
            seen.append(model._cloud_filestore_is_local_only())
            return original(model, bin_value, checksum)

        self.patch(type(self.attachment), "_file_write", spy)
        self.patch(type(self.attachment), "_storage", lambda self: "file")
        asset = {"name": "a.js", "raw": b"asset-1", "url": "/web/assets/3/a.js"}
        user = {"name": "u.bin", "raw": b"user-1", "res_model": "res.partner", "res_id": self.partner.id}
        self.attachment.create([asset])
        self.assertEqual(seen, [True])
        seen.clear()
        mixed = self.attachment.create([asset | {"raw": b"asset-2"}, user])
        self.assertEqual(seen, [False, False])
        self.assertEqual(mixed.mapped("cloud_local_only"), [True, False])
        self.assertFalse(self.attachment._cloud_filestore_is_local_only())

    def test_post_init_hook_marks_existing_module_images(self):
        icon = self.attachment.search(
            [("res_model", "=", "ir.ui.menu"), ("res_field", "=", "web_icon_data")], limit=50
        ).filtered(lambda a: self.env["ir.model.data"].search([("model", "=", "ir.ui.menu"), ("res_id", "=", a.res_id)]))[:1]
        if not icon:
            self.skipTest("No menu icon attachment of a menu with an xmlid in this database")
        user_image = self.partner.write({"image_1920": TINY_PNG}) and self.attachment.search(
            [("res_model", "=", "res.partner"), ("res_id", "=", self.partner.id), ("res_field", "=", "image_1920")]
        )
        self.assertTrue(user_image, "the partner image should be an attachment")
        # Reset both flags, so the effect of the hook is visible.
        self.env.cr.execute(
            SQL("UPDATE ir_attachment SET cloud_local_only = FALSE WHERE id IN %s", tuple((icon | user_image).ids))
        )
        icon.invalidate_recordset(["cloud_local_only"])
        user_image.invalidate_recordset(["cloud_local_only"])
        post_init_hook(self.env)
        self.assertTrue(icon.cloud_local_only)
        self.assertFalse(user_image.cloud_local_only)

    def test_post_init_hook_rules_for_assets_imports_and_null(self):
        bundle = self._create(url="/web/assets/9/hook.min.js", res_model="ir.ui.view")
        imported = self.env["res.partner"].create({"name": "Imported partner", "image_1920": TINY_PNG})
        self.env["ir.model.data"].create(
            {
                "module": "__import__",
                "name": "cloud_filestore_partner",
                "model": "res.partner",
                "res_id": imported.id,
            }
        )
        imported_image = self.attachment.search(
            [("res_model", "=", "res.partner"), ("res_id", "=", imported.id), ("res_field", "=", "image_1920")]
        )
        self.assertTrue(imported_image, "the partner image should be an attachment")
        plain = self._create(res_model="res.partner", res_id=self.partner.id)
        self.env.flush_all()
        # The state before the install: a bundle row not flagged, every other row NULL.
        self.env.cr.execute(SQL("UPDATE ir_attachment SET cloud_local_only = FALSE WHERE id = %s", bundle.id))
        user_files = tuple((imported_image | plain).ids)
        self.env.cr.execute(SQL("UPDATE ir_attachment SET cloud_local_only = NULL WHERE id IN %s", user_files))
        post_init_hook(self.env)
        self.assertTrue(bundle.cloud_local_only)
        # Files of imported records are user data.
        self.assertIs(imported_image.cloud_local_only, False)
        self.assertIs(plain.cloud_local_only, False)
        self.env.cr.execute("SELECT count(*) FROM ir_attachment WHERE cloud_local_only IS NULL")
        self.assertEqual(self.env.cr.fetchone()[0], 0)
