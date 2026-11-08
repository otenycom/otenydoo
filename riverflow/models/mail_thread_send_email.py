from odoo import _, models
from odoo.exceptions import UserError

# The modes of the chatter's e-mail button (plan riverflow/plans/chatter-send-message.md).
# False: Odoo's own Send message stays (an invoice, decision 10).
EMAIL_MODE_SEND_EMAIL = "send_email"  # Send Email replaces Send message (§ 5)
EMAIL_MODE_NOTE_ONLY = "note_only"  # no e-mail button, only Log note (a document record, decision 9)


class MailThread(models.AbstractModel):
    """Send Email in the chatter: one structure for every e-mail that staff send.

    A record without an incoming e-mail sends its first e-mail through an e-mail transition
    of its current state, else through a Send Email service on the record (§ 5 A). A record
    with an incoming e-mail answers it with the reply wizard (§ 5 B). Each conversation thus
    is a record with its own subject (decision 13).
    """

    _inherit = "mail.thread"

    def _riverflow_chatter_email_mode(self):
        """Which e-mail button the chatter of this record shows; see the EMAIL_MODE_* values."""
        return False

    def _thread_to_store(self, store, fields, *, request_list=None):
        super()._thread_to_store(store, fields, request_list=request_list)
        if request_list and "riverflowEmailMode" in request_list:
            for thread in self:
                mode = thread._riverflow_chatter_email_mode()
                values = {"riverflowEmailMode": mode, "riverflowIncomingEmailIds": []}
                if mode == EMAIL_MODE_SEND_EMAIL and thread.id:
                    values["riverflowIncomingEmailIds"] = thread._riverflow_incoming_emails().ids
                store.add(thread, values, as_thread=True)

    # ------------------------------------------------------------
    # Send Email
    # ------------------------------------------------------------

    def action_riverflow_send_email(self):
        """The chatter's Send Email button."""
        self.ensure_one()
        if self._riverflow_chatter_email_mode() != EMAIL_MODE_SEND_EMAIL:
            raise UserError(_("This record sends no e-mail from its chatter."))

        incoming = self._riverflow_incoming_emails()
        if incoming:
            return self._riverflow_reply_action(incoming[-1])

        transitions = self._riverflow_email_transitions()
        if len(transitions) == 1 and transitions.action_id.email_kind == "plain":
            return self._riverflow_open_email_transition(transitions)
        if transitions:
            return self._riverflow_email_choice_action(transitions)
        return self._riverflow_email_service_action()

    def action_riverflow_reply(self, message_id):
        """The Reply action on an incoming e-mail in the chatter."""
        self.ensure_one()
        message = self._riverflow_incoming_emails().filtered(lambda m: m.id == message_id)
        if not message:
            raise UserError(_("This message is not an incoming e-mail of this record."))
        return self._riverflow_reply_action(message)

    def _riverflow_incoming_emails(self):
        """The e-mails from outside senders on this record, oldest first.

        The mail gateway posts an incoming e-mail as the system user, the same rule as
        riverflow.mail.thread.review.mixin. A reply that a colleague writes from their own
        mailbox comes in the same way; it is not an outside sender's e-mail, so it is left out.
        """
        self.ensure_one()
        system_user = self.env.ref("base.user_root")
        return (
            self.message_ids.filtered(
                lambda m: m.message_type == "email"
                and m.create_uid == system_user
                and not m.author_id.user_ids.filtered(lambda user: not user.share)
            )
            .sorted(lambda m: (m.date, m.id))
        )

    def _riverflow_has_email_conversation(self):
        """Whether an e-mail went out from this record or came in on it: a message of the
        Discussions subtype, written as an e-mail or with Send message."""
        self.ensure_one()
        discussions = self.env.ref("mail.mt_comment")
        return bool(
            self.message_ids.filtered(
                lambda m: m.message_type in ("email", "comment") and m.subtype_id == discussions
            )
        )

    def _riverflow_email_transitions(self):
        """The transitions offered from the current state whose action sends an e-mail.

        They are the buttons that the form shows, so the chatter offers what the header
        offers: the same bot rules and the same per-record hiding apply."""
        self.ensure_one()
        Transition = self.env["riverflow.transition"]
        if "transition_buttons_json" not in self._fields:
            return Transition
        buttons = (self.transition_buttons_json or {}).get("buttons", [])
        transition_ids = [
            button["context"]["transition_id"]
            for button in buttons
            if (button.get("context") or {}).get("transition_id")
        ]
        return Transition.browse(transition_ids).filtered(lambda t: t.action_id.email_kind)

    def _riverflow_open_email_transition(self, transition):
        return self.with_context(transition_id=transition.id).action_button_click()

    def _riverflow_email_choice_action(self, transitions):
        """A screen with the e-mail transitions; with "Other e-mail" when each of them sends
        a document (decision 3)."""
        offer_other_email = all(t.action_id.email_kind == "document" for t in transitions)
        choice = self.env["riverflow.send.email.choice"].create(
            {
                "res_model": self._name,
                "res_id": self.id,
                "transition_ids": [(6, 0, transitions.ids)],
                "offer_other_email": offer_other_email,
            }
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Send Email"),
            "res_model": "riverflow.send.email.choice",
            "res_id": choice.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
        }

    def _riverflow_email_service_defaults(self):
        """Where the Send Email service of this record goes: the record is its subject.
        A service puts it under itself as a child service; a contact on its top-level
        contact."""
        self.ensure_one()
        return {
            "default_res_model": self._name,
            "default_res_id": self.id,
            "default_responsible_team_id": self._riverflow_email_responsible_team().id,
        }

    def _riverflow_email_responsible_team(self):
        """The team that sends the e-mail and reviews the reply: the writer's home team."""
        return self.env.user.home_team_id

    def _riverflow_email_service_action(self):
        """The fallback (decision 2): the Send Email transition of a new Send Email service.

        The service is made only when the user sends (the create mode of the transition
        wizard), so a cancel leaves no service behind. Its name becomes the e-mail
        subject, and its "To" starts empty."""
        transition = self.env.ref("riverflow.trans_se_3")
        context = dict(self._riverflow_email_service_defaults(), transition_id=transition.id)
        starter = self.env["riverflow.start.service"].with_context(**context).create({})
        return starter.with_context(**context)._prepare_transition_action()

    def _riverflow_reply_action(self, message):
        reply = self.env["riverflow.mail.reply.wizard"].create(
            {"res_model": self._name, "res_id": self.id, "parent_message_id": message.id}
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Reply"),
            "res_model": "riverflow.mail.reply.wizard",
            "res_id": reply.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
        }
