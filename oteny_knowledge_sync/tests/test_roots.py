"""Two roots, two trees; an unchanged run writes nothing; a removed root unpublishes."""
import tempfile
from pathlib import Path

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


def _root(base, name, skill, extra_line=""):
    root = Path(base) / name
    skills = root / ".claude" / "skills"
    (skills / skill / "references").mkdir(parents=True)
    (skills / "SKILL.MD").write_text(f"# {name} skills\n\nIndex of {name}.\n")
    (skills / skill / "SKILL.md").write_text(
        f"---\nname: {skill}\ndescription: The {skill} skill.\n---\n# {skill}\n\n"
        f"See [the notes](references/notes.md).{extra_line}\n")
    (skills / skill / "references" / "notes.md").write_text(f"# Notes of {skill}\n\nBody.\n")
    return root


@tagged("post_install", "-at_install", "oteny_knowledge_sync")
class TestRoots(TransactionCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root_a = _root(self.tmp.name, "alpha", "alpha-skill")
        self.root_b = _root(self.tmp.name, "beta", "beta-skill")
        self.Article = self.env["knowledge.article"]
        self.Sync = self.env["oteny.knowledge.sync"]
        self.Param = self.env["ir.config_parameter"].sudo()

    def _configure(self, *roots):
        self.Param.set_param("oteny_knowledge_sync.roots", "\n".join(f"{n}={p}" for n, p in roots))

    def _tree(self, label):
        return self.Article.search([("x_skill_is_managed", "=", True), ("x_skill_root", "=", label)])

    def test_two_roots_make_two_trees_and_links_resolve_inside_each(self):
        self._configure(("Alpha", self.root_a), ("Beta", self.root_b))
        out = self.Sync.sync_skills_to_knowledge()
        self.assertEqual(out["roots"], {"Alpha": 3, "Beta": 3})
        for label, skill in (("Alpha", "alpha-skill"), ("Beta", "beta-skill")):
            tree = self._tree(label)
            self.assertEqual(len(tree), 3, label)
            root = tree.filtered(lambda a: not a.parent_id)
            self.assertEqual(root.name, f"{label} Skills")
            skill_article = tree.filtered(lambda a: a.x_skill_file_path == f"skills/{skill}/SKILL.md")
            notes = tree.filtered(lambda a: a.x_skill_file_path == f"skills/{skill}/references/notes.md")
            self.assertEqual(skill_article.parent_id, root)
            self.assertEqual(notes.parent_id, skill_article)
            self.assertIn(f'href="/knowledge/article/{notes.id}"', skill_article.body)

    def test_an_unchanged_run_writes_nothing(self):
        self._configure(("Alpha", self.root_a))
        self.Sync.sync_skills_to_knowledge()
        before = {a.id: a.write_date for a in self._tree("Alpha")}
        self.env.flush_all()
        self.Sync.sync_skills_to_knowledge()
        after = {a.id: a.write_date for a in self._tree("Alpha")}
        self.assertEqual(before, after)

    def test_a_root_that_leaves_the_list_is_unpublished(self):
        self._configure(("Alpha", self.root_a), ("Beta", self.root_b))
        self.Sync.sync_skills_to_knowledge()
        self.assertEqual(len(self._tree("Beta")), 3)
        self._configure(("Alpha", self.root_a))
        self.Sync.sync_skills_to_knowledge()
        self.assertFalse(self._tree("Beta"))
        self.assertEqual(len(self._tree("Alpha")), 3)

    def test_a_root_without_skills_is_skipped_not_fatal(self):
        empty = Path(self.tmp.name) / "empty"; empty.mkdir()
        self._configure(("Alpha", self.root_a), ("Empty", empty))
        self.assertEqual([label for label, _p in self.Sync._skill_roots()], ["Alpha"])

    def test_default_roots_are_the_addons_entries_that_hold_skills(self):
        from unittest.mock import patch
        from odoo.tools import config
        self.Param.set_param("oteny_knowledge_sync.roots", "")
        plain = Path(self.tmp.name) / "plain"; plain.mkdir()
        fake = [str(plain), str(self.root_a), str(self.root_b), str(self.root_b)]
        with patch.dict(config.options, {"addons_path": fake}):
            roots = self.Sync._skill_roots()
        self.assertEqual([(l, str(p)) for l, p in roots],
                         [("alpha", str(self.root_a.resolve())), ("beta", str(self.root_b.resolve()))])

    def test_legacy_articles_without_a_root_are_adopted_in_place(self):
        self._configure(("Alpha", self.root_a))
        self.Sync.sync_skills_to_knowledge()
        tree = self._tree("Alpha")
        ids = set(tree.ids)
        tree.write({"x_skill_root": False})
        self.Sync.sync_skills_to_knowledge()
        self.assertEqual(set(self._tree("Alpha").ids), ids, "adopted, not recreated")

    def test_two_root_sync_resolves_sibling_name_without_warning(self):
        """A healthy two-root upgrade: Alpha names Beta as a sibling, the old
        one-tree shape. That becomes a Knowledge link. No
        ``Could not resolve link`` WARNING. The
        ``../../otenydoo/.claude/skills/…`` escape is not required.
        """
        skill_md = self.root_a / ".claude" / "skills" / "alpha-skill" / "SKILL.md"
        skill_md.write_text(skill_md.read_text().rstrip() + "\n\nSee [beta](../beta-skill/SKILL.md).\n")
        self._configure(("Alpha", self.root_a), ("Beta", self.root_b))
        logger_name = "odoo.addons.oteny_knowledge_sync.models.knowledge_sync"
        with self.assertLogs(logger_name, level="DEBUG") as cm:
            self.Sync.sync_skills_to_knowledge()
        offending = [
            r for r in cm.records
            if r.levelname == "WARNING"
            and "Could not resolve link" in r.getMessage()
            and "beta-skill" in r.getMessage()
        ]
        self.assertEqual(
            offending,
            [],
            f"Healthy two-root sibling name must not warn. Got: {[r.getMessage() for r in offending]}",
        )
        alpha_skill = self._tree("Alpha").filtered(
            lambda a: a.x_skill_file_path == "skills/alpha-skill/SKILL.md")
        beta_skill = self._tree("Beta").filtered(
            lambda a: a.x_skill_file_path == "skills/beta-skill/SKILL.md")
        self.assertEqual(len(alpha_skill), 1)
        self.assertEqual(len(beta_skill), 1)
        self.assertIn(f'href="/knowledge/article/{beta_skill.id}"', alpha_skill.body)

    def test_two_root_sync_missing_sibling_still_warns(self):
        """A sibling name that no configured knowledge root published still warns."""
        missing_href = "../no-such-skill/SKILL.md"
        skill_md = self.root_a / ".claude" / "skills" / "alpha-skill" / "SKILL.md"
        skill_md.write_text(skill_md.read_text().rstrip() + f"\n\nSee [gone]({missing_href}).\n")
        self._configure(("Alpha", self.root_a), ("Beta", self.root_b))
        logger_name = "odoo.addons.oteny_knowledge_sync.models.knowledge_sync"
        with self.assertLogs(logger_name, level="WARNING") as cm:
            self.Sync.sync_skills_to_knowledge()
        matched = [r for r in cm.records if missing_href in r.getMessage()]
        self.assertTrue(
            matched,
            f"Expected a WARNING for {missing_href!r}. Got: {[r.getMessage() for r in cm.records]}",
        )
