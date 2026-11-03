"""Post-migration 19.0.1.1393 — Send Notification closes by itself when its
parent ends the case.

The Not Needed transition from Not Started of Send Notification
(trans_sn_not_needed) gets execute_on_parent_transitions = cancel, as Send
Email's has (trans_se_5): a notification that nobody sent closes when its parent
service enters a state that ends the case, and a sent one stays (radar,
Ries, 3-Nov-2026: the system cancels only services that are not started). The
workflow file is noupdate, so this writes the mark on existing databases.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    transition = env.ref("riverflow.trans_sn_not_needed", raise_if_not_found=False)
    if transition and not transition.execute_on_parent_transitions:
        transition.execute_on_parent_transitions = "cancel"
        _logger.info("19.0.1.1393: Send Notification Not Needed closes by itself when the parent ends the case")
