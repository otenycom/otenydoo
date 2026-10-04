"""Check results on a service: the riverflow mechanism, driven through its hooks."""
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "riverflow", "test_service_check_results")
class TestServiceCheckResults(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        service_model = cls.env["ir.model"]._get("riverflow.service")
        cls.workflow = cls.env["riverflow.workflow"].create({"model_id": service_model.id, "name": "Check WF"})
        State = cls.env["riverflow.state"]
        cls.state_a = State.create({"workflow_id": cls.workflow.id, "name": "A", "sequence": 10})
        cls.state_b = State.create({"workflow_id": cls.workflow.id, "name": "B", "sequence": 20})
        cls.state_end = State.create(
            {"workflow_id": cls.workflow.id, "name": "End", "sequence": 30, "is_end_state": True}
        )
        cls.service = cls.env["riverflow.service"].create(
            {"name": "Checked service", "workflow_id": cls.workflow.id, "state_id": cls.state_a.id}
        )
        # riverflow defines no check type of its own; an installed app adds them.
        values = cls.env["riverflow.check.result"]._fields["check_type"].get_values(cls.env)
        cls.check_type = values[0] if values else None

    def setUp(self):
        super().setUp()
        if not self.check_type:
            self.skipTest("no check type installed")

    def _producer(self, results):
        """Patch the three hooks: this service is claimed, with ``results``."""
        Service = type(self.service)
        check_type = self.check_type
        service = self.service

        def results_for(records):
            return [dict(r, service_id=service.id) for r in results] if service in records else []

        return [
            patch.object(Service, "_get_services_with_checks", lambda s: s & service),
            patch.object(Service, "_get_service_check_types", lambda s: [check_type]),
            patch.object(Service, "_get_service_check_results", results_for),
        ]

    def test_a_result_follows_the_producer_and_routes_to_the_radar_row(self):
        note = {"check_type": self.check_type, "severity": "warning", "name": "A note"}
        patches = self._producer([note])
        for p in patches:
            p.start()
        try:
            self.service.state_id = self.state_b
            result = self.service.check_result_ids
            self.assertEqual(len(result), 1)
            self.assertEqual(result.name, "A note")
            self.assertEqual(result.state_record_id, self.service.state_record_id, "shows on the Radar row")

            self.service.state_id = self.state_a
            self.assertEqual(self.service.check_result_ids, result, "an unchanged result keeps its row")
        finally:
            for p in patches:
                p.stop()

        patches = self._producer([])
        for p in patches:
            p.start()
        try:
            self.service.state_id = self.state_end
            self.assertFalse(self.service.check_result_ids, "a resolved result goes")
        finally:
            for p in patches:
                p.stop()

    def test_a_service_no_producer_claims_is_left_alone(self):
        """Without a claim the compute never syncs, so it costs nothing and removes nothing."""
        manual = self.env["riverflow.check.result"].create(
            {"check_type": self.check_type, "severity": "info", "name": "Kept", "service_id": self.service.id}
        )
        Service = type(self.service)
        with patch.object(Service, "_get_services_with_checks", lambda s: s.browse()):
            self.service.state_id = self.state_b
            self.assertIn(manual, self.service.check_result_ids)
