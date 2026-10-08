from odoo import api, fields, models


class Service(models.Model):
    """Check results on a service: notes that tell the person working the service
    something about it, shown as Issue tags on the service form and on its Radar row.

    riverflow owns the mechanism; an app module produces the checks through three hooks:

    - ``_get_services_with_checks``: the services it produces checks for. The compute
      does nothing (and never searches) for a service no producer claims, so a service
      without checks costs nothing.
    - ``_get_service_check_types``: the check types the service compute owns. The sync
      only reconciles those, never a result another compute owns.
    - ``_get_service_check_results``: the result dicts (check_type, severity, name,
      service_id).

    The base dependencies are only the service's own state; a producer adds the
    dependencies of its rule by overriding ``_compute_check_result_ids`` (Odoo merges
    the ``@api.depends`` of all overrides). Keep them narrow: a wide trigger recomputes
    services in the middle of other flows.
    """

    _name = "riverflow.service"
    _inherit = ["riverflow.service", "riverflow.check.results.mixin"]

    check_result_ids = fields.One2many(
        "riverflow.check.result",
        "service_id",
        string="Issues",
        compute="_compute_check_result_ids",
        store=True,
    )

    @api.depends("active", "state_id.is_end_state")
    def _compute_check_result_ids(self):
        if not self.env.registry.loaded:
            # don't recalc on startup; a migration calls the impl directly
            return
        if any(isinstance(rec.id, api.NewId) for rec in self):
            return
        self._compute_check_result_ids_impl()

    def _compute_check_result_ids_impl(self):
        services = self._get_services_with_checks()
        check_types = self._get_service_check_types()
        if not services or not check_types:
            return
        new_check_results = services._get_service_check_results()
        existing_check_results = self.env["riverflow.check.result"].search(
            [("service_id", "in", services.ids), ("check_type", "in", check_types), ("to_be_deleted", "=", False)]
        )
        services._sync_check_results(existing_check_results, new_check_results, index_field="service_id")

    def _get_services_with_checks(self):
        """The services of ``self`` a producer makes check results for. Base: none."""
        return self.browse()

    def _get_service_check_types(self):
        """The check types the service compute owns. Base: none."""
        return []

    def _get_service_check_results(self):
        """Check-result dicts for ``self`` (the services claimed above). Base: none."""
        return []
