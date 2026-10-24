"""One Radar row per record and row type (radar plan
crewradar-development/plans/one-radar-row-per-subject.md).

- The unique index refuses a second row with the same master_model,
  master_res_id and record_type (F2).
- The lookup of _compute_state_record_id finds the archived row of an archived
  record: it makes no second row and writes the link (F1, cause C3).

The record is a service, the model of riverflow that keeps a Radar row, so the
tests hold for every app that uses the tracker mixin.
"""

from psycopg2 import IntegrityError

from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger


@tagged("post_install", "-at_install", "riverflow", "test_state_record_unique")
class TestStateRecordUnique(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        service_model = cls.env["ir.model"]._get("riverflow.service")
        workflow = cls.env["riverflow.workflow"].create({"model_id": service_model.id, "name": "Radar Row WF"})
        state = cls.env["riverflow.state"].create({"workflow_id": workflow.id, "name": "Open", "sequence": 10})
        cls.service = cls.env["riverflow.service"].create(
            {"name": "Radar row", "workflow_id": workflow.id, "state_id": state.id}
        )
        cls.StateRecord = cls.env["riverflow.state.record"].with_context(active_test=False)

    def _rows(self):
        return self.StateRecord.search(
            [("master_model", "=", "riverflow.service"), ("master_res_id", "=", self.service.id)]
        )

    def test_second_row_of_one_type_is_refused(self):
        """A second row for the same record and row type is refused by the
        database; the record keeps its one row."""
        row = self.service.state_record_id
        self.assertEqual(self._rows(), row)
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError), self.env.cr.savepoint():
            self.StateRecord.create(
                {"master_model": "riverflow.service", "master_res_id": self.service.id, "record_type": "single"}
            )
            self.env.flush_all()
        self.assertEqual(self._rows(), row)

    def test_lookup_finds_the_archived_row(self):
        """An archived record whose link is computed again keeps its one
        archived row and links to it; the lookup used to miss the archived row
        and make a second one."""
        row = self.service.state_record_id
        self.service.active = False
        self.env.flush_all()
        self.assertFalse(row.active)

        self.env.add_to_compute(self.service._fields["state_record_id"], self.service)
        self.assertEqual(self.service.state_record_id, row)
        self.assertEqual(self._rows(), row)
