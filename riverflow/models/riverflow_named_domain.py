from odoo import api, fields, models


class NamedDomain(models.Model):
    """A named, reusable domain condition on one model.

    One place for a condition that several configurations need, with a name a
    reader understands ("Employee: in employment or open contract") instead of
    a domain string repeated in every record that uses it. Auto-add rules use it
    to decide which records get a service; other modules point to it for their
    own conditions (e.g. the applicability of a credential type group in
    rivercreds, the extra condition of a salary auto-add component in
    crewradar). Editing a shared domain changes every user of it, so the form
    lists where it is used.

    Until riverflow 19.0.1.1274 this model was riverflow.auto.add.domain; the
    pre-migrate of that version renamed it in place (same table rows, same ids,
    same XML ids).
    """

    _name = "riverflow.named.domain"
    _description = "Named Domain"
    _order = "applies_to_model,name"

    name = fields.Char(required=True, index=True)
    description = fields.Text(help="Human readable description of this domain condition.")
    applies_to_model_id = fields.Many2one(
        "ir.model",
        string="Applies to",
        required=True,
        ondelete="cascade",
    )
    domain = fields.Text(
        required=True,
        default="[]",
        help="Domain expression to evaluate.",
    )
    applies_to_model = fields.Char(
        string="Technical Model Name",
        related="applies_to_model_id.model",
        store=True,
        readonly=True,
    )
    # Where-used: the auto-add rules that evaluate this domain. Other modules
    # add their own users (see rivercreds for credential type groups).
    auto_add_service_ids = fields.One2many(
        "riverflow.auto.add.service",
        "domain_id",
        string="Auto-Add Rules",
    )
