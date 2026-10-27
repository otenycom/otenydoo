from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "riverflow", "test_service_company")
class TestServiceCompany(TransactionCase):
    """The company of a service follows its parent, else its subject, else the selected company.

    Every test creates services while another company is selected than the one expected, so a
    service that takes the selected company by mistake fails the test.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_subject = cls.env.company
        cls.company_selected = cls.env["res.company"].create({"name": "Selected Test Company"})
        cls.env.user.company_ids |= cls.company_selected
        cls.partner_with_company = cls.env["res.partner"].create(
            {"name": "Subject With Company", "company_id": cls.company_subject.id}
        )
        cls.partner_other_company = cls.env["res.partner"].create(
            {"name": "Subject Other Company", "company_id": cls.company_selected.id}
        )
        cls.partner_without_company = cls.env["res.partner"].create({"name": "Subject Without Company"})

    def _service_model(self):
        """The service model with the other company selected first, as in the company switcher."""
        return self.env["riverflow.service"].with_context(
            allowed_company_ids=[self.company_selected.id, self.company_subject.id]
        )

    def test_standalone_service_takes_the_selected_company(self):
        service = self._service_model().create({"name": "Standalone"})
        self.assertEqual(service.company_id, self.company_selected)

    def test_service_takes_the_subject_company(self):
        service = self._service_model().create(
            {"name": "On subject", "res_model": "res.partner", "res_id": self.partner_with_company.id}
        )
        self.assertEqual(service.company_id, self.company_subject)

    def test_subject_without_company_falls_back_to_the_selected_company(self):
        service = self._service_model().create(
            {"name": "On subject", "res_model": "res.partner", "res_id": self.partner_without_company.id}
        )
        self.assertEqual(service.company_id, self.company_selected)

    def test_service_follows_a_new_subject(self):
        service = self._service_model().create(
            {"name": "On subject", "res_model": "res.partner", "res_id": self.partner_with_company.id}
        )
        service.res_id = self.partner_other_company.id
        self.assertEqual(service.company_id, self.company_selected)

    def test_service_keeps_its_company_when_the_new_subject_has_none(self):
        service = self._service_model().create(
            {"name": "On subject", "res_model": "res.partner", "res_id": self.partner_with_company.id}
        )
        # The compute reads the old company from the database, as in a later request
        service.flush_recordset()
        service.res_id = self.partner_without_company.id
        self.assertEqual(service.company_id, self.company_subject)

    def test_child_takes_and_follows_the_parent_company(self):
        root = self._service_model().create(
            {"name": "Root", "res_model": "res.partner", "res_id": self.partner_with_company.id}
        )
        child = self._service_model().create({"name": "Child", "parent_id": root.id})
        self.assertEqual(child.company_id, self.company_subject)

        # The parent wins over the subject: the child keeps the parent's subject, not its company
        root.company_id = self.company_selected
        self.assertEqual(child.company_id, self.company_selected)
        self.assertEqual(child.res_id, self.partner_with_company.id)

    def test_service_from_template_takes_the_subject_company(self):
        template = self.env["riverflow.service"].create(
            {"name": "Template", "is_this_a_template": True, "company_id": self.company_selected.id}
        )
        service = (
            self._service_model()
            .with_context(default_res_model="res.partner", default_res_id=self.partner_with_company.id)
            ._create_services_from_template(template.id)
        )
        self.assertEqual(service.company_id, self.company_subject)
