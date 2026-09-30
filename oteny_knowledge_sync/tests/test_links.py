"""
Tests for _rewrite_internal_links in the skill sync service.

Verifies that relative .md links in Knowledge article bodies resolve
to the correct sibling/child article rather than an identically-named
file in a different skill folder.
"""

from urllib.parse import quote

from odoo.tests.common import TransactionCase
from odoo.tests import tagged


@tagged("oteny_knowledge_sync", "post_install", "-at_install", "test_skill_sync_links")
class TestSkillSyncLinks(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Article = cls.env["knowledge.article"]
        admin_partner = cls.env.ref("base.partner_admin")

        member_vals = [(0, 0, {"partner_id": admin_partner.id, "permission": "write"})]

        # Use unique fake paths to avoid conflicts with pre-existing
        # managed articles from the real skill sync in the test DB.

        # Root article (simulates a root article)
        cls.root = Article.create(
            {
                "name": "Test Skills Root",
                "body": "<p>Root</p>",
                "is_locked": True,
                "internal_permission": "read",
                "article_member_ids": member_vals,
                "x_skill_is_managed": True,
                "x_skill_root": "Test",
                "x_skill_file_path": "skills/test-link-root/SKILL.MD",
            }
        )

        # some-skill SKILL.md — contains a relative link to references/roadmap.md
        cls.hr_skill = Article.create(
            {
                "name": "test-link-hr",
                "body": (
                    '<p>See <a href="references/roadmap.md">Roadmap</a></p>'
                    '<p>See <a href="../test-link-manning/SKILL.md">Manning Agency</a></p>'
                ),
                "parent_id": cls.root.id,
                "is_locked": True,
                "internal_permission": "read",
                "article_member_ids": member_vals,
                "x_skill_is_managed": True,
                "x_skill_root": "Test",
                "x_skill_file_path": "skills/test-link-hr/SKILL.md",
            }
        )

        # HR's roadmap — the CORRECT target for references/roadmap.md
        cls.hr_roadmap = Article.create(
            {
                "name": "Roadmap",
                "body": "<p>HR Roadmap content</p>",
                "parent_id": cls.hr_skill.id,
                "is_locked": True,
                "internal_permission": "read",
                "article_member_ids": member_vals,
                "x_skill_is_managed": True,
                "x_skill_root": "Test",
                "x_skill_file_path": "skills/test-link-hr/references/roadmap.md",
            }
        )

        # Root-level roadmap — the WRONG target that currently wins via endswith
        cls.root_roadmap = Article.create(
            {
                "name": "Roadmap",
                "body": "<p>Root Roadmap content</p>",
                "parent_id": cls.root.id,
                "is_locked": True,
                "internal_permission": "read",
                "article_member_ids": member_vals,
                "x_skill_is_managed": True,
                "x_skill_root": "Test",
                "x_skill_file_path": "skills/test-link-root/references/roadmap.md",
            }
        )

        # Manning Agency SKILL.md — target for ../test-link-manning/SKILL.md
        cls.manning_skill = Article.create(
            {
                "name": "test-link-manning",
                "body": "<p>Manning Agency content</p>",
                "parent_id": cls.root.id,
                "is_locked": True,
                "internal_permission": "read",
                "article_member_ids": member_vals,
                "x_skill_is_managed": True,
                "x_skill_root": "Test",
                "x_skill_file_path": "skills/test-link-manning/SKILL.md",
            }
        )

    def test_relative_link_resolves_to_correct_sibling(self):
        """references/roadmap.md in some-skill SKILL.md must link to the
        HR roadmap article, not the identically-named root-level roadmap."""
        self.env["oteny.knowledge.sync"]._rewrite_internal_links("Test")

        self.hr_skill.invalidate_recordset(["body"])
        body = self.hr_skill.body

        expected_href = f"/knowledge/article/{self.hr_roadmap.id}"
        wrong_href = f"/knowledge/article/{self.root_roadmap.id}"

        self.assertIn(
            expected_href,
            body,
            f"Roadmap link should point to HR roadmap (id={self.hr_roadmap.id}), "
            f"got body: {body}",
        )
        self.assertNotIn(
            wrong_href,
            body,
            f"Roadmap link must NOT point to root-level roadmap (id={self.root_roadmap.id})",
        )

    def test_parent_relative_link_resolves_correctly(self):
        """../other-skill/SKILL.md should resolve to the
        manning agency article from within some-skill."""
        self.env["oteny.knowledge.sync"]._rewrite_internal_links("Test")

        self.hr_skill.invalidate_recordset(["body"])
        body = self.hr_skill.body

        expected_href = f"/knowledge/article/{self.manning_skill.id}"
        self.assertIn(
            expected_href,
            body,
            f"Manning Agency link should point to id={self.manning_skill.id}, "
            f"got body: {body}",
        )

    def test_external_tree_link_no_warning(self):
        """A markdown link that escapes the synced skills tree must not emit a
        'Could not resolve link' warning. Such links are intentional cross-tree
        references (visible in editors that span the whole workspace) and can
        never be Knowledge articles — warning would be noise. The original href
        is kept as-is in the body. Two escape shapes are covered:
        - into a sibling repo (resolved path keeps a leading ../)
        - into a sibling directory of skills/ under .claude, e.g. the
          ../../../commands/profile.md link in the profiler-analysis reference,
          whose resolved path (commands/profile.md) has no leading ../ and
          previously slipped past the suppression check."""
        Article = self.env["knowledge.article"]
        admin_partner = self.env.ref("base.partner_admin")
        member_vals = [(0, 0, {"partner_id": admin_partner.id, "permission": "write"})]

        external_href = "../../../../external-repo/foo/README.md"
        external_article = Article.create(
            {
                "name": "test-link-external",
                "body": f'<p>See <a href="{external_href}">External</a></p>',
                "parent_id": self.root.id,
                "is_locked": True,
                "internal_permission": "read",
                "article_member_ids": member_vals,
                "x_skill_is_managed": True,
                "x_skill_root": "Test",
                "x_skill_file_path": "skills/test-link-external/SKILL.md",
            }
        )

        # From skills/<skill>/references/, ../../../ lands on .claude — the
        # commands/ sibling of skills/ — so the normalized path has no leading
        # ../ but is still outside the synced tree.
        sibling_dir_href = "../../../commands/profile.md"
        sibling_dir_article = Article.create(
            {
                "name": "test-link-sibling-dir",
                "body": f'<p>See <a href="{sibling_dir_href}">/profile</a></p>',
                "parent_id": self.root.id,
                "is_locked": True,
                "internal_permission": "read",
                "article_member_ids": member_vals,
                "x_skill_is_managed": True,
                "x_skill_root": "Test",
                "x_skill_file_path": "skills/test-link-sibling/references/analysis.md",
            }
        )

        logger_name = "odoo.addons.oteny_knowledge_sync.models.knowledge_sync"
        with self.assertLogs(logger_name, level="DEBUG") as cm:
            self.env["oteny.knowledge.sync"]._rewrite_internal_links("Test")

        # Filter to warnings about these specific external links only —
        # unrelated managed articles in the test DB may produce other warnings.
        offending = [
            r
            for r in cm.records
            if r.levelname == "WARNING"
            and (external_href in r.getMessage() or sibling_dir_href in r.getMessage())
        ]
        self.assertEqual(
            offending,
            [],
            f"No 'Could not resolve link' warning should fire for paths that "
            f"escape the skills tree. Got: {[r.getMessage() for r in offending]}",
        )

        # Original hrefs preserved verbatim — links stay as-is, just not rewritten.
        external_article.invalidate_recordset(["body"])
        self.assertIn(
            external_href,
            external_article.body,
            "External-tree href should be preserved unchanged in the article body",
        )
        sibling_dir_article.invalidate_recordset(["body"])
        self.assertIn(
            sibling_dir_href,
            sibling_dir_article.body,
            "Sibling-directory href should be preserved unchanged in the article body",
        )

    def test_sibling_claude_folder_link_no_warning(self):
        """A link into a NON-SYNCED sibling folder of .claude (e.g. commands/) must not
        warn. Only .claude/skills/ is published, so such a target can never be a
        Knowledge article — the link is a deliberate cross-reference to a real file.

        Regression: stored paths are relative to .claude, so '../../../commands/profile.md'
        from a skill's references/ normalizes to a clean 'commands/profile.md' with NO
        leading '..'. The old escape check only caught paths that walked out of .claude
        entirely, so this shape fell through and warned on every single sync — against a
        file that exists (.claude/commands/profile.md, the real /profile command)."""
        Article = self.env["knowledge.article"]
        admin_partner = self.env.ref("base.partner_admin")
        member_vals = [(0, 0, {"partner_id": admin_partner.id, "permission": "write"})]

        sibling_href = "../../../commands/profile.md"
        sibling_article = Article.create(
            {
                "name": "test-link-sibling-claude",
                "body": f'<p>See <a href="{sibling_href}">/profile</a></p>',
                "parent_id": self.root.id,
                "is_locked": True,
                "internal_permission": "read",
                "article_member_ids": member_vals,
                "x_skill_is_managed": True,
                "x_skill_root": "Test",
                "x_skill_file_path": "skills/test-link-sibling/references/profiler.md",
            }
        )

        logger_name = "odoo.addons.oteny_knowledge_sync.models.knowledge_sync"
        with self.assertLogs(logger_name, level="DEBUG") as cm:
            self.env["oteny.knowledge.sync"]._rewrite_internal_links("Test")

        offending = [
            r for r in cm.records
            if r.levelname == "WARNING" and sibling_href in r.getMessage()
        ]
        self.assertEqual(
            offending,
            [],
            f"No warning should fire for a link into a non-synced .claude folder. "
            f"Got: {[r.getMessage() for r in offending]}",
        )

        sibling_article.invalidate_recordset(["body"])
        self.assertIn(
            sibling_href,
            sibling_article.body,
            "Sibling-.claude href should be preserved unchanged in the article body",
        )

    def test_escapes_skills_tree_classification(self):
        """The rule itself: only paths under skills/ are candidates for an article."""
        Sync = self.env["oteny.knowledge.sync"]
        # Outside the synced tree — silent cross-references.
        self.assertTrue(Sync._escapes_skills_tree("commands/profile.md"))
        self.assertTrue(Sync._escapes_skills_tree("../../riverdeploy/README.md"))
        self.assertTrue(Sync._escapes_skills_tree("launch.json"))
        # Inside it — a miss here is a real authoring bug and must still warn.
        self.assertFalse(Sync._escapes_skills_tree("skills/some-skill/SKILL.md"))
        self.assertFalse(Sync._escapes_skills_tree("skills/typo-does-not-exist.md"))

    def test_agent_only_skill_link_no_warning(self):
        """A link to a skill kept agent-only (sync_to_knowledge: false) has no
        Knowledge article to target. The rewriter must leave the link as-is and
        NOT warn — the exclusion is intentional, not a broken link."""
        Article = self.env["knowledge.article"]
        admin_partner = self.env.ref("base.partner_admin")
        member_vals = [(0, 0, {"partner_id": admin_partner.id, "permission": "write"})]

        # An in-tree link that would normally warn (no matching article), but
        # whose target is passed in as intentionally excluded.
        excluded_href = "test-link-agentonly/SKILL.md"
        agentonly_ref = Article.create(
            {
                "name": "test-link-index",
                "body": f'<p>See <a href="{excluded_href}">Agent Only</a></p>',
                "parent_id": self.root.id,
                "is_locked": True,
                "internal_permission": "read",
                "article_member_ids": member_vals,
                "x_skill_is_managed": True,
                "x_skill_root": "Test",
                "x_skill_file_path": "skills/test-link-index/SKILL.md",
            }
        )

        excluded_paths = {"skills/test-link-agentonly/SKILL.md"}

        logger_name = "odoo.addons.oteny_knowledge_sync.models.knowledge_sync"
        with self.assertLogs(logger_name, level="DEBUG") as cm:
            self.env["oteny.knowledge.sync"]._rewrite_internal_links("Test", excluded_paths)

        offending = [
            r
            for r in cm.records
            if r.levelname == "WARNING" and excluded_href in r.getMessage()
        ]
        self.assertEqual(
            offending,
            [],
            f"No warning should fire for links to agent-only skills. "
            f"Got: {[r.getMessage() for r in offending]}",
        )

        # Link preserved verbatim — not rewritten to a Knowledge URL.
        agentonly_ref.invalidate_recordset(["body"])
        self.assertIn(
            excluded_href,
            agentonly_ref.body,
            "Agent-only skill href should be preserved unchanged in the article body",
        )

    def test_percent_encoded_agent_only_link_no_warning(self):
        """A file name with a space or an em dash is percent-encoded in the href.

        The target is agent-only, so the path is in the exclusion set as it is
        on disk. The rewriter must decode the href before that comparison, leave
        the link as-is, and not warn.
        """
        filename = "Meeting 2026-09-30 — summary.md"
        encoded_href = "../notes/" + quote(filename, safe="")
        disk_path = f"skills/notes/{filename}"
        source = self._managed_article(
            "test-link-encoded-agentonly",
            f'<p>See <a href="{encoded_href}">Note</a></p>',
            "Test",
            "skills/test-link-encoded-agentonly/SKILL.md",
            parent=self.root,
        )

        logger_name = "odoo.addons.oteny_knowledge_sync.models.knowledge_sync"
        with self.assertLogs(logger_name, level="DEBUG") as cm:
            self.env["oteny.knowledge.sync"]._rewrite_internal_links(
                "Test", {disk_path}
            )

        offending = [
            r
            for r in cm.records
            if r.levelname == "WARNING" and "Meeting 2026-09-30" in r.getMessage()
        ]
        self.assertEqual(
            offending,
            [],
            "A percent-encoded link to an agent-only file must not warn. "
            f"Got: {[r.getMessage() for r in offending]}",
        )
        source.invalidate_recordset(["body"])
        self.assertIn(
            encoded_href,
            source.body,
            "The encoded href should stay unchanged when the target is agent-only",
        )

    def test_percent_encoded_href_resolves_to_article(self):
        """The same encoded file name resolves when the target is published."""
        filename = "Meeting 2026-09-30 — summary.md"
        encoded_href = "../notes/" + quote(filename, safe="")
        target = self._managed_article(
            "test-link-encoded-target",
            "<p>Note</p>",
            "Test",
            f"skills/notes/{filename}",
            parent=self.root,
        )
        source = self._managed_article(
            "test-link-encoded-source",
            f'<p>See <a href="{encoded_href}">Note</a></p>',
            "Test",
            "skills/test-link-encoded-source/SKILL.md",
            parent=self.root,
        )

        logger_name = "odoo.addons.oteny_knowledge_sync.models.knowledge_sync"
        with self.assertLogs(logger_name, level="DEBUG") as cm:
            self.env["oteny.knowledge.sync"]._rewrite_internal_links("Test")

        offending = [
            r
            for r in cm.records
            if r.levelname == "WARNING" and "Meeting 2026-09-30" in r.getMessage()
        ]
        self.assertEqual(
            offending,
            [],
            "A percent-encoded link to a published article must not warn. "
            f"Got: {[r.getMessage() for r in offending]}",
        )
        source.invalidate_recordset(["body"])
        self.assertIn(
            f"/knowledge/article/{target.id}",
            source.body,
            "A percent-encoded href must become a Knowledge URL",
        )

    def test_percent_encoded_missing_file_still_warns(self):
        """Decoding must not hide a real miss. An encoded name that no root
        published, and that is not agent-only, still warns."""
        filename = "does not exist.md"
        encoded_href = "references/" + quote(filename, safe="")
        self._managed_article(
            "test-link-encoded-broken",
            f'<p>See <a href="{encoded_href}">Missing</a></p>',
            "Test",
            "skills/test-link-encoded-broken/SKILL.md",
            parent=self.root,
        )

        logger_name = "odoo.addons.oteny_knowledge_sync.models.knowledge_sync"
        with self.assertLogs(logger_name, level="WARNING") as cm:
            self.env["oteny.knowledge.sync"]._rewrite_internal_links("Test")

        matched = [r for r in cm.records if filename in r.getMessage()]
        self.assertTrue(
            matched,
            "Expected a WARNING that names the decoded file, "
            f"but none mentioned {filename!r}. Got: {[r.getMessage() for r in cm.records]}",
        )

    def test_internal_broken_link_still_warns(self):
        """Guard against over-suppression: an in-tree .md link to a file that
        doesn't exist (typo, deleted file, etc.) MUST still emit a warning so
        authoring bugs surface."""
        Article = self.env["knowledge.article"]
        admin_partner = self.env.ref("base.partner_admin")
        member_vals = [(0, 0, {"partner_id": admin_partner.id, "permission": "write"})]

        broken_href = "references/does-not-exist-typo.md"
        Article.create(
            {
                "name": "test-link-broken-internal",
                "body": f'<p>See <a href="{broken_href}">Typo</a></p>',
                "parent_id": self.root.id,
                "is_locked": True,
                "internal_permission": "read",
                "article_member_ids": member_vals,
                "x_skill_is_managed": True,
                "x_skill_root": "Test",
                "x_skill_file_path": "skills/test-link-broken-internal/SKILL.md",
            }
        )

        logger_name = "odoo.addons.oteny_knowledge_sync.models.knowledge_sync"
        with self.assertLogs(logger_name, level="WARNING") as cm:
            self.env["oteny.knowledge.sync"]._rewrite_internal_links("Test")

        matched = [r for r in cm.records if broken_href in r.getMessage()]
        self.assertTrue(
            matched,
            "Expected a WARNING for a typo'd in-tree .md link, "
            f"but none mentioned {broken_href!r}. Got: {[r.getMessage() for r in cm.records]}",
        )

    def _managed_article(self, name, body, knowledge_root, file_path, parent=None):
        admin_partner = self.env.ref("base.partner_admin")
        return self.env["knowledge.article"].create(
            {
                "name": name,
                "body": body,
                "parent_id": parent.id if parent else False,
                "is_locked": True,
                "internal_permission": "read",
                "article_member_ids": [(0, 0, {"partner_id": admin_partner.id, "permission": "write"})],
                "x_skill_is_managed": True,
                "x_skill_root": knowledge_root,
                "x_skill_file_path": file_path,
            }
        )

    def test_cross_root_sibling_name_becomes_knowledge_link(self):
        """A CrewRadar article that names a sibling skill in another configured
        knowledge root becomes a Knowledge link. The old one-tree href
        (``../test-xroot-target/SKILL.md``) is enough. No WARNING.
        """
        target = self._managed_article(
            "test-xroot-target",
            "<p>Oteny Odoo skill</p>",
            "OtenyOdoo",
            "skills/test-xroot-target/SKILL.md",
        )
        source = self._managed_article(
            "test-xroot-source",
            '<p>See <a href="../test-xroot-target/SKILL.md">Target</a></p>',
            "Crewradar",
            "skills/test-xroot-source/SKILL.md",
        )

        logger_name = "odoo.addons.oteny_knowledge_sync.models.knowledge_sync"
        with self.assertLogs(logger_name, level="DEBUG") as cm:
            self.env["oteny.knowledge.sync"]._rewrite_internal_links("Crewradar")

        offending = [
            r for r in cm.records
            if r.levelname == "WARNING" and "test-xroot-target" in r.getMessage()
        ]
        self.assertEqual(
            offending,
            [],
            "A healthy two-root sibling name must not log "
            f"'Could not resolve link'. Got: {[r.getMessage() for r in offending]}",
        )
        source.invalidate_recordset(["body"])
        self.assertIn(
            f"/knowledge/article/{target.id}",
            source.body,
            "Sibling name in another configured knowledge root must become a Knowledge URL",
        )

    def test_cross_root_missing_target_still_warns(self):
        """A sibling name that no configured knowledge root published is still a miss."""
        missing_href = "../test-xroot-missing/SKILL.md"
        self._managed_article(
            "test-xroot-broken",
            f'<p>See <a href="{missing_href}">Missing</a></p>',
            "Crewradar",
            "skills/test-xroot-broken/SKILL.md",
        )

        logger_name = "odoo.addons.oteny_knowledge_sync.models.knowledge_sync"
        with self.assertLogs(logger_name, level="WARNING") as cm:
            self.env["oteny.knowledge.sync"]._rewrite_internal_links("Crewradar")

        matched = [r for r in cm.records if missing_href in r.getMessage()]
        self.assertTrue(
            matched,
            "Expected a WARNING for a sibling name no root published, "
            f"but none mentioned {missing_href!r}. Got: {[r.getMessage() for r in cm.records]}",
        )

    def test_escape_path_into_other_root_becomes_knowledge_link(self):
        """``../../oteny_odoo/.claude/skills/…`` is not required, but when an
        author already wrote it the sync still makes a Knowledge link.
        """
        target = self._managed_article(
            "test-xroot-escape-target",
            "<p>Oteny Odoo skill</p>",
            "OtenyOdoo",
            "skills/test-xroot-escape-target/SKILL.md",
        )
        escape_href = "../../../oteny_odoo/.claude/skills/test-xroot-escape-target/SKILL.md"
        source = self._managed_article(
            "test-xroot-escape-source",
            f'<p>See <a href="{escape_href}">Target</a></p>',
            "Crewradar",
            "skills/test-xroot-escape-source/SKILL.md",
        )

        logger_name = "odoo.addons.oteny_knowledge_sync.models.knowledge_sync"
        with self.assertLogs(logger_name, level="DEBUG") as cm:
            self.env["oteny.knowledge.sync"]._rewrite_internal_links("Crewradar")

        offending = [
            r for r in cm.records
            if r.levelname == "WARNING" and escape_href in r.getMessage()
        ]
        self.assertEqual(offending, [], f"Got: {[r.getMessage() for r in offending]}")
        source.invalidate_recordset(["body"])
        self.assertIn(f"/knowledge/article/{target.id}", source.body)

    def test_same_knowledge_root_wins_on_path_collision(self):
        """When two configured roots publish the same relative skill path, the
        article's own knowledge root wins.
        """
        own_target = self._managed_article(
            "test-xroot-dup-own",
            "<p>Own</p>",
            "Crewradar",
            "skills/test-xroot-dup/SKILL.md",
        )
        other_target = self._managed_article(
            "test-xroot-dup-other",
            "<p>Other</p>",
            "OtenyOdoo",
            "skills/test-xroot-dup/SKILL.md",
        )
        source = self._managed_article(
            "test-xroot-dup-source",
            '<p>See <a href="../test-xroot-dup/SKILL.md">Dup</a></p>',
            "Crewradar",
            "skills/test-xroot-dup-source/SKILL.md",
        )

        self.env["oteny.knowledge.sync"]._rewrite_internal_links("Crewradar")
        source.invalidate_recordset(["body"])
        self.assertIn(f"/knowledge/article/{own_target.id}", source.body)
        self.assertNotIn(f"/knowledge/article/{other_target.id}", source.body)

    def test_skills_tree_suffix_from_escaped_path(self):
        """An escaped href that still names a skill file yields a ``skills/`` tail."""
        Sync = self.env["oteny.knowledge.sync"]
        self.assertEqual(
            Sync._skills_tree_suffix("oteny_odoo/.claude/skills/odoo-development/SKILL.md"),
            "skills/odoo-development/skill.md",
        )
        self.assertEqual(
            Sync._skills_tree_suffix("skills/already-in-tree.md"),
            "skills/already-in-tree.md",
        )
        self.assertIsNone(Sync._skills_tree_suffix("commands/profile.md"))
        self.assertIsNone(Sync._skills_tree_suffix("../../riverdeploy/README.md"))
