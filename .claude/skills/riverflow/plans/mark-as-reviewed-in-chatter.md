# Mark as Reviewed in the chatter

> **Status: BUILT (23-Oct-2026), not committed.** Riverflow 19.0.1.1358; build record in [§ 6](#6-build-record-23-oct-2026). Ries agreed the design and the
> answers to Q1 and Q2 on 23-Oct-2026. Basecamp: [BC 10438133198](https://3.basecamp.com/5527227/buckets/38887735/todos/10438133198).
>
> **Why.** The HR Inbox on the radar Radar (`ir.filters` 50: Review > 0 and
> Responsible = Human Resources) lists rows that HR cannot clear. A reply on an
> employee, a ship or a log entry raises `unreviewed_message_count`, but only the
> service form has the button "Mark as Reviewed". On the production copy of
> 23-Oct-2026 the HR Inbox held 4 such employee rows (employees 577, 594, 595).
>
> **Sources.** `models/riverflow_mail_thread_review_mixin.py`,
> `views/riverflow_service_views.xml`, the Odoo 19 chatter in
> `~/odoo/odoo19/addons/mail/static/src/chatter/web/` (`chatter.xml`,
> `chatter_patch.js`, `form_compiler.js`).

## 1. Today

`riverflow.mail.thread.review.mixin` gives five models the count
`unreviewed_message_count` and the method `action_mark_external_messages_reviewed`:
`riverflow.service`, `hr.employee`, `crewradar.site`, `crewradar.log.entry` and
`rivercreds.credential`. The method works on all five. Only the service form shows
the count and the button, in its body. A model that takes the mixin gets the count,
the Radar column and the inbox rows, but no way to clear them.

## 2. Decisions

- **D1. The count and the button live in the chatter top bar, for every model with
  the mixin.** The chatter top bar of every form shows "N to review" and the
  button "Mark as Reviewed" between Send message / Log note / Activity on the left
  and the search, attachment and follower icons on the right (before the spacer
  `o-mail-Chatter-topbarGrow` of the template `mail.Chatter`). Both show only when
  the count is above 0. No view of a model with the mixin changes; a new model
  that takes the mixin gets the button by itself. (Ries, 23-Oct-2026)
- **D2. The count reaches the chatter with the thread data.** A form loads only the
  fields of its view, so the chatter does not read the count from the form record.
  The mixin adds `unreviewed_message_count` to the thread data that the server sends
  to the chatter (the `mail.thread` store hook of Odoo 19, `_thread_to_store`; the
  build checks the name and signature). A record without the mixin sends no count,
  so its chatter does not change.
- **D3. The button calls the existing method.** A click calls
  `action_mark_external_messages_reviewed` on the record, then reloads the form
  record (when the chatter has one) and the thread data, so the counter goes. No
  Python change to the method.
- **D4 (Q1). The service form loses its own copy.** The field "Review Messages" and
  the button in the body of the service form (`riverflow_service_views.xml`, the
  `unreviewed_message_count` field and the `action_mark_external_messages_reviewed`
  button) are removed, so the button is in one place on every record. The Radar
  column "Review", the filters "Needs Review" and the toggle of the Supplier
  Confirmed wizard stay. (Ries, 23-Oct-2026)
- **D5 (Q2). The counter is live.** When an external message arrives on a record
  that is open, the counter updates at once. The mixin's `_message_create`, which
  already handles each external message, sends the new count to the thread in the
  store through the bus. Cost: one small bus notice per external message.
  (Ries, 23-Oct-2026)

## 3. Build

All in riverflow (otenydoo side clone); crewradar and rivercreds do not change.

- The mixin: the count in the thread data (D2) and in the bus notice of a new
  external message (D5).
- `static/src/`: a template patch of `mail.Chatter` (counter and button, D1), a
  patch of the Chatter component (the click, D3), and an SCSS rule for the counter
  in light and dark mode.
- `views/riverflow_service_views.xml`: remove the copy of D4.
- Manifest: the assets and a version note.

## 4. Tests and checks

- Python: the thread data of a service carries the count; the thread data of a
  record without the mixin (a `res.partner`) does not; after
  `action_mark_external_messages_reviewed` the count is 0.
- Hoot: the button shows when the count is above 0 and hides at 0; a click calls
  the action and hides the button; a bus notice with a new count shows the button.
- Browser walk on `crmain`: the employee Gacotano (577) shows "4 to review" in the
  chatter; Mark as Reviewed clears it, and the employee leaves the HR Inbox. The
  same on a service, whose form body no longer shows the button.

## 5. Open items

- [x] Build D1–D5 with the tests of § 4 (23-Oct-2026). · [BC 10438133198](https://3.basecamp.com/5527227/buckets/38887735/todos/10438133198)
- [x] Browser walk on `crmain` (23-Oct-2026); screenshots for the HR test follow at the deploy to test1.
- [ ] Outcome pass: the chatter mixin section of the riverflow `SKILL.md` names the
  chatter button, a radar release note, and the status of this plan.

## 6. Build record (23-Oct-2026)

**What was built** (otenydoo side clone, riverflow 19.0.1.1358, no migration):

- `models/riverflow_mail_thread_review_mixin.py`: `_thread_to_store` adds
  `unreviewedMessageCount` when the request list asks for it (D2);
  `_bus_send_unreviewed_message_count` sends the count to the internal followers;
  `_message_create` calls it for a message created by the system user (the rule of
  the count), and `action_mark_external_messages_reviewed` calls it after the write
  (D3, D5).
- `static/src/components/chatter_review/`: `chatter_review.xml` (the pill before
  `o-mail-Chatter-topbarGrow`, shown while the count is above 0),
  `chatter_review_patch.js` (the Thread attribute, the request key, the click:
  action, count 0, reload of the thread data and of the form record) and
  `chatter_review.scss` (one pill: a dot and "N to review", then "Mark as
  Reviewed", in `$o-action`; Odoo's SCSS variables, so the dark bundle gets its own
  colours).
- `views/riverflow_service_views.xml`: the field "Review Messages" and the button
  are gone from the service form body (D4).

**Who sees the live count (D5).** The bus notice goes to the internal followers of
the record: the same users whose open chatter gets the new message live. A user who
looks at a record without following it sees the new count at the next load, as they
see the new message.

**Findings.**

- Odoo 19 does not emit the Bootstrap CSS variables (`--bs-info-bg-subtle`,
  `--bs-border-radius-pill`) in the backend; a style that uses them falls back to no
  colour and no radius. Use Odoo's SCSS variables (`$o-action`, `$border-color`,
  `$o-main-text-color`), which the dark bundle compiles with dark values.
- In a flex item, a leading space of a text node is dropped: "4 to review" showed as
  "4to review" until the number and the text shared one inline element.
- `last_external_message_review_time` keeps whole seconds, so a message created in
  the same second as the click still counts as unreviewed. A person reads before the
  click, so this does not happen in use; the test dates its message one minute back.
- The built-in browser pane, when hidden, gets no animation frames while
  `document.visibilityState` stays "visible", so Owl applies no render and
  `hidden_page_animation_frames.js` does not switch on (23-Oct-2026). The service
  walk was proved by flushing the render queue by hand.

**Tests.**

- `tests/test_review_chatter.py` (tag `test_review_chatter`): 3 tests, green.
- `static/tests/chatter_review.test.js` (hoot): green; the Odoo suite
  `@mail/chatter` with the patch loaded: 90 passed, 1 skipped by Odoo.
- All riverflow tests on `cr-test`: 0 failed, 0 errors.

**Browser walk on `crmain`** (production copy of 23-Oct-2026, server without
upgrade; `--dev xml` reads the views from the files):

- Employee Gacotano (577): the chatter top bar showed "4 to review" and Mark as
  Reviewed between Log note / Activity and the search icon. After the click the pill
  went; `hr_employee.unreviewed_message_count` 4 → 0, Radar row 38276 4 → 0, and the
  employee left the HR Inbox (`ir.filters` 50: employee rows 3 → 2, left 594 and
  595).
- Service 3656 (Arrange Work Permit at AB, count 2): the form body has no "Review
  Messages" and no button; the chatter shows "2 to review" and Mark as Reviewed.
- Dark bundle: the pill has border `#3C3E4B`, text `#E4E4E4` and the dark action
  colour; light: `#d8dadd`, `#111827`.
