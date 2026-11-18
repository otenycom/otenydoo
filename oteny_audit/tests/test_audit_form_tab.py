# The Audit tab on the form of every audited model that has tabs (models/base_audit_tab.py).
from odoo.tests import TransactionCase, tagged


@tagged("oteny_audit", "post_install", "-at_install")
class TestAuditFormTab(TransactionCase):
    def _arch(self, model_name):
        self.env.registry.clear_cache("templates")
        return self.env[model_name].get_view(view_type="form")["arch"]

    def test_an_audited_form_with_tabs_gets_the_audit_tab_last(self):
        arch = self._arch("res.partner")
        self.assertIn('<page name="oteny_audit_log"', arch)
        self.assertIn('<widget name="oteny_audit_log"', arch)
        self.assertIn('invisible="not id"', arch[arch.index('name="oteny_audit_log"') - 200:])
        self.assertEqual(arch.count('name="oteny_audit_log"'), 2, "one page and one widget, once")
        main_notebook_end = arch.index("</notebook>", arch.index('<page name="oteny_audit_log"'))
        self.assertNotIn("<page", arch[arch.index('<page name="oteny_audit_log"') + 1:main_notebook_end],
                         "the Audit tab is the last page of its notebook")

    def test_an_ignored_model_gets_no_tab(self):
        self.assertTrue(self.env["oteny.audit.log"]._is_audit_ignored("mail.mail"))
        self.assertNotIn("oteny_audit_log", self._arch("mail.mail"))

    def test_a_form_without_tabs_gets_no_tab(self):
        self.assertNotIn("oteny_audit_log", self._arch("oteny.audit.test.parent"))

    def test_a_model_can_switch_the_tab_off(self):
        Partner = type(self.env["res.partner"])
        Partner._oteny_audit_form_tab = False
        self.addCleanup(delattr, Partner, "_oteny_audit_form_tab")
        self.assertNotIn("oteny_audit_log", self._arch("res.partner"))

    def test_the_caption_leaves_out_user_and_time_for_the_tab(self):
        """The tab shows the user and the time once per transaction; the record header of a
        caption leaves them out when the tab asks for it."""
        partner = self.env["res.partner"].create({"name": "Caption probe"})
        self.env.flush_all()
        View = self.env["oteny.audit.log.aggregated"]
        domain = [("model_name", "=", "res.partner"), ("record_id", "=", partner.id)]
        full = View.search(domain).caption
        short = View.with_context(oteny_audit_caption_without_user=True).search(domain).caption
        self.assertIn(" by <b>", full)
        self.assertNotIn(" by <b>", short)
        self.assertIn("Caption probe", short)
