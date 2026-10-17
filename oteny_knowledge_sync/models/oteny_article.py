from odoo import fields, models


class KnowledgeArticle(models.Model):
    """Mark the articles the skill sync manages, and which root they came from."""

    _inherit = "oteny.article"

    x_skill_file_path = fields.Char(
        string="Skill File Path",
        help="Path of the markdown file this article mirrors, relative to the root's "
        ".claude folder (e.g. skills/my-skill/SKILL.md).",
    )
    x_skill_is_managed = fields.Boolean(
        string="Managed by Skill Sync",
        default=False,
        help="Written and deleted by the skill sync from the source markdown files.",
    )
    x_skill_root = fields.Char(
        string="Skill Root",
        index=True,
        help="The label of the configured root this article belongs to. One article "
        "tree per root.",
    )
