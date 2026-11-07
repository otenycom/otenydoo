# -*- coding: utf-8 -*-
from odoo import api, fields, models


class OtenyAuditTestParent(models.Model):
    _name = "oteny.audit.test.parent"
    _description = "Oteny Audit Test Parent Model"
    _oteny_audit_ignore = False

    name = fields.Char()
    # Computed field with inverse that simulates the company_id behavior during creation
    computed_field = fields.Char(
        compute="_compute_computed_field",
        inverse="_inverse_computed_field",
        store=True,
        string="Computed Field",
    )
    child_ids = fields.One2many("oteny.audit.test.child", "parent_id")

    @api.depends("name")
    def _compute_computed_field(self):
        """Compute method that will be triggered during creation when name is set"""
        for record in self:
            if record.name:
                record.computed_field = f"Computed: {record.name}"
            else:
                record.computed_field = False

    def _inverse_computed_field(self):
        """Inverse method - required for the field to be properly audited"""
        # In real scenarios, this might update related fields
        # For testing, we just pass
        pass


class OtenyAuditTestChild(models.Model):
    _name = "oteny.audit.test.child"
    _description = "Oteny Audit Test Child Model"
    _oteny_audit_parent_field = "parent_id"
    _oteny_audit_ignore = False

    name = fields.Char()
    parent_id = fields.Many2one("oteny.audit.test.parent", ondelete="cascade")


class OtenyAuditTestHtml(models.Model):
    """Test model exercising Measure 2: HTML stripping for selected fields.

    `body` is listed in `_oteny_audit_html_strip_fields` so audit logs store a
    plain-text projection. `note` is a regular Html field that retains its
    full markup in audit logs (acts as a control to prove stripping is opt-in
    and per-field).
    """

    _name = "oteny.audit.test.html"
    _description = "Oteny Audit Test HTML Strip Model"
    _oteny_audit_ignore = False
    _oteny_audit_html_strip_fields = {"body"}

    name = fields.Char()
    body = fields.Html(string="Body (stripped)")
    note = fields.Html(string="Note (unstripped)")


class OtenyAuditTestSecret(models.Model):
    """Test model exercising secret redaction, one field per rule it has to prove.

    - `access_token` — caught by the NAME shape, no declaration needed.
    - `vault_handle` — caught by nothing, and redacted only because the class declares it
      in `_oteny_audit_redact_fields`. The declaration must carry a name the shape misses,
      or the test proves nothing.
    - `stripe_secret_key` — caught by the marker word `secret` in the MIDDLE of the name.
      An exact-name-plus-suffix rule missed this whole family.
    - `ssl_private_key` — a Binary. The type gate must NOT drop it: the allow-list it
      replaced let `ir.mail_server.smtp_ssl_private_key` write a full PEM to the audit log.
    - `token_payload` — a Json holding a credential.
    - `retry_token` — matches the name shape but is an Integer, so the type gate must let
      it through. A counter is not a credential.
    - `cache_key`, `name` — controls. `cache_key` is in `_NOT_SECRET_FIELD_NAMES`, so the
      `_key` suffix must not reach it.
    """

    _name = "oteny.audit.test.secret"
    _description = "Oteny Audit Test Secret Redaction Model"
    _oteny_audit_ignore = False
    _oteny_audit_redact_fields = {"vault_handle"}

    name = fields.Char()
    access_token = fields.Char(string="Access Token")
    vault_handle = fields.Char(string="Vault Handle")
    stripe_secret_key = fields.Char(string="Stripe Secret Key")
    ssl_private_key = fields.Binary(string="SSL Private Key", attachment=False)
    token_payload = fields.Json(string="Token Payload")
    retry_token = fields.Integer(string="Retry Token")
    cache_key = fields.Char(string="Cache Key")


class OtenyAuditTestCoverageMixin(models.AbstractModel):
    """A mixin that declares its own audit-ignored field: the model's own declaration must
    not hide it (the sets of all classes are joined)."""

    _name = "oteny.audit.test.coverage.mixin"
    _description = "Oteny Audit Test Coverage Mixin"
    _oteny_audit_ignore_fields = {"mixin_note"}

    mixin_note = fields.Char()


class OtenyAuditTestCoverage(models.Model):
    """Test model for the coverage rules of 19.0.1.723 (plan audit-storage-reduction.md):
    cut I (`code_state`, read-only without a compute, is logged; `name_upper`, a read-only
    compute, is not), the read-only `active`, `parent_path`, rule N1 (`flag`, `count` without
    a default; `flag_on` with one) and `_oteny_audit_ignore_fields` (`skipped_note`, and the
    mixin's `mixin_note`)."""

    _name = "oteny.audit.test.coverage"
    _description = "Oteny Audit Test Coverage Model"
    _inherit = ["oteny.audit.test.coverage.mixin"]
    _oteny_audit_ignore = False
    _oteny_audit_ignore_fields = {"skipped_note"}
    _parent_store = True

    name = fields.Char()
    code_state = fields.Char(readonly=True)
    name_upper = fields.Char(compute="_compute_name_upper", store=True)
    active = fields.Boolean(default=True, readonly=True)
    skipped_note = fields.Char()
    flag = fields.Boolean()
    flag_on = fields.Boolean(default=True)
    count = fields.Integer()
    parent_id = fields.Many2one("oteny.audit.test.coverage", index=True)
    parent_path = fields.Char(index=True)
    tag_ids = fields.Many2many("oteny.audit.test.child")
    kind = fields.Selection([("alpha", "Alpha kind"), ("beta", "Beta kind")])

    @api.depends("name")
    def _compute_name_upper(self):
        for record in self:
            record.name_upper = (record.name or "").upper()
