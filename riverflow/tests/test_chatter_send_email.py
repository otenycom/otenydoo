"""Send Email in the chatter (plan riverflow/plans/chatter-send-message.md).

- The thread data tells the chatter which e-mail button to show (§ 5): Send Email on a
  riverflow record and on a contact.
- Without an incoming e-mail, Send Email opens the e-mail transition of the state, a choice
  when there are more or when each sends a document, else the fallback Send Email service,
  which is made only on send (decisions 2 and 3).
- With an incoming e-mail, it opens the reply wizard, which answers the sender, keeps the
  state and marks the e-mails as reviewed (§ 5 B, decision 4).
- The writer is never a recipient (decision 6).
- A contact's Send Email service goes on its top-level contact (§ 5 D).
"""

from odoo.addons.mail.tools.discuss import Store
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "riverflow", "test_chatter_send_email")
class TestChatterSendEmail(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        service_model = cls.env["ir.model"]._get("riverflow.service")
        Transition = cls.env["riverflow.transition"]
        cls.plain_action = cls.env.ref("riverflow.transition_action_email_sender")
        cls.document_action = cls.env["riverflow.transition.action"].create(
            {
                "name": "Test Send Document",
                "description": "Renders and e-mails a document",
                "odoo_view": "view_service_email_sender_form",
                "model_id": service_model.id,
                "sends_email": True,
            }
        )
        cls.workflow = cls.env["riverflow.workflow"].create({"model_id": service_model.id, "name": "Chatter Mail WF"})
        cls.state_quiet = cls.env["riverflow.state"].create(
            {"workflow_id": cls.workflow.id, "name": "Quiet", "sequence": 10}
        )
        cls.state_one = cls.env["riverflow.state"].create(
            {"workflow_id": cls.workflow.id, "name": "One mail", "sequence": 20}
        )
        cls.state_two = cls.env["riverflow.state"].create(
            {"workflow_id": cls.workflow.id, "name": "Two mails", "sequence": 30}
        )
        cls.state_doc = cls.env["riverflow.state"].create(
            {"workflow_id": cls.workflow.id, "name": "Document", "sequence": 40}
        )
        cls.state_end = cls.env["riverflow.state"].create(
            {"workflow_id": cls.workflow.id, "name": "Sent", "sequence": 50}
        )
        default_action = cls.env.ref("riverflow.transition_action_default")
        Transition.create(
            {"name": "Just move", "from_state_id": cls.state_quiet.id, "to_state_id": cls.state_end.id,
             "action_id": default_action.id}
        )
        cls.trans_one = Transition.create(
            {"name": "Mail it", "from_state_id": cls.state_one.id, "to_state_id": cls.state_end.id,
             "action_id": cls.plain_action.id}
        )
        Transition.create(
            {"name": "Mail A", "from_state_id": cls.state_two.id, "to_state_id": cls.state_end.id,
             "action_id": cls.plain_action.id, "sequence": 10}
        )
        Transition.create(
            {"name": "Mail B", "from_state_id": cls.state_two.id, "to_state_id": cls.state_end.id,
             "action_id": cls.document_action.id, "sequence": 20}
        )
        cls.trans_doc = Transition.create(
            {"name": "Send contract", "from_state_id": cls.state_doc.id, "to_state_id": cls.state_end.id,
             "action_id": cls.document_action.id}
        )
        cls.client = cls.env["res.partner"].create({"name": "Client Contact", "email": "contact@client.example"})
        cls.agent = cls.env["res.partner"].create({"name": "Agency Contact", "email": "sara@agency.example"})
        cls.writer = cls.env.user.partner_id

    def _service(self, state):
        return self.env["riverflow.service"].create(
            {"name": "Collect the declaration", "workflow_id": self.workflow.id, "state_id": state.id}
        )

    def _incoming_email(self, record, author, **values):
        """An e-mail as the mail gateway posts it: created by the system user."""
        root = self.env.ref("base.user_root")
        return (
            self.env["mail.message"]
            .with_user(root)
            .create(
                dict(
                    {
                        "model": record._name,
                        "res_id": record.id,
                        "message_type": "email",
                        "subtype_id": self.env.ref("mail.mt_comment").id,
                        "author_id": author.id,
                        "subject": "The declaration",
                        "body": "<p>Here it is.</p>",
                    },
                    **values,
                )
            )
            .with_user(self.env.user)
        )

    def _thread_values(self, record):
        store = Store()
        record._thread_to_store(store, [], request_list=["riverflowEmailMode"])
        threads = store.get_result().get("mail.thread", [])
        return next((t for t in threads if t["id"] == record.id and t["model"] == record._name), {})

    # ---- the button ------------------------------------------------------------------

    def test_thread_data_names_the_mode_and_the_incoming_emails(self):
        service = self._service(self.state_quiet)
        self.assertEqual(self._thread_values(service)["riverflowEmailMode"], "send_email")
        self.assertEqual(self._thread_values(service)["riverflowIncomingEmailIds"], [])
        message = self._incoming_email(service, self.client)
        self.assertEqual(self._thread_values(service)["riverflowIncomingEmailIds"], [message.id])
        self.assertEqual(self._thread_values(self.client)["riverflowEmailMode"], "send_email")

    def test_one_plain_transition_opens_it(self):
        action = self._service(self.state_one).action_riverflow_send_email()
        self.assertEqual(action["res_model"], "riverflow.service.email.sender.wizard")
        self.assertEqual(action["context"]["transition_id"], self.trans_one.id)

    def test_two_transitions_open_the_choice(self):
        action = self._service(self.state_two).action_riverflow_send_email()
        choice = self.env["riverflow.send.email.choice"].browse(action["res_id"])
        self.assertEqual(choice.transition_ids.mapped("name"), ["Mail A", "Mail B"])
        captions = [b["caption"] for b in choice.transition_buttons_json["buttons"]]
        self.assertEqual(captions, ["Mail A", "Mail B"])

    def test_one_document_transition_opens_it_without_a_plain_e_mail(self):
        service = self._service(self.state_doc)
        action = service.action_riverflow_send_email()
        self.assertEqual(action["context"]["transition_id"], self.trans_doc.id)
        self.assertFalse(service.child_ids, "No Send Email service next to the workflow")

    # ---- the fallback service --------------------------------------------------------

    def test_fallback_service_is_made_only_on_send(self):
        service = self._service(self.state_quiet)
        action = service.action_riverflow_send_email()
        self.assertEqual(action["res_model"], "riverflow.service.email.sender.wizard")
        self.assertEqual(action["context"]["transition_id"], self.env.ref("riverflow.trans_se_3").id)
        self.assertEqual(action["views"][0][1], "form")
        self.assertIn("default_responsible_team_id", action["context"], "The fallback names the team")
        self.assertFalse(service.child_ids, "Opening the screen makes no service")

        wizard = self.env[action["res_model"]].with_context(action["context"]).create({})
        self.assertFalse(wizard.recipient_partner_ids, "The fallback starts with an empty To")
        wizard.write(
            {
                "recipient_partner_ids": [(6, 0, self.client.ids)],
                "subject_updatable": "Document request",
                "body_updatable": "<p>Please send the declaration.</p>",
            }
        )
        wizard.action_save()

        child = service.child_ids
        self.assertEqual(len(child), 1)
        self.assertEqual(child.workflow_id, self.env.ref("riverflow.workflow_service_send_email"))
        self.assertEqual(child.state_id, self.env.ref("riverflow.state_service_se_awaiting_reply"))
        self.assertEqual(child.name, "Document request")
        sent = child.message_ids.filtered(lambda m: m.message_type == "email")
        self.assertEqual(sent.partner_ids, self.client)
        self.assertIn(self.client, child.message_partner_ids, "The recipient follows the new service")

    # ---- the writer is never a recipient ---------------------------------------------

    def test_writer_is_no_default_recipient(self):
        service = self._service(self.state_one)
        service.message_subscribe(partner_ids=(self.writer | self.agent).ids)
        action = service.action_riverflow_send_email()
        wizard = self.env[action["res_model"]].with_context(action["context"]).create({})
        self.assertEqual(wizard.recipient_partner_ids, self.agent)

    def test_send_to_the_writer_alone_is_refused(self):
        mixin = self.env["riverflow.mail.reply.wizard"]
        with self.assertRaises(UserError):
            mixin._riverflow_check_email_recipients(self.writer)
        mixin._riverflow_check_email_recipients(self.writer | self.client)

    # ---- the reply -------------------------------------------------------------------

    def test_incoming_email_opens_the_reply_to_the_sender(self):
        service = self._service(self.state_one)
        colleague = self.env["res.users"].create(
            {"name": "Colleague Staff", "login": "colleague.staff", "email": "colleague@company.example",
             "group_ids": [(4, self.env.ref("base.group_user").id)]}
        )
        message = self._incoming_email(
            service, self.client,
            incoming_email_to="Agency Contact <sara@agency.example>, colleague@company.example",
        )
        action = service.action_riverflow_send_email()
        reply = self.env["riverflow.mail.reply.wizard"].browse(action["res_id"])
        self.assertEqual(reply.parent_message_id, message)
        self.assertEqual(reply.recipient_partner_ids, self.client | self.agent,
                         "The sender and the known To, without the colleague")
        self.assertNotIn(colleague.partner_id, reply.recipient_partner_ids)
        self.assertEqual(reply.subject, "Re: The declaration")

        reply.body = "<p>Thank you.</p>"
        reply.action_send()
        sent = service.message_ids.filtered(lambda m: m.parent_id == message)
        self.assertEqual(sent.message_type, "email")
        self.assertEqual(sent.partner_ids, self.client | self.agent)
        self.assertEqual(service.state_id, self.state_one, "A reply is not a transition")
        self.assertEqual(service.unreviewed_message_count, 0, "The reply marks the e-mail as reviewed")

    def test_reply_refuses_a_message_that_is_not_incoming(self):
        service = self._service(self.state_quiet)
        note = service.message_post(body="A note", message_type="comment", subtype_xmlid="mail.mt_note")
        with self.assertRaises(UserError):
            service.action_riverflow_reply(note.id)

    # ---- contacts --------------------------------------------------------------------

    def test_contact_service_goes_on_the_top_level_contact(self):
        company = self.env["res.partner"].create({"name": "Client Company BV", "is_company": True})
        person = self.env["res.partner"].create({"name": "Client Person", "parent_id": company.id, "email": "person@client.example"})
        action = person.action_riverflow_send_email()
        self.assertEqual(action["context"]["default_res_model"], "res.partner")
        self.assertEqual(action["context"]["default_res_id"], company.id)

        wizard = self.env[action["res_model"]].with_context(action["context"]).create({})
        wizard.write(
            {
                "recipient_partner_ids": [(6, 0, person.ids)],
                "subject_updatable": "Yearly confirmation",
                "body_updatable": "<p>Please sign and return.</p>",
            }
        )
        wizard.action_save()
        self.assertEqual(company.service_ids.mapped("name"), ["Yearly confirmation"])
        self.assertFalse(person.service_ids)


@tagged("post_install", "-at_install", "riverflow", "test_chatter_send_email")
class TestFollowUpSubject(TransactionCase):
    """A follow-up keeps its subject when the user switches the template (decision 20); a
    first e-mail takes the subject of the template, as before."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        service_model = cls.env["ir.model"]._get("riverflow.service")
        cls.workflow = cls.env["riverflow.workflow"].create({"model_id": service_model.id, "name": "Follow-up WF"})
        cls.state_open = cls.env["riverflow.state"].create(
            {"workflow_id": cls.workflow.id, "name": "Ordered", "sequence": 10}
        )
        cls.template_order = cls.env["mail.template"].create(
            {"name": "Order", "model_id": service_model.id, "subject": "Order for the ship",
             "body_html": "<p>Order body</p>"}
        )
        cls.template_blank = cls.env["mail.template"].create(
            {"name": "Blank", "model_id": service_model.id, "subject": "", "body_html": "<p>Dear</p>"}
        )
        cls.transition = cls.env["riverflow.transition"].create(
            {"name": "Re-order", "from_state_id": cls.state_open.id, "to_state_id": cls.state_open.id,
             "action_id": cls.env.ref("riverflow.transition_action_email_sender").id,
             "mail_template_id": cls.template_order.id}
        )

    def _form(self, service):
        from odoo.tests import Form

        action = service.with_context(transition_id=self.transition.id).action_button_click()
        return Form(self.env[action["res_model"]].with_context(action["context"]))

    def _service(self):
        return self.env["riverflow.service"].create(
            {"name": "Taxi order", "workflow_id": self.workflow.id, "state_id": self.state_open.id}
        )

    def test_follow_up_keeps_the_subject_on_a_template_switch(self):
        service = self._service()
        service.message_post(body="First order", subject="Order for the ship", message_type="comment",
                             subtype_xmlid="mail.mt_comment")
        form = self._form(service)
        self.assertEqual(form.subject_updatable, "Order for the ship")
        form.mail_template_id = self.template_blank
        self.assertEqual(form.subject_updatable, "Order for the ship")
        self.assertIn("Dear", form.body_updatable)

    def test_first_email_takes_the_subject_of_the_new_template(self):
        service = self._service()
        form = self._form(service)
        self.assertEqual(form.subject_updatable, "Order for the ship")
        other = self.template_order.copy({"name": "Other", "subject": "Another subject"})
        form.mail_template_id = other
        self.assertEqual(form.subject_updatable, "Another subject")
