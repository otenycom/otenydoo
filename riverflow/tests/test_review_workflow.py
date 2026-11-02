"""The Review workflow (radar plan crewradar-cuneus/plans/work-permit-change-appointment.md,
decision 6): the services of a review workflow check work, they do not do work.

- The Review workflow is a review workflow; the Task workflow is not.
- Re-start goes from Done back to Not Started.
- Cancel from Not Started is executed by a parent that ends the case.
"""

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("riverflow", "post_install", "-at_install", "test_review_workflow")
class TestReviewWorkflow(TransactionCase):
    def test_review_workflow_is_a_review(self):
        self.assertTrue(self.env.ref("riverflow.workflow_service_review").is_review)
        self.assertFalse(self.env.ref("riverflow.workflow_service_task").is_review)

    def test_restart_goes_from_done_to_not_started(self):
        restart = self.env.ref("riverflow.trans_review_done_to_not_started")
        self.assertEqual(restart.from_state_id, self.env.ref("riverflow.state_review_done"))
        self.assertEqual(restart.to_state_id, self.env.ref("riverflow.state_review_not_started"))

    def test_cancel_is_executed_by_the_parent(self):
        cancel = self.env.ref("riverflow.trans_review_not_started_to_cancelled")
        self.assertEqual(cancel.execute_on_parent_transitions, "cancel")
        self.assertTrue(cancel.to_state_id.is_cancelled_state)
