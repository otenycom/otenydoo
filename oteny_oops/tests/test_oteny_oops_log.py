from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.oteny_oops.models.oteny_oops_log import CUT_LINE, MAX_RECORD_BYTES

# The text fields of a record; together they hold at most MAX_RECORD_BYTES.
TEXT_FIELDS = (
    "name", "exception_name", "error_type", "model", "url", "user_agent", "fingerprint", "message", "traceback",
)

SERVER_TRACEBACK = """Traceback (most recent call last):
  File "/srv/odoo/odoo/http.py", line 2329, in _serve_db
    return service_model.retrying(serve_func, env=self.env)
  File "/srv/addons/sample/models/sample_note.py", line 330, in _get_note
    return self.company_id.name.rstrip(".")
AttributeError: 'bool' object has no attribute 'rstrip'

The above server error caused the following client error:
OwlError: An error occured in the owl lifecycle
    at handleError (https://erp.example.com/web/assets/58105ec/web.assets_web.min.js:775:101)
    at App.handleError (https://erp.example.com/web/assets/58105ec/web.assets_web.min.js:1437:29)
"""

CLIENT_TRACEBACK = """TypeError: Cannot read properties of undefined (reading 'id')
    at SampleWidget.onClick (https://erp.example.com/web/assets/58105ec/web.assets_web.min.js:9120:44)
    at HTMLButtonElement.<anonymous> (https://erp.example.com/web/assets/58105ec/web.assets_web.min.js:871:12)
"""


