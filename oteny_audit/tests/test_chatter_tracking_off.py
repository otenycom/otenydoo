"""The chatter tracks no field change: the audit log records changes instead.

oteny_audit sets ``tracking_disable`` on every MailThread create and write.
A stored computed or related field is not written there: it is recomputed at
the flush, in the env of whoever flushes, and MailThread._compute_field_value
then prepares its tracking. A change of a ship's operator wrote the tracked
related fields Operator Name and Operator Email to the chatter, while the
change of the operator itself was not tracked (crmain, 19-Nov-2026).
"""

from unittest.mock import patch

from odoo.addons.mail.models.mail_thread import MailThread
from odoo.tests import TransactionCase, tagged


@tagged("oteny_audit", "post_install", "-at_install")
class TestChatterTrackingOff(TransactionCase):
    def test_recompute_at_the_flush_prepares_no_tracking(self):
        company = self.env["res.partner"].create({"name": "Parent Company", "is_company": True})
        person = self.env["res.partner"].create({"name": "Person"})
        self.env.flush_all()

        with patch.object(MailThread, "_track_prepare", autospec=True) as track_prepare:
            # commercial_partner_id is a stored computed field: it is
            # recomputed at the flush, outside the write.
            person.write({"parent_id": company.id})
            self.env.flush_all()

        self.assertEqual(person.commercial_partner_id, company)
        track_prepare.assert_not_called()
