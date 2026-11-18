# test_audit_log.py
from odoo.tests import TransactionCase
from odoo.tests import tagged


@tagged("oteny_audit", "post_install", "-at_install")
class TestAuditLog(TransactionCase):
    """Test the audit log functionality"""

    def setUp(self):
        super().setUp()

    def _enable_audit_for_default_ignored_model(self, model_name):
        """Locally re-enable auditing for a model that is in
        OtenyAuditLog._DEFAULT_IGNORED_MODEL_NAMES (see Measure 1).

        Tests that exercise audit behavior on infra models (mail.message,
        discuss.channel, etc.) call this in their body to opt back in.
        Restores via addCleanup so other tests are unaffected.
        """
        model_class = type(self.env[model_name])
        had_attr = "_oteny_audit_ignore" in model_class.__dict__
        old_value = model_class._oteny_audit_ignore if had_attr else None
        model_class._oteny_audit_ignore = False

        def restore():
            if had_attr:
                model_class._oteny_audit_ignore = old_value
            else:
                del model_class._oteny_audit_ignore

        self.addCleanup(restore)

    def test_create_logs_insert(self):
        """Creating a record logs one insert row that holds the record's id and no field
        value (19.0.1.747): the values are in the record itself."""
        partner = self.env["res.partner"].create(
            {
                "name": "Test Partner for Audit",
                "email": "test@example.com",
            }
        )
        partner.flush_recordset()
        insert_logs = self.env["oteny.audit.log"].search(
            [
                ("model_name", "=", "res.partner"),
                ("record_id", "=", partner.id),
                ("change_type", "=", "i"),
            ]
        )
        self.assertEqual(len(insert_logs), 1, "Insert log should be created")
        self.assertEqual(
            (insert_logs.field_name, insert_logs.new_value, insert_logs.snapshot),
            ("id", str(partner.id), False),
        )
        self.assertEqual(insert_logs.record_display_name, partner.display_name)

    def test_write_logs_update(self):
        """Test that updating a record logs an update"""
        # Create a test partner
        partner = self.env["res.partner"].create(
            {
                "name": "Initial Name",
                "email": "initial@example.com",
            }
        )

        # Clear any logs from creation
        self.env["oteny.audit.log"].search(
            [
                ("model_name", "=", "res.partner"),
                ("record_id", "=", partner.id),
            ]
        ).unlink()

        # Update the partner
        partner.write(
            {
                "name": "Updated Name",
                "email": "updated@example.com",
            }
        )

        # Force flush to trigger audit logging
        partner.flush_recordset()

        # Check that update logs were created
        update_logs = self.env["oteny.audit.log"].search(
            [
                ("model_name", "=", "res.partner"),
                ("record_id", "=", partner.id),
                ("change_type", "=", "u"),
            ]
        )

        self.assertTrue(update_logs, "Update logs should be created")

        # Check that name field was logged correctly
        name_log = update_logs.filtered(lambda l: l.field_name == "name")
        if name_log:
            self.assertEqual(name_log.old_value, "Initial Name")
            self.assertEqual(name_log.new_value, "Updated Name")

    def test_unlink_logs_delete(self):
        """Test that deleting a record logs a tombstone snapshot of all non-default fields.

        Measure 3: a delete must capture every non-default field value so the
        record is reconstructable from log + (still-existing related records)
        until log expiry.
        """
        # Create a test partner
        partner = self.env["res.partner"].create(
            {
                "name": "To Be Deleted",
                "email": "delete@example.com",
            }
        )

        partner_id = partner.id
        partner_name = partner.name

        # Delete the partner
        partner.unlink()

        # Check that exactly one delete snapshot was created
        delete_logs = self.env["oteny.audit.log"].search(
            [
                ("model_name", "=", "res.partner"),
                ("record_id", "=", partner_id),
                ("change_type", "=", "d"),
            ]
        )

        self.assertEqual(len(delete_logs), 1, "Delete should produce one snapshot log row")
        self.assertEqual(delete_logs.field_name, "__snapshot__")

        snapshot = delete_logs.snapshot or {}
        self.assertIn("name", snapshot)
        self.assertEqual(snapshot["name"]["raw"], partner_name)
        self.assertIn("email", snapshot)
        self.assertEqual(snapshot["email"]["raw"], "delete@example.com")

    def test_write_logs_m2m_group_add(self):
        """Test that adding a user to a group logs an update"""
        # Create a test user
        main_company = self.env.ref("base.main_company")
        user = self.env["res.users"].create(
            {
                "name": "Group Test User",
                "login": "test_group_user@example.com",
                "company_id": main_company.id,
                "company_ids": [(6, 0, [main_company.id])],
            }
        )

        # ensure the user doesn't have the group_sanitize_override
        group_html = self.env.ref("base.group_sanitize_override")
        self.assertNotIn(group_html, user.group_ids)

        # Clear any logs from creation
        self.env["oteny.audit.log"].search(
            [
                ("model_name", "=", "res.users"),
                ("record_id", "=", user.id),
            ]
        ).unlink()

        initial_groups = user.group_ids

        # Add the user to the system group
        user.write({"group_ids": [(4, group_html.id, 0)]})
        user.flush_recordset()

        # Check that update logs were created
        update_logs = self.env["oteny.audit.log"].search(
            [
                ("model_name", "=", "res.users"),
                ("record_id", "=", user.id),
                ("change_type", "=", "u"),
                ("field_name", "=", "group_ids"),
            ]
        )

        self.assertEqual(len(update_logs), 1, "One update log for group_ids should be created")

        log = update_logs

        final_groups = user.group_ids
        # Compare display names directly - they contain group names like 'Technical / Access to export feature'
        expected_prev_display = ", ".join(sorted(g.display_name for g in initial_groups))
        expected_new_display = ", ".join(sorted(g.display_name for g in final_groups))

        actual_prev_display = (
            ", ".join(sorted(log.old_value_display_name.split(", "))) if log.old_value_display_name else ""
        )
        actual_new_display = (
            ", ".join(sorted(log.new_value_display_name.split(", "))) if log.new_value_display_name else ""
        )

        self.assertEqual(actual_prev_display, expected_prev_display)
        self.assertEqual(actual_new_display, expected_new_display)

    def test_no_recursion_on_audit_log(self):
        """Test that audit log operations don't trigger recursive logging"""
        # Create an audit log entry directly
        initial_count = self.env["oteny.audit.log"].search_count([])

        log = self.env["oteny.audit.log"].create(
            {
                "model_name": "test.model",
                "record_id": 1,
                "field_name": "test_field",
                "old_value": "old",
                "new_value": "new",
                "change_type": "u",
            }
        )

        # Check that no additional logs were created for the audit log itself
        new_count = self.env["oteny.audit.log"].search_count(
            [
                ("model_name", "=", "oteny.audit.log"),
            ]
        )

        self.assertEqual(new_count, 0, "No audit logs should be created for audit log model itself")

    def test_predefined_parent_keys_mail_message(self):
        """Test that predefined parent keys work for mail.message"""
        # mail.message is default-ignored (Measure 1); locally re-enable to
        # exercise the polymorphic _model_parent_keys "model,res_id" path.
        self._enable_audit_for_default_ignored_model("mail.message")
        # Create a test partner as the parent record
        parent_partner = self.env["res.partner"].create(
            {
                "name": "Parent Partner for Message",
                "email": "parent@example.com",
            }
        )

        # Clear any logs from partner creation
        self.env["oteny.audit.log"].search(
            [
                ("model_name", "=", "res.partner"),
                ("record_id", "=", parent_partner.id),
            ]
        ).unlink()

        # Create a mail message that references the partner
        message = self.env["mail.message"].create(
            {
                "model": "res.partner",
                "res_id": parent_partner.id,
                "subject": "Test Message",
                "body": "Test message body",
                "message_type": "comment",
            }
        )

        # Force flush to trigger audit logging
        message.flush_recordset()

        # Check that audit logs were created for the message
        message_logs = self.env["oteny.audit.log"].search(
            [
                ("model_name", "=", "mail.message"),
                ("record_id", "=", message.id),
            ]
        )

        self.assertTrue(message_logs, "Audit logs should be created for mail.message")

        # Check that references were created using predefined keys
        refs = self.env["oteny.audit.log.ref"].search(
            [
                ("audit_log_id", "in", message_logs.ids),
            ]
        )

        # Each log should have refs (at least a direct ref)
        self.assertTrue(refs, "References should be created for mail.message logs")

        # Verify parent references (not direct) pointing to the expected parent partner
        partner_parent_refs = refs.filtered(
            lambda r: not r.is_direct
            and r.target_model_name == "res.partner"
            and r.target_record_id == parent_partner.id
        )
        self.assertTrue(partner_parent_refs, "Should have parent references pointing to the parent partner")
        # For parent references from child logs (mail.message), target_display_name should be the child's display name
        self.assertEqual(
            partner_parent_refs[0].target_display_name,
            message.display_name,
            "Target display name should be the message's display name for child logs",
        )
        # And parent_display_name should be the parent's display name
        self.assertEqual(
            partner_parent_refs[0].parent_display_name,
            parent_partner.display_name,
            "Parent display name should match",
        )

    def test_predefined_parent_keys_res_partner(self):
        """Test that predefined parent keys work for res.partner"""
        # Create a parent partner
        parent_partner = self.env["res.partner"].create(
            {
                "name": "Parent Partner",
                "email": "parent@example.com",
            }
        )

        # Clear any logs from parent partner creation
        self.env["oteny.audit.log"].search(
            [
                ("model_name", "=", "res.partner"),
                ("record_id", "=", parent_partner.id),
            ]
        ).unlink()

        # Create a child partner
        child_partner = self.env["res.partner"].create(
            {
                "name": "Child Partner",
                "email": "child@example.com",
                "parent_id": parent_partner.id,
            }
        )

        # Force flush to trigger audit logging
        child_partner.flush_recordset()

        # Check that audit logs were created for the child partner
        child_logs = self.env["oteny.audit.log"].search(
            [
                ("model_name", "=", "res.partner"),
                ("record_id", "=", child_partner.id),
            ]
        )

        self.assertTrue(child_logs, "Audit logs should be created for child res.partner")

        # Check that references were created using predefined keys
        refs = self.env["oteny.audit.log.ref"].search(
            [
                ("audit_log_id", "in", child_logs.ids),
            ]
        )

        self.assertTrue(refs, "References should be created for child partner logs")

        # Verify parent references (not direct) pointing to the expected parent partner
        partner_parent_refs = refs.filtered(
            lambda r: not r.is_direct
            and r.target_model_name == "res.partner"
            and r.target_record_id == parent_partner.id
        )
        self.assertTrue(partner_parent_refs, "Should have parent references pointing to the parent partner")
        # For parent references from child logs, target_display_name should be the child's display name
        self.assertEqual(
            partner_parent_refs[0].target_display_name,
            child_partner.display_name,
            "Target display name should be the child's display name for child logs",
        )
        # And parent_display_name should be the parent's display name
        self.assertEqual(
            partner_parent_refs[0].parent_display_name,
            parent_partner.display_name,
            "Parent display name should match",
        )

    def test_recursive_parent_keys_grandparent_reference(self):
        """Test that recursive parent key resolution works up to 2 levels (grandparent)"""
        # Create a grandparent partner
        grandparent_partner = self.env["res.partner"].create(
            {
                "name": "Grandparent Partner",
                "email": "grandparent@example.com",
            }
        )

        # Create a parent partner (child of grandparent)
        parent_partner = self.env["res.partner"].create(
            {
                "name": "Parent Partner",
                "email": "parent@example.com",
                "parent_id": grandparent_partner.id,
            }
        )

        # Clear any logs from partner creation
        self.env["oteny.audit.log"].search(
            [
                ("model_name", "=", "res.partner"),
                ("record_id", "in", [grandparent_partner.id, parent_partner.id]),
            ]
        ).unlink()

        # Create a child partner (grandchild)
        child_partner = self.env["res.partner"].create(
            {
                "name": "Child Partner",
                "email": "child@example.com",
                "parent_id": parent_partner.id,
            }
        )

        # Force flush to trigger audit logging
        child_partner.flush_recordset()

        # Check that audit logs were created for the child partner
        child_logs = self.env["oteny.audit.log"].search(
            [
                ("model_name", "=", "res.partner"),
                ("record_id", "=", child_partner.id),
            ]
        )

        self.assertTrue(child_logs, "Audit logs should be created for child res.partner")

        # Check that references were created and point to GRANDPARENT (not just immediate parent)
        refs = self.env["oteny.audit.log.ref"].search(
            [
                ("audit_log_id", "in", child_logs.ids),
            ]
        )

        self.assertTrue(refs, "References should be created for child partner logs")

        # Filter for parent references (not direct)
        parent_refs = refs.filtered(lambda r: not r.is_direct)

        # Verify that we have parent references pointing to both immediate parent and grandparent
        immediate_parent_refs = parent_refs.filtered(
            lambda r: r.target_model_name == "res.partner" and r.target_record_id == parent_partner.id
        )
        grandparent_refs = parent_refs.filtered(
            lambda r: r.target_model_name == "res.partner" and r.target_record_id == grandparent_partner.id
        )

        self.assertTrue(
            immediate_parent_refs, "Should have parent references pointing to the immediate parent"
        )
        self.assertTrue(grandparent_refs, "Should have parent references pointing to the grandparent")

        # For parent references from child logs, target_display_name should be the child's display name
        self.assertEqual(
            immediate_parent_refs[0].target_display_name,
            child_partner.display_name,
            "Target display name should be the child's display name for child logs",
        )
        # And parent_display_name should be the parent's display name
        self.assertEqual(
            immediate_parent_refs[0].parent_display_name,
            parent_partner.display_name,
            "Parent display name should match",
        )
        # For grandparent references, target_display_name should also be the child's display name
        self.assertEqual(
            grandparent_refs[0].target_display_name,
            child_partner.display_name,
            "Target display name should be the child's display name for child logs",
        )
        # And parent_display_name should be the grandparent's display name
        self.assertEqual(
            grandparent_refs[0].parent_display_name,
            grandparent_partner.display_name,
            "Grandparent display name should match",
        )

    def test_recursive_parent_keys_message_to_grandparent(self):
        """Test recursive parent key resolution for mail.message pointing through partners to grandparent"""
        # mail.message is default-ignored (Measure 1); locally re-enable.
        self._enable_audit_for_default_ignored_model("mail.message")
        # Create a grandparent partner
        grandparent_partner = self.env["res.partner"].create(
            {
                "name": "Grandparent Partner",
                "email": "grandparent@example.com",
            }
        )

        # Create a parent partner (child of grandparent)
        parent_partner = self.env["res.partner"].create(
            {
                "name": "Parent Partner",
                "email": "parent@example.com",
                "parent_id": grandparent_partner.id,
            }
        )

        # Clear any logs from partner creation
        self.env["oteny.audit.log"].search(
            [
                ("model_name", "=", "res.partner"),
                ("record_id", "in", [grandparent_partner.id, parent_partner.id]),
            ]
        ).unlink()

        # Create a mail message that points to the immediate parent partner
        message = self.env["mail.message"].create(
            {
                "model": "res.partner",
                "res_id": parent_partner.id,  # Points to immediate parent
                "subject": "Test Message to Parent",
                "body": "Test message body",
                "message_type": "comment",
            }
        )

        # Force flush to trigger audit logging
        message.flush_recordset()

        # Check that audit logs were created for the message
        message_logs = self.env["oteny.audit.log"].search(
            [
                ("model_name", "=", "mail.message"),
                ("record_id", "=", message.id),
            ]
        )

        self.assertTrue(message_logs, "Audit logs should be created for mail.message")

        # Check that references point to GRANDPARENT (not just immediate parent)
        refs = self.env["oteny.audit.log.ref"].search(
            [
                ("audit_log_id", "in", message_logs.ids),
            ]
        )

        self.assertTrue(refs, "References should be created for mail.message logs")

        # Filter for parent references (not direct)
        parent_refs = refs.filtered(lambda r: not r.is_direct)

        # Verify that we have parent references pointing to both immediate parent and grandparent
        immediate_parent_refs = parent_refs.filtered(
            lambda r: r.target_model_name == "res.partner" and r.target_record_id == parent_partner.id
        )
        grandparent_refs = parent_refs.filtered(
            lambda r: r.target_model_name == "res.partner" and r.target_record_id == grandparent_partner.id
        )

        self.assertTrue(
            immediate_parent_refs, "Should have parent references pointing to the immediate parent partner"
        )
        self.assertTrue(grandparent_refs, "Should have parent references pointing to the grandparent partner")

        # For parent references from child logs (mail.message), target_display_name should be the child's display name
        self.assertEqual(
            immediate_parent_refs[0].target_display_name,
            message.display_name,
            "Target display name should be the message's display name for child logs",
        )
        # And parent_display_name should be the parent's display name
        self.assertEqual(
            immediate_parent_refs[0].parent_display_name,
            parent_partner.display_name,
            "Parent display name should match",
        )
        # For grandparent references, target_display_name should also be the child's display name
        self.assertEqual(
            grandparent_refs[0].target_display_name,
            message.display_name,
            "Target display name should be the message's display name for child logs",
        )
        # And parent_display_name should be the grandparent's display name
        self.assertEqual(
            grandparent_refs[0].parent_display_name,
            grandparent_partner.display_name,
            "Grandparent display name should match",
        )

    def test_unlink_no_blank_values_logged(self):
        """The delete tombstone only contains non-blank fields, but every non-blank
        field at the time of deletion is captured (full reconstructability)."""
        partner = self.env["res.partner"].create(
            {
                "name": "Partner To Delete",
                "email": "",  # Explicitly blank
                "phone": "",  # Explicitly blank
                "street": "Street Address",  # Non-blank
                "city": "",  # Explicitly blank
            }
        )

        partner_id = partner.id
        partner_name = partner.name
        partner_street = partner.street

        partner.unlink()

        delete_logs = self.env["oteny.audit.log"].search(
            [
                ("model_name", "=", "res.partner"),
                ("record_id", "=", partner_id),
                ("change_type", "=", "d"),
            ]
        )
        self.assertEqual(len(delete_logs), 1)
        snapshot = delete_logs.snapshot or {}

        # Name and street had values: in the snapshot.
        self.assertIn("name", snapshot)
        self.assertEqual(snapshot["name"]["raw"], partner_name)
        self.assertIn("street", snapshot)
        self.assertEqual(snapshot["street"]["raw"], partner_street)

        # Blank fields are not in the snapshot.
        self.assertNotIn("email", snapshot)
        self.assertNotIn("phone", snapshot)
        self.assertNotIn("city", snapshot)

        # Optional unset fields are not in the snapshot either.
        for field_name in ["mobile", "zip", "country_id", "state_id"]:
            if field_name in snapshot:
                self.assertNotEqual(
                    snapshot[field_name]["raw"],
                    "",
                    f"Field {field_name} should not be snapshotted with blank value",
                )

    def test_is_audit_ignored_stale_ir_model(self):
        """Stale ir.model records (model dropped from code but row still in DB)
        must not crash _is_audit_ignored / install_for_all_models_action.
        Reproduces: KeyError 'rivercreds.rule' after that model was removed."""
        audit_log = self.env["oteny.audit.log"]

        # A model name that exists nowhere in the registry
        phantom_model = "test.phantom.removed.model"
        self.assertNotIn(phantom_model, self.env.registry.models)

        # _is_audit_ignored should return True (ignore) instead of raising
        result = audit_log._is_audit_ignored(phantom_model)
        self.assertTrue(result, "Stale model not in registry should be treated as ignored")

        # Simulate a stale ir.model row left behind after a module dropped a model.
        # Raw SQL because ORM validation rejects non-x_ manual model names.
        self.env.cr.execute(
            """INSERT INTO ir_model (name, model, state, transient, "order")
               VALUES (%s::jsonb, %s, 'manual', false, 'id')""",
            ('{"en_US": "Phantom Removed Model"}', phantom_model),
        )

        # install_for_all_models_action iterates all ir.model rows — must not raise
        audit_log.install_for_all_models_action()

    def test_audit_action_xml_id_injective_for_similar_names(self):
        """Technical names that collided under dot-to-underscore must get distinct xml ids."""
        log = self.env["oteny.audit.log"]
        m1 = "a.b.c"
        m2 = "a.b_c"
        self.assertNotEqual(
            log._audit_log_action_xml_id(m1),
            log._audit_log_action_xml_id(m2),
        )
        self.assertTrue(
            log._audit_log_action_xml_id(m1).startswith("oteny_audit.action_audit_log_"),
        )

    def test_create_all_defaults_or_empty_logs_insert(self):
        """Even when a record has only default/empty values, its insert row is recorded."""
        test_parent = self.env["oteny.audit.test.parent"].create({})
        test_parent.flush_recordset()
        logs = self.env["oteny.audit.log"].search(
            [
                ("model_name", "=", "oteny.audit.test.parent"),
                ("record_id", "=", test_parent.id),
                ("change_type", "=", "i"),
            ]
        )
        self.assertEqual(logs.mapped("field_name"), ["id"])

    def test_unlink_all_defaults_or_empty_logs_snapshot(self):
        """Even a delete of a record with only defaults emits a tombstone snapshot row."""
        test_parent = self.env["oteny.audit.test.parent"].create({})
        test_parent_id = test_parent.id

        test_parent.unlink()

        logs = self.env["oteny.audit.log"].search(
            [
                ("model_name", "=", "oteny.audit.test.parent"),
                ("record_id", "=", test_parent_id),
                ("change_type", "=", "d"),
            ]
        )

        self.assertEqual(len(logs), 1, "Should create exactly one snapshot log row for delete")
        self.assertEqual(logs.field_name, "__snapshot__")
