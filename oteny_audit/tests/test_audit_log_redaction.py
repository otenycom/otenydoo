# test_audit_log_redaction.py
#
# Coverage for secret redaction: a credential must never reach an audit row, on any change
# type, in either storage shape (the flat old_value/new_value columns of an update, and the
# `snapshot` jsonb of an insert or a delete). The audit log is readable and value-searchable
# by every internal user, so this is a security boundary, not a storage optimisation.
#
# Three levers are exercised:
#   1. the field-NAME shape (access_token) -- no declaration needed;
#   2. `_oteny_audit_redact_fields` on the model class (vault_handle);
#   3. `_oteny_audit_redact_record_field` on ir.config_parameter, where the answer depends
#      on the record's `key`, not on the field name.
import base64


from odoo.addons.oteny_audit.models.base_patch import AUDIT_REDACTED, _is_secret_field
from odoo.tests import TransactionCase, tagged

from .common import end_transaction


@tagged("oteny_audit", "post_install", "-at_install")
class TestAuditLogRedaction(TransactionCase):
    """Verify credential values are replaced by a placeholder in the audit log."""

    SECRET = "sk-test-DO-NOT-STORE-0123456789"
    PEM = b"-----BEGIN PRIVATE KEY-----\nDO-NOT-STORE-KEY-MATERIAL\n-----END PRIVATE KEY-----\n"

    def setUp(self):
        super().setUp()
        self.model = self.env["oteny.audit.test.secret"]
        self.log_model = self.env["oteny.audit.log"]

    def _snapshot(self, model_name, record_id, change_type):
        rows = self.log_model.search(
            [
                ("model_name", "=", model_name),
                ("record_id", "=", record_id),
                ("field_name", "=", "__snapshot__"),
                ("change_type", "=", change_type),
            ]
        )
        self.assertEqual(len(rows), 1, f"expected one {change_type} snapshot row")
        return rows.snapshot or {}

    def _update_rows(self, model_name, record_id, field_name):
        return self.log_model.search(
            [
                ("model_name", "=", model_name),
                ("record_id", "=", record_id),
                ("field_name", "=", field_name),
                ("change_type", "=", "u"),
            ]
        )

    def _make(self):
        return self.model.create(
            {
                "name": "Redaction probe",
                "access_token": self.SECRET,
                "vault_handle": self.SECRET,
                "stripe_secret_key": self.SECRET,
                "ssl_private_key": base64.b64encode(self.PEM),
                "token_payload": {"bearer": self.SECRET},
                "retry_token": 7,
                "cache_key": "places:schiphol",
            }
        )

    # --- inserts --------------------------------------------------------------------- #

    def test_insert_redacts_name_shaped_field(self):
        record = self._make()
        entry = self._snapshot(self.model._name, record.id, "i")["access_token"]
        self.assertEqual(entry["raw"], AUDIT_REDACTED)
        self.assertEqual(entry["display"], AUDIT_REDACTED)
        self.assertNotIn(self.SECRET, str(self._snapshot(self.model._name, record.id, "i")))

    def test_insert_redacts_declared_field(self):
        """`vault_handle` matches no shape at all — only the class declaration reaches it."""
        record = self._make()
        snapshot = self._snapshot(self.model._name, record.id, "i")
        self.assertEqual(snapshot["vault_handle"]["raw"], AUDIT_REDACTED)

    def test_insert_redacts_a_marker_word_inside_the_name(self):
        """`stripe_secret_key` has its marker in the middle. The exact-name-plus-suffix rule
        this replaced missed the whole `*_secret_*` / `*_key` family."""
        snapshot = self._snapshot(self.model._name, self._make().id, "i")
        self.assertEqual(snapshot["stripe_secret_key"]["raw"], AUDIT_REDACTED)

    def test_insert_redacts_a_binary_field(self):
        """The regression that motivated the type DENY list. An allow-list of char/text/html
        let `ir.mail_server.smtp_ssl_private_key` — a Binary bytea column — write a full PEM
        into the audit log on every mail-server edit."""
        snapshot = self._snapshot(self.model._name, self._make().id, "i")
        self.assertEqual(snapshot["ssl_private_key"]["raw"], AUDIT_REDACTED)
        self.assertNotIn("PRIVATE KEY", str(snapshot))

    def test_insert_redacts_a_json_field(self):
        snapshot = self._snapshot(self.model._name, self._make().id, "i")
        self.assertEqual(snapshot["token_payload"]["raw"], AUDIT_REDACTED)
        self.assertNotIn(self.SECRET, str(snapshot))

    def test_the_real_mail_server_private_key_is_covered(self):
        """Asserted against the live core field, so the rule cannot drift from Odoo. The
        field is Binary + `attachment=False`, so it is a real bytea column that the audit
        patch reads with raw SQL — which also bypasses its `groups="base.group_system"`."""
        server = self.env["ir.mail_server"]
        for field_name in ("smtp_pass", "smtp_ssl_private_key"):
            self.assertTrue(
                _is_secret_field(server, server._fields[field_name]),
                f"ir.mail_server.{field_name} must be redacted",
            )

    def test_insert_keeps_the_field_key_so_the_change_is_still_visible(self):
        """A redacted field must stay IN the snapshot with a placeholder. Dropping the key
        would hide that the field changed at all, and would shift the row count the
        aggregated view unnests."""
        snapshot = self._snapshot(self.model._name, self._make().id, "i")
        self.assertIn("access_token", snapshot)
        self.assertEqual(snapshot["access_token"]["label"], "Access Token")

    def test_insert_does_not_redact_a_non_secret_field(self):
        record = self._make()
        snapshot = self._snapshot(self.model._name, record.id, "i")
        self.assertEqual(snapshot["cache_key"]["raw"], "places:schiphol")
        self.assertEqual(snapshot["name"]["raw"], "Redaction probe")

    def test_insert_does_not_redact_a_non_text_field(self):
        """`retry_token` matches the name shape but is an Integer. A counter is not a
        credential, and the field-type gate is what keeps `tokens_in` readable."""
        snapshot = self._snapshot(self.model._name, self._make().id, "i")
        self.assertEqual(snapshot["retry_token"]["raw"], "7")

    # --- updates --------------------------------------------------------------------- #

    def test_update_redacts_both_old_and_new_value(self):
        """A rotation is the worst row: without this it keeps the superseded credential
        beside the replacement."""
        record = self._make()
        end_transaction(self.env)
        record.write({"access_token": "rotated-" + self.SECRET})
        record.flush_recordset()
        rows = self._update_rows(self.model._name, record.id, "access_token")
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row.old_value, AUDIT_REDACTED)
            self.assertEqual(row.new_value, AUDIT_REDACTED)
            self.assertEqual(row.old_value_display_name, AUDIT_REDACTED)
            self.assertEqual(row.new_value_display_name, AUDIT_REDACTED)

    def test_update_does_not_redact_a_non_secret_field(self):
        record = self._make()
        end_transaction(self.env)
        record.write({"cache_key": "places:rotterdam"})
        record.flush_recordset()
        rows = self._update_rows(self.model._name, record.id, "cache_key")
        self.assertTrue(rows)
        self.assertEqual(rows[0].new_value, "places:rotterdam")

    # --- deletes --------------------------------------------------------------------- #

    def test_delete_tombstone_redacts_the_secret(self):
        record = self._make()
        record_id = record.id
        record.unlink()
        entry = self._snapshot(self.model._name, record_id, "d")["access_token"]
        self.assertEqual(entry["raw"], AUDIT_REDACTED)
        self.assertEqual(entry["display"], AUDIT_REDACTED)

    # --- ir.config_parameter: the record-aware lever --------------------------------- #

    def test_secret_system_parameter_value_is_redacted(self):
        param = self.env["ir.config_parameter"].create(
            {"key": "oteny.audit.probe_api_key", "value": self.SECRET}
        )
        param.flush_recordset()
        snapshot = self._snapshot("ir.config_parameter", param.id, "i")
        self.assertEqual(snapshot["value"]["raw"], AUDIT_REDACTED)

    def test_secret_system_parameter_key_stays_readable(self):
        """The key is the parameter NAME. An auditor needs it, and the restore detector
        matches `record_display_name == 'database.uuid'` on it."""
        param = self.env["ir.config_parameter"].create(
            {"key": "oteny.audit.probe_api_key", "value": self.SECRET}
        )
        param.flush_recordset()
        snapshot = self._snapshot("ir.config_parameter", param.id, "i")
        self.assertEqual(snapshot["key"]["raw"], "oteny.audit.probe_api_key")
        row = self.log_model.search(
            [("model_name", "=", "ir.config_parameter"), ("record_id", "=", param.id)], limit=1
        )
        self.assertEqual(row.record_display_name, "oteny.audit.probe_api_key")

    def test_secret_system_parameter_rotation_is_redacted(self):
        param = self.env["ir.config_parameter"].create(
            {"key": "oteny.audit.probe_token", "value": self.SECRET}
        )
        end_transaction(self.env)
        param.write({"value": "rotated-" + self.SECRET})
        param.flush_recordset()
        rows = self._update_rows("ir.config_parameter", param.id, "value")
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row.old_value, AUDIT_REDACTED)
            self.assertEqual(row.new_value, AUDIT_REDACTED)

    def test_secret_system_parameter_delete_tombstone_is_redacted(self):
        param = self.env["ir.config_parameter"].create(
            {"key": "oteny.audit.probe_secret", "value": self.SECRET}
        )
        param.flush_recordset()
        param_id = param.id
        param.unlink()
        snapshot = self._snapshot("ir.config_parameter", param_id, "d")
        self.assertEqual(snapshot["value"]["raw"], AUDIT_REDACTED)

    def test_plain_system_parameter_value_is_not_redacted(self):
        """The control. A config change is exactly what an audit trail is for, so only a
        credential-shaped key loses its value."""
        param = self.env["ir.config_parameter"].create(
            {"key": "oteny.audit.probe_base_url", "value": "https://example.test"}
        )
        param.flush_recordset()
        snapshot = self._snapshot("ir.config_parameter", param.id, "i")
        self.assertEqual(snapshot["value"]["raw"], "https://example.test")

    def test_param_key_shape(self):
        from odoo.addons.oteny_audit.models.ir_config_parameter_override import (
            is_secret_param_key,
        )

        for key in ("ai.google_key", "ai.anthropic_key", "iap_vies.client_token",
                    "oteny.broker_token_live_watch", "database.secret", "smtp.PASSWORD"):
            self.assertTrue(is_secret_param_key(key), key)
        # The last four are why the match is on WORDS, not substrings: `tokens` is not
        # `token`, `keys` is not `key`, and two real keys are explicit exceptions.
        for key in ("web.base.url", "oteny.broker_base_url", "posted_workers_nl_url",
                    "portal_stub.require_login", "database.uuid", "", None,
                    "wilma.llm_max_tokens", "portal.allow_api_keys",
                    "auth_signup.reset_password", "recaptcha_public_key"):
            self.assertFalse(is_secret_param_key(key), key)

    def test_param_value_shape(self):
        """A flag or a short number is never a credential, so it keeps its audit value.
        `auth_password_policy.minlength` is the case that matters: its key says password and
        its value is `8`, and how long a password must be is exactly what an auditor reads."""
        from odoo.addons.oteny_audit.models.ir_config_parameter_override import (
            is_secret_param_value,
        )

        for value in ("True", "False", "8", "8192", "0", "", None, "none"):
            self.assertFalse(is_secret_param_value(value), value)
        for value in (self.SECRET, "b2b", "123456789012345678901234567890"):
            self.assertTrue(is_secret_param_value(value), value)

    def test_numeric_secret_shaped_parameter_keeps_its_value(self):
        """End to end: the key matches, the value does not, so the row stays readable."""
        param = self.env["ir.config_parameter"].create(
            {"key": "oteny.audit.probe_password_minlength", "value": "8"}
        )
        param.flush_recordset()
        snapshot = self._snapshot("ir.config_parameter", param.id, "i")
        self.assertEqual(snapshot["value"]["raw"], "8")

    def test_field_name_shape(self):
        """The shape itself, so a future edit cannot quietly widen or narrow it."""
        from odoo.addons.oteny_audit.models.base_patch import _is_secret_field_name

        for name in ("password", "smtp_pass", "pin", "credential", "access_token",
                     "refresh_token", "smtp_ssl_private_key", "stripe_secret_key",
                     "google_calendar_rtoken", "openai_key", "avalara_api_key",
                     "document_token", "bot_claim_token", "login_dance_token",
                     "firebase_push_certificate_key", "partner_key"):
            self.assertTrue(_is_secret_field_name(name), name)
        # `key` is the parameter NAME an auditor must read; `cache_key` and
        # `gemini_request_key` are lookup keys; `pincode` proves `pin` is exact-only.
        for name in ("key", "name", "value", "cache_key", "gemini_request_key", "monkey",
                     "keyword", "pincode", "credential_type_id", "display_name", "body"):
            self.assertFalse(_is_secret_field_name(name), name)
