"""
Tests for the skill-sync performance guarantees.

The skill sync runs on every upgrade of a module under a root. To keep upgrades fast it must:
  * write a body only when it actually changed (``_write_managed``), and
  * produce a body that is stable across runs, so re-syncing unchanged skills is a
    true no-op (``_rewrite_internal_links`` must be idempotent).

These were previously violated: links were rewritten in a second pass, so every
body oscillated between a raw ``.md`` href and a ``/knowledge/article/N`` URL,
forcing two expensive writes per article on every upgrade.
"""

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("oteny_knowledge_sync", "post_install", "-at_install", "test_skill_sync_idempotent")
class TestSkillSyncIdempotent(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Article = cls.env["oteny.article"]

        # Unique fake paths so we don't collide with real managed articles
        # already synced into the test DB.
        cls.root = Article.create(
            {
                "name": "Test Idem Root",
                "body": "<p>Root</p>",
                "is_locked": True,
                "x_skill_is_managed": True,
                "x_skill_root": "Test",
                "x_skill_file_path": "skills/test-idem-root/SKILL.MD",
            }
        )

        # A skill article with a relative .md link to its own roadmap.
        cls.skill = Article.create(
            {
                "name": "test-idem-skill",
                "body": '<p>See <a href="references/roadmap.md">Roadmap</a></p>',
                "parent_id": cls.root.id,
                "is_locked": True,
                "x_skill_is_managed": True,
                "x_skill_root": "Test",
                "x_skill_file_path": "skills/test-idem-skill/SKILL.md",
            }
        )

        cls.roadmap = Article.create(
            {
                "name": "Roadmap",
                "body": "<p>Roadmap content</p>",
                "parent_id": cls.skill.id,
                "is_locked": True,
                "x_skill_is_managed": True,
                "x_skill_root": "Test",
                "x_skill_file_path": "skills/test-idem-skill/references/roadmap.md",
            }
        )

    @staticmethod
    def _body_revisions(article):
        """Number of stored body revisions (grows by one per real body write)."""
        return len((article.html_field_history or {}).get("body", []))

    def test_rewrite_internal_links_is_idempotent(self):
        """A second link-rewrite pass over already-resolved bodies must write nothing."""
        Sync = self.env["oteny.knowledge.sync"]

        # First pass resolves the .md link to the roadmap's Knowledge URL.
        Sync._rewrite_internal_links("Test")
        self.assertIn(
            f"/odoo/action-oteny_article.action_oteny_article/{self.roadmap.id}",
            self.skill.body,
            "First pass should have resolved the roadmap link",
        )

        # Snapshot, run again, and assert nothing changed.
        before = {a.id: a.body for a in (self.root | self.skill | self.roadmap)}
        revs_before = self._body_revisions(self.skill)

        Sync._rewrite_internal_links("Test")

        after = {a.id: a.body for a in (self.root | self.skill | self.roadmap)}
        self.assertEqual(before, after, "Second link-rewrite pass must not change any body")
        self.assertEqual(
            self._body_revisions(self.skill),
            revs_before,
            "Idempotent pass must not add a body revision (i.e. must not write)",
        )

    def test_write_managed_skips_unchanged_and_writes_only_diffs(self):
        """_write_managed must no-op when values match, and write only changed fields."""
        Sync = self.env["oteny.knowledge.sync"]
        article = self.skill

        # Seed one real body revision so "no new revision" is a meaningful assertion.
        Sync._write_managed(article, {"body": "<p>Fresh body content</p>"})
        revs = self._body_revisions(article)
        self.assertEqual(revs, 1, "A real body change should add exactly one revision")

        # Values identical to the current stored state — must be a complete no-op.
        same_values = {
            "name": article.name,
            "body": article.body,
            "icon": article.icon,
            "is_locked": True,
            "parent_id": article.parent_id.id,
            "x_skill_file_path": article.x_skill_file_path,
            "x_skill_is_managed": True,
                "x_skill_root": "Test",
        }
        Sync._write_managed(article, same_values)
        self.assertEqual(
            self._body_revisions(article),
            revs,
            "Re-writing identical values must not touch the body (no new revision)",
        )

        # Changing only the name must update the name without re-writing the body.
        Sync._write_managed(article, {**same_values, "name": "Renamed Idem Skill"})
        self.assertEqual(article.name, "Renamed Idem Skill")
        self.assertEqual(
            self._body_revisions(article),
            revs,
            "Changing the name must not add a body revision",
        )
