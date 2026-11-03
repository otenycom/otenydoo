"""Post-migration 19.0.1.1394 — renumber display_order in the trees that hold
more than one subject.

display_order used to be numbered per subject (res_model, res_id). In a tree
with services of two subjects (a work permit on the employee with a train
booking on a log entry), a service whose parent has another subject was left
out of the walk and kept a stale number, so the Child Services list showed it
apart from its tree. The compute now numbers the whole tree; this renumbers the
trees that the old numbering got wrong. A tree of one subject was numbered in
tree order already and keeps its numbers.

The state records copy display_order from their service. The tree walk stores
the new numbers on the queued services without the triggers of a write, so
their state records are queued as well.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute(
        """
        SELECT root_id FROM riverflow_service
        WHERE root_id IS NOT NULL AND NOT to_be_deleted
        GROUP BY root_id
        HAVING COUNT(DISTINCT (COALESCE(res_model, ''), COALESCE(res_id, 0))) > 1
        """
    )
    root_ids = [row[0] for row in cr.fetchall()]
    if not root_ids:
        return
    env = api.Environment(cr, SUPERUSER_ID, {"active_test": False})
    Service = env["riverflow.service"]
    StateRecord = env["riverflow.state.record"]
    services = Service.search([("root_id", "in", root_ids)])
    env.add_to_compute(Service._fields["display_order"], services)
    env.add_to_compute(StateRecord._fields["display_order"], StateRecord.search([("service_id", "in", services.ids)]))
    env.flush_all()
    _logger.info(
        "19.0.1.1394: display_order renumbered in %d trees with more than one subject (%d services)",
        len(root_ids),
        len(services),
    )
