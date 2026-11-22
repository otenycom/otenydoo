from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    oteny_knowledge_sync_roots = fields.Char(
        string="Skill roots",
        config_parameter="oteny_knowledge_sync.roots",
        help="One root per line as Label=path. The path is a repository folder that "
        "holds .claude/skills, absolute or relative to an addons-path entry. Empty "
        "means every addons-path entry whose parent folder holds .claude/skills. "
        "A root without a label takes the knowledge_root that its "
        ".claude/skills/SKILL.MD declares, else the folder's name.",
    )

    def action_oteny_knowledge_sync_now(self):
        self.ensure_one()
        return self.env["oteny.knowledge.sync"].sync_skills_to_knowledge()
