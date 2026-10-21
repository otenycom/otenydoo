"""
Tests for _embed_body_images in the skill sync service.

Locally-referenced images in skill markdown (``<img src="img/foo.png">``) must be
uploaded as ir.attachments and their src rewritten to ``/web/image/<id>`` so the
synced Knowledge article can serve them.

Dedupe uses a content marker (``skill-img:<sha1 of the source file>`` in
``description``), NOT the attachment's own ``checksum`` — Odoo re-encodes images on
store, so ``checksum`` is the sha1 of the transformed bytes and never equals the
source file's sha1. These tests use real PNGs so that re-encode path is exercised
(a previous checksum-based dedupe silently created a new attachment on every sync).
"""

import hashlib
import io
import tempfile
from pathlib import Path

from PIL import Image

from odoo.tests.common import TransactionCase
from odoo.tests import tagged
from odoo.tools import mute_logger


def _png(size=(120, 90), color=(200, 30, 30)):
    """A real (Odoo-re-encodable) PNG byte string."""
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


@tagged("oteny_knowledge_sync", "post_install", "-at_install", "test_skill_sync_images")
class TestSkillSyncImages(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sync = cls.env["oteny.knowledge.sync"]
        cls.Attachment = cls.env["ir.attachment"]
        cls.article = cls.env["oteny.article"].create(
            {
                "name": "Test WP Guide",
                "body": "<p>placeholder</p>",
                "is_locked": True,
                "x_skill_is_managed": True,
                "x_skill_root": "Test",
                "x_skill_file_path": "skills/test-img/references/guide.md",
            }
        )

    def _make_tree(self, tmp, image_bytes=None):
        """Build a skills-root/md-dir/img tree with an ``img/x.png`` and return
        ``(skills_root, md_dir)`` as absolute Paths."""
        skills_root = Path(tmp)
        md_dir = skills_root / "some-skill" / "references"
        (md_dir / "img").mkdir(parents=True, exist_ok=True)
        (md_dir / "img" / "x.png").write_bytes(image_bytes if image_bytes is not None else _png())
        return skills_root, md_dir

    def _marker(self, image_bytes):
        return "skill-img:%s" % hashlib.sha1(image_bytes).hexdigest()

    def test_embeds_local_image_as_attachment(self):
        body = '<p><img class="img-fluid o_we_custom_image" src="img/x.png" alt="t" /></p>'
        png = _png(color=(10, 120, 200))
        with tempfile.TemporaryDirectory() as tmp:
            skills_root, md_dir = self._make_tree(tmp, png)
            new_body, created = self.sync._embed_body_images(
                body, md_dir, skills_root, self.article
            )

        self.assertEqual(len(created), 1, "exactly one attachment created")
        att = self.Attachment.browse(created[0])
        self.assertEqual(att.res_model, "oteny.article")
        self.assertEqual(att.res_id, self.article.id, "bound to the target article")
        self.assertEqual(att.description, self._marker(png), "dedupe marker on description")
        self.assertIn('src="/web/image/%d"' % att.id, new_body)
        self.assertNotIn('src="img/x.png"', new_body)
        self.assertIn("img-fluid", new_body, "display class is preserved")

    def test_dedupe_is_idempotent_for_real_png(self):
        """Re-embedding the same (re-encoded) image reuses the attachment.

        Regression: with checksum-based dedupe this created a fresh attachment on
        every run because Odoo re-encodes images (stored checksum != sha1(file)).
        """
        body = '<p><img src="img/x.png" alt="t" /></p>'
        png = _png(color=(30, 200, 90))
        marker = self._marker(png)
        with tempfile.TemporaryDirectory() as tmp:
            skills_root, md_dir = self._make_tree(tmp, png)
            body1, created1 = self.sync._embed_body_images(
                body, md_dir, skills_root, self.article
            )
            body2, created2 = self.sync._embed_body_images(
                body, md_dir, skills_root, self.article
            )

        self.assertEqual(len(created1), 1)
        self.assertEqual(created2, [], "re-embed reuses the attachment, creates none")
        self.assertEqual(
            self.Attachment.sudo().search_count(
                [("description", "=", marker), ("res_model", "=", "oteny.article")]
            ),
            1,
            "no duplicate attachment for the same image",
        )
        self.assertEqual(body1, body2, "rewritten body is byte-identical (idempotent)")

    def test_changed_image_creates_new_attachment(self):
        body = '<p><img src="img/x.png" alt="t" /></p>'
        with tempfile.TemporaryDirectory() as tmp:
            skills_root, md_dir = self._make_tree(tmp, _png(color=(1, 1, 1)))
            _b1, created1 = self.sync._embed_body_images(
                body, md_dir, skills_root, self.article
            )
            # Overwrite the image with visibly different content.
            (md_dir / "img" / "x.png").write_bytes(_png(size=(64, 64), color=(250, 250, 0)))
            _b2, created2 = self.sync._embed_body_images(
                body, md_dir, skills_root, self.article
            )

        self.assertEqual(len(created1), 1)
        self.assertEqual(len(created2), 1, "changed image → new attachment")
        self.assertNotEqual(created1[0], created2[0], "old attachment left in place, not reused")

    def test_new_article_defers_res_id(self):
        """When the article does not exist yet, attachments are created with
        res_id=0 and their ids returned for the caller to backfill."""
        body = '<p><img src="img/x.png" alt="t" /></p>'
        with tempfile.TemporaryDirectory() as tmp:
            skills_root, md_dir = self._make_tree(tmp, _png(color=(90, 90, 90)))
            new_body, created = self.sync._embed_body_images(
                body, md_dir, skills_root, None
            )

        self.assertEqual(len(created), 1)
        att = self.Attachment.browse(created[0])
        self.assertEqual(att.res_id, 0, "res_id left 0 for the caller to backfill after create")
        self.assertIn('src="/web/image/%d"' % att.id, new_body)

    def test_percent_encoded_src_finds_the_file(self):
        """The markdown converter percent-encodes a file name with spaces or an
        em dash; the sync decodes it to find the file on disk."""
        with tempfile.TemporaryDirectory() as tmp:
            skills_root, md_dir = self._make_tree(tmp)
            (md_dir / "Call \u2014 matrix.png").write_bytes(_png(color=(7, 7, 7)))
            body = '<p><img src="Call%20%E2%80%94%20matrix.png" alt="t" /></p>'
            new_body, created = self.sync._embed_body_images(
                body, md_dir, skills_root, self.article
            )

        self.assertEqual(len(created), 1, "the decoded file name is found")
        self.assertIn('src="/web/image/%d"' % created[0], new_body)

    @mute_logger("odoo.addons.oteny_knowledge_sync.models.knowledge_sync")
    def test_missing_file_is_left_untouched(self):
        # The sync warns and keeps the original src, which is right for a real
        # broken doc. Here the missing file IS the case under test, so that
        # WARNING would only redden an Odoo.sh build (--log-db copies every
        # WARNING into ir_logging, and any row there turns the build red).
        body = '<p><img src="img/missing.png" alt="t" /></p>'
        with tempfile.TemporaryDirectory() as tmp:
            skills_root, md_dir = self._make_tree(tmp)  # only x.png exists
            new_body, created = self.sync._embed_body_images(
                body, md_dir, skills_root, self.article
            )

        self.assertEqual(created, [], "no attachment for a missing file")
        self.assertIn('src="img/missing.png"', new_body, "original src is kept")

    def test_external_src_is_left_untouched(self):
        body = '<p><img src="https://example.com/a.png" alt="t" /></p>'
        with tempfile.TemporaryDirectory() as tmp:
            skills_root, md_dir = self._make_tree(tmp)
            new_body, created = self.sync._embed_body_images(
                body, md_dir, skills_root, self.article
            )

        self.assertEqual(created, [], "external URLs are not uploaded")
        self.assertIn('src="https://example.com/a.png"', new_body)
