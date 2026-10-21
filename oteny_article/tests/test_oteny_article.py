from odoo.exceptions import AccessError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged


@tagged("oteny_article", "post_install", "-at_install")
class TestOtenyArticleTree(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Article = cls.env["oteny.article"]
        cls.root = Article.create({"name": "Root"})
        cls.child = Article.create({"name": "Child", "parent_id": cls.root.id})
        cls.grandchild = Article.create({"name": "Grandchild", "parent_id": cls.child.id})
        cls.other_root = Article.create({"name": "Other Root"})

    def test_root_article_follows_the_tree(self):
        """Every article knows the top of its tree, and a move to another tree
        updates the whole branch through @api.depends."""
        self.assertEqual(self.root.root_article_id, self.root)
        self.assertEqual(self.child.root_article_id, self.root)
        self.assertEqual(self.grandchild.root_article_id, self.root)
        self.assertEqual(self.root.child_ids, self.child)

        self.child.parent_id = self.other_root
        self.assertEqual(self.child.root_article_id, self.other_root)
        self.assertEqual(self.grandchild.root_article_id, self.other_root)
        found = self.env["oteny.article"].search([("id", "child_of", self.other_root.id)])
        self.assertEqual(found, self.other_root | self.child | self.grandchild)

        self.child.parent_id = False
        self.assertEqual(self.grandchild.root_article_id, self.child)

    def test_internal_user_rights(self):
        """An internal user reads, writes and creates articles (HR edits the
        parsing prompts), but only an administrator deletes one."""
        user = new_test_user(self.env, login="article_user", groups="base.group_user")
        Article = self.env["oteny.article"].with_user(user)
        article = Article.create({"name": "Prompt", "parent_id": self.root.id})
        article.body = "<p>Rule</p>"
        self.assertIn("Rule", str(article.body))
        with self.assertRaises(AccessError):
            article.unlink()
        article.sudo().unlink()
        self.assertFalse(article.exists())

    def test_article_has_no_chatter(self):
        """An article is a document, not a conversation: the model keeps no
        messages or followers, and the form shows no chatter."""
        Article = self.env["oteny.article"]
        self.assertNotIn("message_ids", Article._fields)
        self.assertNotIn("message_follower_ids", Article._fields)
        self.assertNotIn("<chatter", Article.get_view(view_type="form")["arch"])


@tagged("oteny_article", "post_install", "-at_install")
class TestOtenyArticleKnowledgeLinks(HttpCase):
    def test_knowledge_link_opens_the_same_article(self):
        """A link of the former Knowledge app opens the article with that id."""
        article = self.env["oteny.article"].create({"name": "Linked"})
        self.authenticate("admin", "admin")
        response = self.url_open(f"/knowledge/article/{article.id}", allow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertTrue(
            response.headers["Location"].endswith(
                f"/odoo/action-oteny_article.action_oteny_article/{article.id}"
            )
        )
