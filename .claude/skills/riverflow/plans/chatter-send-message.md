# Send message in the chatter: one structure for every e-mail that staff send

*Draft plan, 8-Nov-2026. Modules: riverflow (otenydoo) for the structure; crewradar for
the services on a top-level contact (§ 5 D); rivercreds for the credential wizard. Status:
Ries set the structure on 8-Nov-2026 (§ 5) and answered four interviews (§ 4); no
question is open. § 7 is the inventory of every kind of staff e-mail and its home. Goal
(Ries, 8-Nov-2026): one plan with one style for every Send message function. Built on
8-Nov-2026 on `feat/send-message` (radar and otenydoo), § 9; test to-do
[BC 10487893479](https://3.basecamp.com/5527227/buckets/38887735/todos/10487893479).
Basecamp:
[BC 10487142172](https://3.basecamp.com/5527227/buckets/38887735/todos/10487142172)
(the messages that reached no one, training first).*

## 1. Why

On 27-Oct-2026 Dolf wrote a request for the Rijnvaartverklaring to the skipper Teunis with
**Send message** on the service Collect Rijnvaartverklaring (Lansingh). The "To" field was
empty, so the composer showed "To: Followers only". Dolf was the only follower, and Odoo
never notifies the author. Odoo saved the message, sent no e-mail and showed no warning. In
the last 180 days, 11 Send messages went to no one in this way (the list is in BC
10487142172). Dolf probably expected Send message to reach the ship's contact.

Send message lets the user send an e-mail that the record does not guide: the user must
know who follows the record, and nothing shows where the message goes. On a riverflow
record the workflow already has the e-mail paths. This plan replaces Send message with one
structure that uses them, gives every kind of staff e-mail one home (§ 7), and adds the one
home that is missing: services on a top-level contact (§ 5 D).

## 2. What exists today (checked 8-Nov-2026)

- **The composer.** Send message e-mails the names in "To" and every follower that gets
  Discussions, except the author. With an empty "To" the field shows "Followers only"
  (`mail/static/src/core/web/recipients_input.js`). A name in "To" does not become a
  follower on our models (`mail_thread.py`, `message_post`: only `_mail_thread_customer`
  models subscribe the "To" names).
- **Reply on a message.** In the chatter, the Reply action exists only on a note, and it opens
  Log note (`message_actions.js`, `reply-to`; `canReplyTo` is true only in Discuss). So the
  chatter has no other way to answer an incoming e-mail than Send message.
- **Who becomes a follower.** The Send Email wizard posts with `mail_post_autofollow=True`,
  so every recipient of a transition e-mail becomes a follower
  (`riverflow_service_email_sender.py`, `_send_email`). The sender of an incoming e-mail
  becomes a follower when Odoo knows the contact (`mail_thread.py`, the mail gateway).
- **Incoming e-mail.** `riverflow.mail.thread.review.mixin` is on every riverflow record
  (`riverflow.service`, `crewradar.log.entry`, `hr.employee`, `crewradar.site`,
  `rivercreds.credential`). Its field `message_from_external_sender_ids` holds the messages
  that the mail gateway posted (created by the system user), that is the incoming e-mails.
  Mark as Reviewed works on them.
- **The Send Email wizard** (`riverflow.service.email.sender.wizard`) works only on a
  `riverflow.service` and only as a transition. Its default recipients are the followers
  that get Discussions, the current user included (`_followers_as_recipients` →
  `riverflow.service._get_default_recipients`); a mail template adds its `partner_to`. It
  refuses a send without a recipient ("Please select at least one recipient."), but the
  writer alone counts as a recipient. It also applies `riverflow.restrict_email_recipients_to`
  and checks that each recipient has an e-mail address.
- **Colleague message copy.** A staff message to a colleague of another team, by "To" or by
  an @mention, is also posted in that colleague's team channel. It covers Send message and
  Log note alike (`riverflow/models/mail_thread.py`, `_colleague_copy_teams`).
- **The chatter seam.** riverflow already sends a per-record value to the chatter through
  `_thread_to_store` and `requestList` (`unreviewedMessageCount`,
  `static/src/components/chatter_review/chatter_review_patch.js`).
- **Transitions that send e-mail.** The actions whose wizard sends e-mail are "Send Email
  Action" and "Generate & Email" (`riverflow.service.email.sender.wizard`), "Request
  Appointment" (inherits it), "Send Permit + Letter" and "Request Return" (post with
  `partner_ids`). No field marks such an action today. 15 workflows use them: Send Email,
  Taxi Supply Order, Arrange Work Permit, Arrange AUV Contract, Arrange and Send Document,
  Receive and Send Ship A1, Receive and Send Employee A1, Inform Employee Travel Plan, Renew
  Passport, Send Notification, Arrange MFNL Notification, Arrange TFT Contract, Work Permit
  Handover, Collect Unpaid Leave Request, Collect Work Permits at AB.
- **The credential wizard** offers pages only from the service's own chatter
  (`rivercreds_credential_wizard.py`, `service.message_ids`).

## 3. How Send message is used (`crmain`, 7-Nov-2026)

Send messages written by staff in the last 180 days, per model:

- `riverflow.service`: 1,002. 860 reached an outside party, 132 reached only colleagues, 10
  reached no one.
- `account.move`: 962. About 880 come from Odoo's invoice Send button ("Here is your
  invoice" or "credit note", with the PDF), not from the chatter. 48 are payment reminders
  that finance (Gabriele) writes in the chatter with the client in "To". About 15 are
  questions about a payment. The client follows 881 of the 882 customer invoices of these
  180 days, and 32 replies of clients came in on 23 invoices.
- `hr.employee`: 51, all reached an outside party.
- `res.partner`: 2 (questions to the colleague Bas). Over all time 75, of which 63 are one
  yearly letter to the clients in Q1-2026 ("Geachte klant, eenmaal per jaar moeten wij aan
  de Duitse autoriteiten …"), written with the client in "To". 3 replies came in on
  contacts in 180 days. Only 1 of the 500 active companies has an outside follower.
- `crewradar.log.entry`: 1 (it reached no one). `rivercreds.credential`: 0, and 6 over all
  time, the last on 15-Apr-2026; no credential has an outside follower, and no incoming
  e-mail ever reached one.

On services, the outside parties are mostly manning agencies (ODI, Quncrew), taxi and
transport suppliers (Munckhof, Taxi Botlek, ATS Taxi) and skippers. Per workflow: Taxi
Supply Order 502 sends, Send Email 355, Arrange AUV Contract 42, Inform Employee Travel Plan
29, Arrange MFNL Notification 18, Collect Rijnvaartverklaring 17 (15 of them with a name in
"To"), Arrange TFT Contract 14, the rest under 11. A sample shows replies to a supplier's or
an agency's e-mail, and messages between Operations NL and HR ("Hi Kajal, the B&B
confirmed").

So most Send messages are replies in a conversation that a transition e-mail started. A
structure without a reply would remove the main use. The first concept (hide Send message
when the workflow has an e-mail transition) did that; Ries replaced it with § 5.

## 4. Decisions (Ries, 8-Nov-2026)

1. **One structure replaces Send message** on riverflow records: the first e-mail goes
   through an e-mail transition, a reply through an ad-hoc reply wizard (§ 5).
2. **The fallback service.** When the current state has no e-mail transition, the button
   offers it: Radar creates a blank service of the workflow Send Email (a child service on a
   service, a service on a log entry, an employee or a ship) and opens its Send Email wizard
   at once, with no recipient filled in. An error alone would send the user to Outlook, and
   the e-mail would not be on the Radar.
3. **No plain e-mail next to the workflow.** When the state has e-mail transitions, the
   button offers only those, also when each of them sends a document (Send AUV). A user
   has no reason to bypass the workflow, and no case shows the need. A user who needs
   another e-mail adds a separate service (Ries, 9-Nov-2026; the first build offered an
   "Other e-mail" here).
4. **A reply marks the incoming e-mails up to the replied one as reviewed**, because the
   reply shows that the user read them.
5. **Colleagues write a Log note with an @mention** on every riverflow record. The colleague
   is notified, the copy to the colleague's team channel works the same, and no outside
   follower gets the message.
6. **The writer is never a recipient.** No e-mail wizard puts the writer in the default
   recipients, and none counts the writer for "at least one recipient". This holds for
   every e-mail transition, not only the new paths.
7. **The credential wizard also offers the pages of the child services**, so a document that
   comes back on a child Send Email service reaches the parent's upload without a download.
8. **The change lives in riverflow**, next to the colleague message copy and Mark as
   Reviewed. The "reaches no one" error of BC 10487142172 lives there too.
9. **A credential shows no Send Email button, only Log note.** A credential is a document
   record, not a conversation (§ 3). An e-mail about it goes from its service
   (`service_id`) or its holder (`holder_employee_id`, `holder_site_id`).
10. **Invoices keep Odoo's Send message.** In an Odoo setup the invoice is the place where
    finance and the client discuss it: the invoice e-mail makes the client a follower, and
    the client's reply comes back into the invoice's chatter (§ 3: the client follows 881 of
    882 invoices; 32 replies on 23 invoices). Dolf's mistake cannot happen there, because
    the client follows. The "reaches no one" error sits in the chatter composer, not in
    `message_post`, so it never stops the invoice Send button or a message that code posts.
11. **Recipients stay the job of the Send Email wizard** (Ries' push-back on a list of
    contacts from the context). This plan does not extend how recipients are collected. The
    reply wizard reuses the wizard's recipient rules through one shared mixin, so a later
    change to how recipients are collected (for example from the ship's contacts of BC
    10407809799) is made in one place for both wizards. The only reply-specific default is
    the addressee of a reply: the sender and the known To and Cc of the e-mail replied to.
12. **No ad-hoc e-mail in a contact's chatter.** A chatter is one thread, and its subject is
    the record's name. An ad-hoc e-mail to a contact is its own conversation with its own
    subject, so it does not belong in the contact's chatter. A contact gets Send Email
    through services on its top-level contact instead (decision 14).
13. **One conversation is one record with its own subject.** This is the principle behind
    decisions 2, 9, 10 and 12. On a riverflow record a new e-mail goes out through a
    service (the e-mail transition of the service, or the fallback service), so each
    conversation gets its own thread and subject. An invoice is itself such a record. A
    reply stays in the thread where the incoming e-mail arrived.
14. **Services on a top-level contact are part of this plan** (Ries, 8-Nov-2026). A
    top-level contact becomes a subject record of services, as a log entry, an employee and
    a ship are, so a request that names a client (the yearly confirmation for the German
    authorities, § 7 kind 7) has one home per client and not one per ship. On a contact,
    Send Email works as on a riverflow record (§ 5 D).
15. **Every top-level contact, no "client" rule** (Ries, 8-Nov-2026). Radar has no field
    "client", and a rule that guesses one (for example "on a ship's contact list") is vague
    and misses contacts. A *top-level contact* is a contact without a parent company
    (`parent_id` empty): a company, or a person who is not part of a company. Odoo names it
    the commercial partner (`commercial_partner_id`). On `crmain` there are 1,315 active
    ones: 493 companies and 822 persons.
16. **The yearly confirmation goes out in Q1-2027 as a Send Email service on each client,
    with a mail template.** It needs no new workflow. Its own yearly workflow (a request, a
    reminder, the upload of the signed confirmation), the move of KvK and VAT from the ship
    to the client, and a credential scope "client" belong to the track "Ship and client
    onboarding" (BC 10376869542), because they are about documents, not about Send message.
17. **A contact with an incoming e-mail shows the reply wizard**, so the reply stays in the
    thread where the e-mail arrived (decision 13) and does not go out from Outlook.
18. **No new door to a root Send Email service.** Radar › New › New Service › Send Email
    stays the way for the rare e-mail without a record (§ 7 kind 10); the chatter of the
    record stays the first door.
19. **The reply screen keeps the subject editable** (Ries, 8-Nov-2026). A reply comes back
    into the same chatter whatever its subject, because Odoo routes an incoming e-mail by its
    `References` and `In-Reply-To` headers (`mail_thread.py`, `message_route`). A new
    subject does start a separate conversation in Outlook and Gmail, which group by subject;
    the writer chooses that.
20. **A follow-up keeps its subject when the template is switched** (Ries, 8-Nov-2026). When
    the record already has an e-mail conversation, a template that the user picks in the
    Send Email wizard brings its body but keeps the current subject, so the e-mail stays in
    the recipient's conversation. The screen still opens with the subject of the
    transition's own template, so a reminder keeps its "Reminder" subject. The user can
    still edit the subject. Built in `riverflow.service.email.sender.wizard`
    (`has_email_conversation`, `subject_filled`, `onchange_email_template_id`).

## 5. The structure (draft)

The chatter of a riverflow record (not a credential, decision 9) and of a contact (§ 5 D)
shows **Send Email** in the place of Send message. What it does depends on one question: has the record an incoming
e-mail?

**A. No incoming e-mail yet: the first e-mail.** The e-mail goes through an e-mail
transition of the current state, so the workflow records that it went out.

- An action that sends e-mail gets a flag, `sends_email` on `riverflow.transition.action`.
  riverflow sets it on its two actions; each module sets it on its own actions (Request
  Appointment, Send Permit + Letter, Request Return in crewradar_cuneus_sign). A transition
  such as "Sent already", which sends nothing, keeps the default action and is not flagged.
- The chatter button lists the flagged transitions that leave the current state (for
  example Resend, Send Reminder, Request Appointment, Request Pickup) and opens the chosen
  one. With one transition it opens it at once.
- With none (Collect Rijnvaartverklaring today, a log entry, an employee, a ship, a Done
  service of Arrange MFNL Notification), the button offers the fallback service
  (decision 2). With e-mail transitions, the list holds only those (decision 3).

**B. An incoming e-mail exists: the reply.** An ad-hoc reply wizard sends the reply. It is
not a transition: the state and the deadline stay as they are.

- It opens from the chatter button (a reply to the newest incoming e-mail) and from a Reply
  action on each incoming e-mail in the chatter (a reply to that e-mail).
- Its "To" starts with the sender of that e-mail and the known contacts in its To and Cc,
  without the writer and without staff (decision 11). The user can change it as in the Send
  Email wizard.
- It posts the reply as an e-mail on the record, with the incoming e-mail as parent, the
  subject "Re: …", and `mail_post_autofollow`, so the recipients follow the record. It
  marks the incoming e-mails up to the replied one as reviewed (decision 4).
- It works on every riverflow record, not only on a service, so it is its own transient
  model on `res_model` and `res_id`. It shares the sending rules with the Send Email wizard
  (the recipient checks, `riverflow.restrict_email_recipients_to`, the sanitizing, the
  signature) through one mixin, so the two cannot drift apart.

**C. Outside the structure.**

- Colleagues write a Log note with an @mention (decision 5).
- Invoices keep Odoo's Send message, with the "reaches no one" error (decision 10).
- A credential shows no Send button, only Log note (decision 9).

**D. Services on a top-level contact** (decisions 14 and 15).

- *Which contacts.* Every top-level contact. A service on a person of a company goes on the
  company, so Send Email on that person creates the service on the company.
- *riverflow.* Nothing new: a service holds any subject by `res_model` and `res_id`, and a
  template limits its subjects with `allowed_subject_model_ids`. The fallback service is a
  blank service, not made from a template, so no subject limit applies to it; a template
  that must go on a contact lists `res.partner` in `allowed_subject_model_ids`.
- *crewradar.* The form of a top-level contact gets a Services tab with Add Service, as
  `crewradar.site` has (`service_ids`, the caption, the count). A service on a contact
  takes the company of the contact (`_get_subject_company`). The Radar shows the contact's
  name as the subject, as it shows a ship's name.
- *Send Email on a contact.* A contact has no workflow, so path A has no transition: Send
  Email offers the fallback service on the top-level contact (decision 2). An incoming
  e-mail on that service follows path B there. An incoming e-mail on the contact itself
  (an answer to an old message) gets the reply wizard on the contact (decision 17).
- *Not in this plan:* where a document lives and a credential scope "client"
  (decision 16).

## 6. Edge cases

1. **Two or more e-mail transitions in one state** (Arrange Work Permit, Not Started:
   Request Appointment and Request Pickup): the button lists them, and the user picks one.
2. **An e-mail transition that sends a document.** In Arrange AUV Contract, Not Started, the
   only e-mail transition is Send AUV, which renders and sends the contract. The button
   opens Send AUV; it offers no plain e-mail next to it (decision 3).
3. **A transition with a gate.** Send AUV refuses when a required field is empty. The chatter
   button then shows the same error as the header button. No new rule.
4. **A state that the bot owns** (`is_owned_by_bot`, Arrange MFNL Notification): a person
   does not run the bot's transitions there, so the button falls back as with no transition.
5. **An e-mail transition went out, and no reply came yet.** Path A applies again, so the
   user sends Resend or Send Reminder, which the workflow records.
6. **An incoming e-mail that is not part of a conversation** (a bounce, an auto-reply, a
   copy that the team-channel rules posted): the reply wizard proposes its sender. A bounce
   already opens its own review service. No new rule.
7. **A reply that carries a document** to a child Send Email service: decision 7 makes the
   pages reachable from the parent's credential wizard.
8. **Colleague messages on a record with outside followers.** Today a Send message "to the
   colleague" on such a record also e-mails the agency or the supplier. With Log note and an
   @mention for colleagues (decision 5), this stops.
9. **The fallback service starts with an empty "To"** (decisions 2 and 6). The user picks the
   contact; the wizard refuses a send without one. Help to find the right contact is the
   later topic of decision 11.
10. **Bots and code** post with `message_post` and do not use the chatter button. No change.
11. **Staff and team contacts are top-level contacts too** (15 active contacts belong to a
    user). Decision 15 makes no exception: Send Email there creates a Send Email service, as
    on any contact.

## 7. Inventory: every kind of staff e-mail and its home

*Source: `crmain`, 7-Nov-2026, staff messages of the last 365 days with the subtype
Discussions, outside Discuss. Each kind gets one home, and the pit of success is the path
that the screen offers first.*

1. **Transition e-mails** (8,746 on services): Send Email, Order Now, Send AUV, Request
   Appointment and the other e-mail transitions. *Home:* the service, through its workflow.
   No change, except decision 6 (the writer is never a recipient).
2. **Replies in a conversation that a transition started** (about 860 on services in 180
   days; agencies, suppliers, skippers). *Home:* the same service, through the reply wizard
   (§ 5 B).
3. **Replies that staff write from their own mailbox** (248 on services, 11 on employees, 7
   on invoices, 4 on contacts, 1 on a log entry). The mail gateway posts them into the
   thread they answer. *Home:* that thread. No change.
4. **Messages to colleagues** (132 on services in 180 days, and most of the few on contacts
   and credentials). *Home:* a Log note with an @mention on the record (decision 5).
5. **A new e-mail about a service, a log entry, an employee or a ship** (Dolf's request; on
   employees 104 in the year: "please sign the General Terms", an enrolment for the exam,
   the letter about an A1 document, a taxi time, a note to the agency KMS). *Home:* the e-mail
   transition of the service, else a Send Email service on that record (§ 5 A). A kind that
   comes back often (the A1 letter to the employee, the General Terms) is a candidate for its
   own transition later; this plan does not build those.
6. **Invoice e-mails** (about 880 from the invoice Send button in 180 days, 48 payment
   reminders, about 15 questions). *Home:* the invoice and Odoo's Send message
   (decision 10).
7. **The yearly confirmation for the German authorities** (63 in Q1-2026, on the client
   companies). The client signs it and sends it back ("Hierbij retour"), so it is a yearly
   document request, not an ad-hoc e-mail. *Home:* a Send Email service on the client's
   top-level contact (§ 5 D), one per client, from Q1-2027 on (decision 16).
8. **A conversation about a client's contact data** (Luboil: two questions to the colleague
   Bas, and 3 replies of clients on contacts in 180 days). The questions to Bas are kind 4.
   A new e-mail to the client goes through a service on its top-level contact (§ 5 D). An
   incoming reply on a contact gets the reply wizard there (decision 17).
9. **Credentials** (4 in the year, all to colleagues or a remark). *Home:* Log note
   (decision 9).
10. **An e-mail with no record at all.** 10 root Send Email services exist (no subject
    record), made through Radar › New › New Service › Send Email; 4 in the last year, and
    most are tests ("Testbericht", "Test email Ries/Kirsten/Fred"). The others had a record
    that fits: "Send Pension Fund info to employee" (the employee), "Book Hotel" (the log
    entry). *Home:* the record that fits; else Radar › New › New Service › Send Email
    (decision 18).

## 8. Open questions

None. Q1 to Q12 were answered on 8-Nov-2026 (decisions 2 to 5 and 9 to 18).

## 9. Build (8-Nov-2026, `feat/send-message`)

What was built, by module:

- **riverflow.** The flag `sends_email` on `riverflow.transition.action` (set on Send Email
  Action). `models/mail_thread_send_email.py`: the mode per record
  (`_riverflow_chatter_email_mode`, sent to the chatter as `riverflowEmailMode` with the ids
  of the incoming e-mails), `action_riverflow_send_email`, `action_riverflow_reply` and the
  fallback service. `riverflow.mail.send.mixin` holds the recipient rules for the Send Email
  wizard and the reply wizard. New wizards `riverflow.mail.reply.wizard` and
  `riverflow.send.email.choice`. The create mode of the transition wizard starts a new
  record in the transition's from-state, so the fallback service is made only on Send.
  Services on a top-level contact: `service_ids` and a Services tab with Add Service on
  `res.partner`, for service readers. JS: `chatter_send_email_patch.js` (the button, the
  Reply action, the guard on Odoo's Send message).
- **rivercreds.** A credential is `note_only`; the credential wizard offers the pages of the
  child services (`_chatter_services`).
- **crewradar_sign, crewradar_cuneus_sign.** `sends_email` on Generate & Email, Request
  Appointment, Send Permit + Letter and Request Return, with a post-migrate in 19.0.1.337
  and 19.0.6.348 (the records are noupdate).
- **Tests.** `riverflow/tests/test_chatter_send_email.py` (10 tests); two tests in
  `rivercreds/tests/test_credential_wizard.py`. The full suite ran with 0 failures; the one
  oteny_bot error it found (the contact form read services for a user without service
  rights) is fixed by the group on the Services tab.

Where the build differs from § 5:

- The first build had a selection `email_kind` ("plain" or "document") for the first
  decision 3. The revised decision 3 (9-Nov-2026) made "document" change no behaviour, so
  the flag became the boolean `sends_email` of § 5 A.
- Services on a contact live in riverflow, not crewradar: the Send Email button of a
  contact needs them, and `res.partner` and the services are both riverflow's concern.
- Edge case 4 (a state that the bot owns): the chatter lists the transitions that the form
  header offers, so it follows the header's bot rules; no separate rule.
- Odoo's Reply All and Forward open their own full composer; on a record with Send Email they
  are hidden, so Send Email stays the one door (decision 1).
- The fallback service takes the team of the service, else the writer's home team, because
  the Send Email wizard requires a responsible team.
- The chatter hides Odoo's Send message until the thread data says which button applies, so
  the old button does not flash while a riverflow record loads.

