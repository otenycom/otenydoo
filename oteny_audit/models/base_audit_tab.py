from lxml import etree

from odoo import api, models


class Base(models.AbstractModel):
    """An Audit tab on the form of every audited model that has tabs.

    The tab is the last page of the form's main notebook. It holds the widget
    `oteny_audit_log` (static/src/widgets/audit_log_tab), which reads the newest rows of the
    record's audit log, the same rows as the Action menu's "Audit Log for …", only when the
    user opens the tab, and offers "Full Screen (N)" for the whole list. No field on the model
    is needed (plan oteny-audit/plans/audit-insert-id-only.md, decision 10).

    A model switches the tab off with `_oteny_audit_form_tab = False`. Odoo caches the arch
    per view, so the check below runs once per view and not on every form load.
    """

    _inherit = "base"

    _OTENY_AUDIT_PAGE_NAME = "oteny_audit_log"

    @api.model
    def _get_view(self, view_id=None, view_type="form", **options):
        arch, view = super()._get_view(view_id, view_type, **options)
        if view_type == "form" and self._oteny_audit_form_tab_wanted():
            notebooks = arch.xpath("//sheet//notebook[not(ancestor::notebook)]") or arch.xpath(
                "//notebook[not(ancestor::notebook)]"
            )
            if notebooks and not arch.xpath(f"//page[@name='{self._OTENY_AUDIT_PAGE_NAME}']"):
                page = etree.SubElement(
                    notebooks[0],
                    "page",
                    {"name": self._OTENY_AUDIT_PAGE_NAME, "string": self.env._("Audit"), "invisible": "not id"},
                )
                etree.SubElement(page, "widget", {"name": "oteny_audit_log"})
        return arch, view

    def _oteny_audit_form_tab_wanted(self):
        """True for a stored model whose changes the audit log records, unless it opts out. The
        widget reloads on `write_date`, so a model without the access columns gets no tab."""
        if self._abstract or self._transient or not self._auto or "write_date" not in self._fields:
            return False
        if not getattr(type(self), "_oteny_audit_form_tab", True):
            return False
        return not self.env["oteny.audit.log"].with_context(oteny_audit_ignore=False)._is_audit_ignored(self._name)
