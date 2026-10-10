"""Mark the transitions that close a child service that is not started
(radar pipeline applicants plan, Q162).

The Cancel transition from Not started of Task, Taxi Supply Order and Train
Ticket, and the Not Needed transition from Not Started of Send Email, get
execute_on_parent_transitions "cancel". A child service in that state executes
the transition by itself when its parent service enters a state with
execute_child_transitions "cancel". The workflow files are noupdate, so the
value is written here, as in the data files. No riverflow state sets the parent
side: a module that uses the workflows decides which of its states end the
case.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

TRANSITIONS = (
    "riverflow.trans_task_not_started_to_cancelled",
    "riverflow.trans_taxi_not_started_to_cancelled",
    "riverflow.trans_train_not_started_to_cancelled",
    "riverflow.trans_se_5",
)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    marked = []
    for xml_id in TRANSITIONS:
        transition = env.ref(xml_id, raise_if_not_found=False)
        if transition and transition.execute_on_parent_transitions != "cancel":
            transition.execute_on_parent_transitions = "cancel"
            marked.append(xml_id)
    _logger.info("19.0.1.1320: transitions marked to execute when the parent's case ends: %s", marked)
