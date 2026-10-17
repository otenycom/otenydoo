"""The sync_to_knowledge front-matter flag, the root index rule, and the exclusion
cascade: an agent-only SKILL.md excludes its whole folder."""
import tempfile
from pathlib import Path

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.oteny_knowledge_sync.tools import markdown_utils


@tagged("post_install", "-at_install", "oteny_knowledge_sync")
class TestSyncFlags(TransactionCase):
    def test_parse_skill_file_defaults_sync_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.md"
            path.write_text("# Hello World\n", encoding="utf-8")
            self.assertTrue(markdown_utils.parse_skill_file(path)["sync_to_knowledge"])

    def test_parse_skill_file_sync_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "internal.md"
            path.write_text("---\nname: internal_tool\nsync_to_knowledge: false\n---\n# Internal\n", encoding="utf-8")
            self.assertFalse(markdown_utils.parse_skill_file(path)["sync_to_knowledge"])

    def test_parse_skill_file_explicit_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "public.md"
            path.write_text("---\nsync_to_knowledge: true\n---\n# Public\n", encoding="utf-8")
            self.assertTrue(markdown_utils.parse_skill_file(path)["sync_to_knowledge"])

    def test_is_repo_skills_root_index(self):
        Sync = self.env["oteny.knowledge.sync"]
        self.assertTrue(Sync._is_repo_skills_root_index(".claude/skills/SKILL.MD"))
        self.assertTrue(Sync._is_repo_skills_root_index(".claude/skills/skill.md"))
        self.assertFalse(Sync._is_repo_skills_root_index(".claude/skills/some-skill/SKILL.md"))
        self.assertFalse(Sync._is_repo_skills_root_index("skills/some-skill/SKILL.md"))

    def test_effective_sync_respects_false_for_nested_skill(self):
        Sync = self.env["oteny.knowledge.sync"]
        self.assertFalse(Sync._effective_sync_to_knowledge({"sync_to_knowledge": False}, ".claude/skills/some-skill/SKILL.md"))

    def test_effective_sync_root_forces_true_when_marked_false(self):
        Sync = self.env["oteny.knowledge.sync"]
        self.assertTrue(Sync._effective_sync_to_knowledge({"sync_to_knowledge": False}, ".claude/skills/SKILL.md"))


@tagged("post_install", "-at_install", "oteny_knowledge_sync")
class TestSyncExcludeCascade(TransactionCase):
    """A skill folder is published as a unit or not at all: an agent-only SKILL.md
    must not let its references land on the root as context-free articles."""

    def _make_root(self):
        return self.env["oteny.article"].create({
            "name": "Test Cascade Root", "body": "<p>Root</p>", "is_locked": True,
            "x_skill_is_managed": True, "x_skill_root": "Test",
            "x_skill_file_path": "skills/test-cascade-root/SKILL.MD",
        })

    def _sync(self, skills_dir, folder, root):
        processed, excluded = set(), set()
        self.env["oteny.knowledge.sync"]._sync_directory(
            "Test", skills_dir, skills_dir / folder, root, processed, excluded, {}, {})
        return processed, excluded

    def _build_tree(self, tmp, front_matter):
        skills = Path(tmp) / "skills"
        skill_dir = skills / "test-cascade-skill"
        (skill_dir / "references" / "deep").mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"{front_matter}# Cascade Skill\n", encoding="utf-8")
        (skill_dir / "references" / "note.md").write_text("# Cascade Note\n", encoding="utf-8")
        (skill_dir / "references" / "deep" / "nested.md").write_text("# Cascade Nested\n", encoding="utf-8")
        return skills

    def test_agent_only_skill_excludes_its_whole_subtree(self):
        root = self._make_root()
        with tempfile.TemporaryDirectory() as tmp:
            skills = self._build_tree(tmp, "---\nsync_to_knowledge: false\n---\n")
            processed, excluded = self._sync(skills, "test-cascade-skill", root)
        self.assertEqual(processed, set())
        self.assertEqual(excluded, {
            "skills/test-cascade-skill/SKILL.md",
            "skills/test-cascade-skill/references/note.md",
            "skills/test-cascade-skill/references/deep/nested.md"})
        self.assertFalse(root.child_ids)
        self.assertFalse(self.env["oteny.article"].search([("x_skill_file_path", "like", "test-cascade-skill%")]))

    def test_published_skill_still_nests_its_references(self):
        root = self._make_root()
        with tempfile.TemporaryDirectory() as tmp:
            skills = self._build_tree(tmp, "")
            processed, excluded = self._sync(skills, "test-cascade-skill", root)
            self.assertEqual(excluded, set())
            self.assertEqual(processed, {
                "skills/test-cascade-skill/SKILL.md",
                "skills/test-cascade-skill/references/note.md",
                "skills/test-cascade-skill/references/deep/nested.md"})
            Article = self.env["oteny.article"]
            skill_article = Article.search([("x_skill_file_path", "=", "skills/test-cascade-skill/SKILL.md")])
            note = Article.search([("x_skill_file_path", "=", "skills/test-cascade-skill/references/note.md")])
            self.assertEqual(skill_article.parent_id, root)
            self.assertEqual(note.parent_id, skill_article)
            self.assertEqual(root.child_ids, skill_article)
            self.assertEqual(skill_article.x_skill_root, "Test")
