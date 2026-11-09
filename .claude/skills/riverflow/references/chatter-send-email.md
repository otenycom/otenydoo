# Send Email in the chatter

*User guide and technical reference. Built 8 and 9-Nov-2026, merged on 9-Nov-2026 (otenydoo
`main` `f71c1f1`, radar `dev` `8388b91e3`). Design and decisions: plan
[chatter-send-message.md](../plans/chatter-send-message.md).*

## For users

On a service, a log entry, an employee, a ship and a contact, the chatter shows **Send
Email** in place of Odoo's Send message. On a contact, the new service goes to the top-level
contact: the company, or the person when there is no company.
Every e-mail that staff send then goes through the workflow, so the Radar shows that it went
out and what came back.

- **No e-mail came in yet.** Send Email opens the e-mail transition of the current state
  (for example Resend or Request Appointment). With two or more, a small screen lets you
  choose. When the state has no e-mail transition, Send Email opens the e-mail screen of a
  new Send Email service under the record. The service is made only when you press Send;
  Cancel leaves nothing behind.
- **An e-mail came in.** Send Email, or the Reply arrow on that e-mail, opens the reply
  screen: "To" holds the sender and the known outside recipients, the subject is "Re: …".
  The reply marks the incoming e-mails up to that one as reviewed. A reply is not a
  transition: the state and the deadline stay.
- **To a colleague:** write a Log note with an @mention.
- A **credential** shows only Log note. An **invoice** keeps Odoo's Send message.
- The e-mail screen refuses a send to yourself alone and a recipient without an e-mail
  address.
- When you change the team or the deadline, the screen renders the template again (the
  signature names the team), but a subject or a text that you changed stays as typed. An
  edited text therefore keeps the signature of the team before the change.
- In an e-mail conversation, a switch of template keeps the subject, so the recipient's mail
  program keeps the e-mails in one conversation.

## Technical reference

- **Mode per record:** `mail.thread._riverflow_chatter_email_mode()` returns `False` (Odoo's
  Send message stays), `"send_email"` or `"note_only"`. `riverflow.mail.thread.review.mixin`
  and `res.partner` return `"send_email"`, `rivercreds.credential` `"note_only"`. The chatter
  asks for it with the request list key `riverflowEmailMode`
  (`models/mail_thread_send_email.py`, `_thread_to_store`).
- **The button:** `action_riverflow_send_email()`. The e-mail transitions are the form's
  transition buttons whose action has `sends_email` set, so the chatter follows the header's
  bot rules and hiding. One transition opens at once; more open
  `riverflow.send.email.choice`. None: `_riverflow_email_service_action()`.
- **The Send Email service:** the transition `riverflow.trans_se_3` in the create mode of the
  transition wizard, with the action context of the Start transition `riverflow.trans_se_1`
  (crewradar sets the template Blank Email there). On a service the new service is a child
  service; on a contact it goes to `commercial_partner_id` (`_riverflow_email_service_defaults`).
  The team is the service's team, else the writer's home team; a writer without a home team
  chooses one on the screen.
- **The reply screen:** `riverflow.mail.reply.wizard`; incoming e-mails are
  `_riverflow_incoming_emails()` (message type email, created by the system user, author
  not an internal user).
- **Recipient checks:** `riverflow.mail.send.mixin` (`_riverflow_check_email_recipients`).
- **Edited text on a team or deadline change:** `subject_last_rendered` and
  `body_last_rendered` on `riverflow.service.email.sender.wizard`; `onchange_supply_fields`
  keeps what differs from them (the text compared without markup).
- **JS:** `static/src/components/chatter_send_email/` (the button, the Reply action, Reply
  All and Forward hidden on a record with Send Email, Send message hidden while the thread
  loads).
- **Tests:** `riverflow/tests/test_chatter_send_email.py`, hoot
  `riverflow/static/tests/chatter_send_email.test.js`, two tests in
  `rivercreds/tests/test_credential_wizard.py`.