@tagged("oteny_oops", "post_install", "-at_install")
class TestOtenyOopsLog(TransactionCase):
    """The Oops Log: one record per fault and user, at most 1 MB of text, only for
    Settings administrators, deleted when nobody met its fault for the maximum
    age."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Log = cls.env["oteny.oops.log"]
        cls.user = new_test_user(cls.env, login="oops_user", groups="base.group_user")
        cls.other_user = new_test_user(cls.env, login="oops_other", groups="base.group_user")

    def _log(self, user, **values):
        return self.Log._log_error(
            {
                "exception_name": "builtins.AttributeError",
                "message": "'bool' object has no attribute 'rstrip'",
                "traceback": SERVER_TRACEBACK,
                "error_type": "server",
                "model": "sample.note",
                "url": "https://erp.example.com/odoo/notes/7",
                "user_id": user.id,
                "company_id": self.env.company.id,
                "user_agent": "Mozilla/5.0",
                **values,
            }
        )

    def _record(self, days_ago, **values):
        return self.Log.create(
            {
                "name": "builtins.ValueError: old",
                "user_id": self.user.id,
                "last_date": fields.Datetime.now() - timedelta(days=days_ago),
                **values,
            }
        )

    def test_fingerprint_ignores_line_numbers(self):
        """A deploy moves line numbers and asset hashes; the same fault keeps its
        fingerprint. Another function is another fault."""
        fingerprint = self.Log._fingerprint
        moved = SERVER_TRACEBACK.replace("line 2329", "line 2340").replace("line 330", "line 331")
        moved = moved.replace("58105ec", "77aa01f").replace(":775:101", ":780:3")
        self.assertEqual(
            fingerprint("builtins.AttributeError", "", SERVER_TRACEBACK),
            fingerprint("builtins.AttributeError", "", moved),
        )
        other_function = SERVER_TRACEBACK.replace("in _get_note", "in _get_other_note")
        self.assertNotEqual(
            fingerprint("builtins.AttributeError", "", SERVER_TRACEBACK),
            fingerprint("builtins.AttributeError", "", other_function),
        )
        # A client error has no Python frame: its function names count, not the asset URLs.
        self.assertEqual(
            fingerprint("TypeError", "", CLIENT_TRACEBACK),
            fingerprint("TypeError", "", CLIENT_TRACEBACK.replace("58105ec", "77aa01f").replace(":9120:44", ":9200:1")),
        )
        self.assertNotEqual(
            fingerprint("TypeError", "", CLIENT_TRACEBACK),
            fingerprint("TypeError", "", CLIENT_TRACEBACK.replace("SampleWidget.onClick", "SampleWidget.onSave")),
        )

    def test_record_holds_the_dialog_text(self):
        record = self._log(self.user)
        self.assertEqual(record.name, "builtins.AttributeError: 'bool' object has no attribute 'rstrip'")
        self.assertEqual(record.error_type, "server")
        self.assertEqual(record.traceback, SERVER_TRACEBACK)
        self.assertEqual(record.url, "https://erp.example.com/odoo/notes/7")
        self.assertEqual(record.user_id, self.user)
        self.assertEqual(record.occurrence_count, 1)

    def test_same_fault_within_ten_minutes_counts(self):
        first = self._log(self.user)
        first.last_date = fields.Datetime.now() - timedelta(minutes=9)
        second = self._log(self.user)
        self.assertEqual(second, first)
        self.assertEqual(first.occurrence_count, 2)
        self.assertGreater(first.last_date, fields.Datetime.now() - timedelta(minutes=1))

    def test_same_fault_after_ten_minutes_is_new_record(self):
        first = self._log(self.user)
        first.last_date = fields.Datetime.now() - timedelta(minutes=11)
        second = self._log(self.user)
        self.assertNotEqual(second, first)
        self.assertEqual(first.occurrence_count, 1)

    def test_same_fault_other_user_is_new_record(self):
        first = self._log(self.user)
        second = self._log(self.other_user)
        self.assertNotEqual(second, first)
        self.assertEqual(first.fingerprint, second.fingerprint, "the list groups both users under one fault")

    def test_record_is_cut_to_one_megabyte(self):
        """A 3 MiB traceback of two-byte characters: the record holds at most
        1 MiB, counted in bytes; the traceback keeps its start and ends with the
        cut line. A message that is too long gives way after the traceback."""
        record = self._log(self.user, traceback="é" * (3 << 19))
        self.assertLessEqual(sum(len((record[name] or "").encode()) for name in TEXT_FIELDS), MAX_RECORD_BYTES)
        self.assertTrue(record.traceback.startswith("éééé"))
        self.assertTrue(record.traceback.endswith(CUT_LINE))
        self.assertEqual(record.message, "'bool' object has no attribute 'rstrip'")

        record = self._log(self.other_user, message="ü" * (3 << 19), traceback="Traceback\n" * 1000)
        self.assertLessEqual(sum(len((record[name] or "").encode()) for name in TEXT_FIELDS), MAX_RECORD_BYTES)
        self.assertEqual(record.traceback, CUT_LINE)
        self.assertTrue(record.message.startswith("üüüü"))
        self.assertTrue(record.message.endswith(CUT_LINE))

    def test_internal_user_cannot_read(self):
        """A traceback can hold personal data, so only Settings administrators
        read the Oops Log."""
        self._log(self.user)
        with self.assertRaises(AccessError):
            self.Log.with_user(self.user).search([])

    def test_old_records_are_deleted(self):
        """With the default of 30 days, the clean-up deletes the records whose
        fault nobody met for 30 days, archived records too. The age counts from
        the last time, not from the date of the record."""
        old = self._record(31)
        old_archived = self._record(31, active=False)
        recent = self._record(29)
        still_met = self._record(1)
        self.env.cr.execute(
            "UPDATE oteny_oops_log SET create_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(days=40), still_met.id),
        )
        self.Log._gc_old_records()
        self.assertFalse(old.exists())
        self.assertFalse(old_archived.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(still_met.exists())

    def test_max_age_parameter_is_read(self):
        self.env["ir.config_parameter"].sudo().set_param("oteny_oops.max_age_days", "7")
        older = self._record(8)
        younger = self._record(6)
        self.Log._gc_old_records()
        self.assertFalse(older.exists())
        self.assertTrue(younger.exists())

    def test_menu_is_in_oteny_section(self):
        self.assertEqual(
            self.env.ref("oteny_oops.menu_oteny_oops_log").parent_id, self.env.ref("oteny_base.menu_oteny")
        )
