from odoo import models
from odoo.tools import SQL

# Images that Odoo makes from another file: the browser makes the `thumbnail` of a PDF
# (mail adds ir.attachment.thumbnail), and image.mixin resizes `image_1920` into the smaller
# sizes. The source file and `image_1920` stay audited.
_GENERATED_IMAGE_FIELDS = ("thumbnail", "image_128", "image_256", "image_512", "image_1024")
# The compiled web asset bundles (web.assets_web.min.js, ...): build output that the next
# request builds again.
_ASSET_URL_PREFIX = "/web/assets/"


class IrAttachment(models.Model):
    """Audit-log integration for attachments (plan oteny-audit/plans/audit-storage-reduction.md,
    cut C, rule N4 and the exclusions of cut I)."""

    _inherit = "ir.attachment"

    # Technical values of the stored file. `store_fname` stays audited: it is the content hash
    # of the file, the only value that proves which content was uploaded. `index_content` is
    # the full text of a document (11.8 MB in 180 days on production); the file is the source.
    _oteny_audit_ignore_fields = {"index_content", "checksum", "mimetype", "file_size"}

    def _oteny_audit_ignore_record(self, record):
        """Skip a generated image and a web asset bundle. Neither `res_field` nor `url` of
        such an attachment changes after the create, so the answer is the same at create and
        at delete."""
        return record.res_field in _GENERATED_IMAGE_FIELDS or (record.url or "").startswith(_ASSET_URL_PREFIX)

    def _oteny_audit_purge_record_sql(self):
        """The rows of the attachments that _oteny_audit_ignore_record skips, for the purge
        of the stored rows. The insert and delete snapshots hold `res_field` and `url`."""
        return SQL(
            """log.record_id IN (
                   SELECT snap.record_id FROM oteny_audit_log snap
                    WHERE snap.model_name = 'ir.attachment' AND snap.field_name = '__snapshot__'
                      AND (snap.snapshot->'res_field'->>'raw' IN %s
                           OR snap.snapshot->'url'->>'raw' LIKE %s))""",
            _GENERATED_IMAGE_FIELDS,
            _ASSET_URL_PREFIX + "%",
        )
