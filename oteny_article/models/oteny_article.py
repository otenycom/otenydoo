from odoo import api, fields, models


class OtenyArticle(models.Model):
    """An HTML article in a tree of articles.

    Holds what the Enterprise Knowledge app held for radar: the parsing prompts
    of the credential types, the planning rules, and the skill files that the
    skill sync publishes for key users. Rights are plain model rights: internal
    users read, write and create; only administrators delete. Articles that
    code or module data own are locked, and the form shows them read-only.
    """

    _name = "oteny.article"
    _description = "Article"
    _inherit = ["html.field.history.mixin"]
    _parent_store = True
    _order = "sequence, id"

    name = fields.Char(required=True)
    icon = fields.Char(string="Emoji")
    body = fields.Html(prefetch=False)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    is_locked = fields.Boolean(
        string="Locked",
        help="Owned by code or module data (for example the skill sync). The form "
        "shows a locked article read-only; extend it with a child article instead.",
    )
    parent_id = fields.Many2one(
        "oteny.article",
        string="Parent Article",
        index=True,
        ondelete="cascade",
    )
    child_ids = fields.One2many("oteny.article", "parent_id", string="Child Articles")
    parent_path = fields.Char(index=True)
    root_article_id = fields.Many2one(
        "oteny.article",
        string="Root Article",
        compute="_compute_root_article_id",
        store=True,
        recursive=True,
    )

    def _get_versioned_fields(self):
        """Keep a revision history of the body, as the Knowledge app did."""
        return [OtenyArticle.body.name]

    @api.depends("parent_id.root_article_id")
    def _compute_root_article_id(self):
        for article in self:
            article.root_article_id = article.parent_id.root_article_id or article

    @api.model
    def action_open_articles(self):
        """The Articles menu: the form of the first top-level article.

        The form shows the whole tree in its left column, so the menu opens on
        an article instead of a list. The first article is the top-level one
        with the lowest sequence; without any article, the list opens.
        """
        action = self.env["ir.actions.act_window"]._for_xml_id("oteny_article.action_oteny_article")
        first = self.search([("parent_id", "=", False)], limit=1)
        if first:
            action.update(res_id=first.id, view_mode="form", views=[(False, "form")], context={})
        return action
