# Plan: the transition in the audit log

Status: design agreed with Thijs on 26-Oct-2026; built on 26-Oct-2026 on the branches `feat/audit-remarks` (otenydoo, radar), not committed. oteny_audit 19.0.1.687, riverflow 19.0.1.1373, crewradar_wilma 19.0.2.347. Tests: oteny_audit 0 failed of 110; riverflow `test_transition_audit_remark` 0 failed of 8; full radar suite 1 failed and 7 errors of 5,483, all from the state of `cr-test` (see the outcome below). Basecamp: [BC 10447209690](https://3.basecamp.com/5527227/buckets/38887735/todos/10447209690).

## Why

The audit log records the change of `state_id`, but not the transition that made it. When two
transitions lead to the same state, nobody can tell which button a user pressed.

Example on production: service 33239 (Send A1 to Client, child of Arrange A1 Application 21506 on
log entry 9178). On 16-Oct-2026 at 08:45:54 UTC Kirsten moved it from Awaiting Ship A1 to Ship A1
Received. Two transitions do that (`crewradar_cuneus_sign/data/a1_recipient_workflows.xml`):
Upload Ship A1 and Received already. Both have `set_deadline_to_today`, so they write the same
values. Only an elimination shows that it was Received already: transaction `2347871369` holds
only 7 field changes on the service, and no `rivercreds.credential` and no `ir.attachment`, while
Upload Ship A1 always creates both, and both models are audited (676 and 2027 rows since
15-Oct-2026). At 07:11 the same day, the service went back from Ship A1 Received to Awaiting Ship
A1. Only Back does that, but the log does not say so either.

Two causes:

- `oteny_audit` logs only changes of stored field values. The transition wizard writes
  `state_id` and the deadline fields; the transition itself is stored nowhere.
- `oteny_audit` turns off Odoo's own chatter tracking (`MailThread` with `tracking_disable`), so
  the chatter does not show the state change either.

A transition back to the same state (for example Resend Application) changes no state, so the
audit log can have no row for it at all.

On production, services change state about 3,000 to 4,000 times per month (July to October 2026:
4032, 3068, 3690, 2680 `state_id` rows).

## How it works

Today the audit log writes a row only when a value in a record changes. After this change, it can
also write a remark. A remark is a short line of text that a program adds to the audit log of a
record, for example "Transition: Received already". It does not say that a value changed. It says
what a person or a process did.

The audit module gets one new function to add a remark. Any module can use it. The function
writes the remark like every other audit row: with the user, the date and time, the record, and
the database transaction. Because the remark has the same transaction as the changes that the
action made, the audit log shows them together. The remark also shows in the audit log of the
parent records, for example on the log entry above a service.

Riverflow uses this function at two places:

- When a user, a bot or the system runs a transition, the transition wizard adds a remark with the
  name of the transition, for example "Transition: Received already". This also works when the
  transition does not change the state, for example Resend Application.
- When a service changes state because of its parent or its child services, and not because of a
  transition, riverflow adds a remark that names the cause, for example "Transition: Done.
  Executed by parent.".

Riverflow adds a remark only when the audit module is installed. Without the audit module, riverflow
works as it does today.

In the audit log of service 33239, Kirsten's click on 16-Oct-2026 would then show like this:

> Remark Service *Send A1 to Client* by **Kirsten** on 16-Oct-2026 10:45
> Transition: **Received already**
> Update Service *Send A1 to Client* by **Kirsten** on 16-Oct-2026 10:45
> Updated **State** from 'Awaiting Ship A1' to 'Ship A1 Received'

A user can find all remarks with the new filter "Remarks" in the audit log.

## Decisions

1. **oteny_audit gets a new change type `'r'` (Remark) and one generic function that adds a
   remark line to the audit log** (Thijs, 26-Oct-2026). Any module can call it; oteny_audit knows
   nothing about riverflow. Signature:
   `env["oteny.audit.log"]._log_remark(records, key, label, text)`, one line per record. The
   leading underscore keeps it out of the web API (`/json/2/` and RPC refuse private methods), so a
   user cannot write a false line. A remark is not a field change: it holds no field value, and it
   is never confused with an update row.
2. **The line uses the path of a normal change**, `_create_audit_logs` in
   `oteny_audit/models/base_patch.py`. So it gets the same `transaction_id` (`txid_current()`),
   the same user (`create_uid` is the user of the environment, also under the `sudo()` of that
   helper), the same parent references (`_create_audit_log_references`, so the line also shows on
   the log entry above the service), and the same skip rules: `oteny_audit_ignore` in the
   context, registry not loaded, restore detected, a model in `_is_audit_ignored`, and a record in
   `_oteny_audit_ignore_record`. A skip writes nothing and raises nothing: an audit line must
   never stop the transition.
3. **The values of a remark row:** `change_type = 'r'`; `field_name = key`, a fixed technical key
   of the caller (for example `transition`, `process`), the same in every language, so a filter on
   one kind of remark keeps working; `field_display_name = label` (for example "Transition"), in
   the user's language; `new_value` and `new_value_display_name = text` (for example "Received
   already"); `old_value` and `old_value_display_name` empty; `model_name`, `record_id` and
   `record_display_name` of the record. `field_name` is required (`not null`), which is why the
   caller passes a key.
   - `('r', 'Remark')` is added to the `change_type` Selection of `oteny.audit.log` and of
     `oteny.audit.log.aggregated`. The column is a `varchar`, so no migration is needed.
   - The aggregated view unnests only `__snapshot__` rows, so a remark row passes through as it
     is.
   - `_compute_caption`: the header verb is "Remark", and the line is "**label**: text". Because
     the header groups rows by record, change type and transaction, a remark gets its own header,
     next to the Update header of the same transaction:

     > Remark Service *Send A1 to Client* by **Kirsten** on 16-Oct-2026 10:45
     > Transition: **Received already**
     > Update Service *Send A1 to Client* by **Kirsten** on 16-Oct-2026 10:45
     > Updated **State** from 'Awaiting Ship A1' to 'Ship A1 Received'

   - `_is_html_field` finds no field named like the key, or the key names no HTML field, and the
     text is escaped as plain text. The build checks that a key that is also a field name of the
     model (for example a remark with key `state_id`) cannot be rendered as HTML.
   - The two search views of `oteny_audit_log_views.xml` get a filter "Remarks"
     (`[('change_type', '=', 'r')]`) next to Inserts, Updates and Deletes.
   - A remark holds no field value, so the rebuild of a deleted record (insert snapshot, then the
     updates, then the delete snapshot) skips `r` rows. The `oteny-audit` skill says so.
4. **riverflow writes the remark after the state write.** In the transition wizard, the call comes
   after `record.write(write_vals)` and `record.flush_recordset()`, so the remark belongs to the
   transaction of the state change. Because a remark has its own header, its position next to the
   Update rows does not matter for the caption.
5. **riverflow does not depend on oteny_audit.** A small helper on `riverflow.state.mixin`, for
   example `_audit_log_remark(key, label, text)`, calls the function only when
   `"oteny.audit.log" in self.env`. Without oteny_audit, nothing happens. The manifest does not
   change its `depends`.
6. **riverflow writes a remark in `action_save` of the transition wizard**
   (`riverflow/wizards/riverflow_transition_wizard.py`), key `transition`, label "Transition",
   text `transition.name`, one line per record in `records_to_transition_ids`:
   - for an existing record: after the state write and its flush, before the cascades;
   - for a new record (a Start transition from no state): after the create, when the record has
     its id.
   This covers every transition that runs through the wizard:
   - a button click and a kanban drop (`action_kanban_drop` opens the same wizard);
   - a bot transition (`_bot_claim_run_wizard`) and the closing transitions of child services
     (`_execute_transition_in_code`), which save the same wizard; the user on the line is the bot
     user or the person who started it;
   - every wizard that inherits the transition wizard. The overrides of `action_save` in radar
     call `super().action_save()`: `rivercreds.credential.wizard` (also the Upload Ship A1
     upload), the crewradar_wilma extension, the MFNL wizards (through
     `_confirm_browser_then_save` in `mfnl_browser_confirm.py`), the work permit wizards, the
     weekly bulk upload, the sign fill wizard and the register successor wizard. The build
     checks each one again.
   - A transition back to the same state also writes a remark.
   - A save check that holds the wizard (`riverflow.save.check.mixin`) returns before the state
     write, so it writes no remark. A refused save (for example "Another user just updated this
     record") rolls back the transaction, the remark included.
7. **The three state changes without a transition also write a remark that names the process**
   (Thijs, 26-Oct-2026), in `riverflow/models/riverflow_service.py`, each after its own state
   write, on the service whose state changes, key `process`, label "Transition" (Thijs,
   28-Oct-2026: the same label as a real transition; the key `process` still tells the two apart):
   - the Done cascade (`_cascade_done_to_children`, `child.state_id = done_state`): text "Done.
     Executed by parent.";
   - the cancel cascade (`_cancel_end_state`, `child.state_id = cancel_state`): text "Cancelled.
     Executed by parent.";
   - the parent auto-progress (`_auto_progress_to_next_state`, `self.state_id = next_state`):
     text "*name of the new state*. Executed by child services.".
   The texts are translatable (`_()`). The user on the line is the person whose transition on the
   other service started the process.
8. **Wilma learns the new change type.** Two texts tell Wilma that the change types are only `i`,
   `u` and `d`: the audit model description in
   `crewradar_wilma/models/riverflow_service_analysis.py` (the "Captured-value mapping by
   change_type") and the audit table in `crewradar_wilma/data/knowledge_skill_packs.xml`. Both
   name `'r'` remark: the key in `field_name`, the label in `field_display_name`, the text in
   `new_value_display_name`, and no field value. Without this, Wilma can read a remark as a field
   change.

## Cost

- One remark row and one or more reference rows (direct, plus the parents up to 3 levels) per
  transition: about 3,500 rows per month on production, which is small next to the field rows
  that each transition already writes (7 for service 33239 alone).
- One extra `INSERT` per transition, inside the transaction that is already open. No extra
  `SELECT` for the parent references beyond what a field change already costs.
- The retention is the same as for every audit row: 180 days (`oteny_audit.retention_days`).

## Not in scope

- No backfill. Lines exist only for transitions after the deploy; service 33239 stays as it is.
- No chatter note per transition.
- No change to how `oteny_audit` logs field changes.

## Build outline

Work in `~/oteny/otenydoo` on a `feat/` branch, and in `~/oteny/radar` on a `feat/` branch of the
same name for the Wilma texts (decision 8). Commit otenydoo first, then radar, with
`python -m riverdeploy commit-with-otenydoo`, which also moves the radar submodule pin.

1. `oteny_audit`:
   - `('r', 'Remark')` in both `change_type` Selections;
   - `_log_remark` on `oteny.audit.log` (`models/oteny_audit_log.py`), which builds the log dicts
     and calls `_create_audit_logs`;
   - the verb "Remark" and the `r` branch in `_compute_caption`
     (`models/oteny_audit_log_aggregated.py`);
   - the filter "Remarks" in the two search views (`views/oteny_audit_log_views.xml`);
   - manifest version and version note.
2. `riverflow`:
   - `_audit_log_remark` on `riverflow.state.mixin`;
   - the calls in `action_save` (both paths of decision 6) and in the three methods of
     decision 7;
   - manifest version and version note.
3. `crewradar_wilma` (radar): the two texts of decision 8; manifest version and version note.
4. Skills (ask first, CLAUDE.md § 5 step 3): a short section "Remarks" in
   `oteny-audit/SKILL.md` (the change type, the function, the key and label, the skip rules, no
   field value, skipped by the rebuild of a deleted record), and the change type in its table of
   row shapes; one line in `riverflow/SKILL.md` that each transition and each process writes a
   remark when oteny_audit is installed.

## Tests

- `oteny_audit` (new file `tests/test_audit_log_remark.py`, on the audit test models of
  `models/oteny_audit_test_models.py`):
  - `_log_remark` writes one row per record, with `change_type = 'r'`, the key, the label and the
    text, and the `transaction_id` of a field change in the same transaction;
  - a child record's remark gets a parent reference, so it shows in the parent's audit log;
  - the caption has the header "Remark ..." and the line "**label**: text", apart from the
    Update header of the same transaction; a text with HTML is escaped, also when the key is the
    name of an HTML field of the model;
  - the filter "Remarks" finds the row in the aggregated view;
  - it writes nothing under `oteny_audit_ignore`, and nothing for an ignored model.
- `riverflow` (new file `tests/test_transition_audit_remark.py`; skip the class when
  `oteny.audit.log` is not in the registry, because the otenydoo CI in `.github/workflows/tests.yml`
  does not install riverflow and the radar suite installs both):
  - two transitions from one state into the same state give two remarks with different texts;
  - a transition back to the same state gives a remark;
  - the remark has the `transaction_id` of the state change;
  - a Start transition that creates a record gives a remark on the new record;
  - a save check that holds writes no remark;
  - each of the three processes of decision 7 gives its remark on the service whose state
    changed.
- Run the oteny_audit and riverflow tests, then the full radar suite
  (`.claude/skills/crewradar-development/references/running-tests.md`), because every service
  transition passes through the changed code.
- Check in the browser on the local server (`preview_start` name `crmain`): run Received already
  on a Ship A1 child service and open its Audit Log; the Remark header and the line "Transition:
  Received already" show next to the Update header.

## Outcome (26-Oct-2026)

- Built as designed. Two changes against the decisions above: the riverflow test of the save
  check moves the parent service by hand into an end state, because the incomplete-children
  warning of `riverflow.service.wizard` holds only for an end state; and a remark of a process
  (decision 7) is written before the flush of its state change, so its Remark header shows below
  the Update header of the same transaction (the audit views sort by `id DESC`). Both headers have
  the same user and time.
- Proof on `crmain` (a production copy), in an Odoo shell: Received already on service 39038
  (Send A1 to Client, Awaiting Ship A1) wrote the remark "Transition: Received already" under its
  own Remark header, directly above the Update header with the state change and the deadline
  fields. The browser walk did not run: the built-in browser pane gave 0 animation frames while
  the page was visible, so Odoo did not draw (the case "a covered window gives no frames" in
  `crewradar-development/references/browser-walk.md`).
- The full radar suite on `cr-test`: 1 failed and 7 errors of 5,483, none in code that this work
  changed. `TestFormSessionPartner` (5 errors, oteny_bot): `KeyError: 'total_all_due'`, a field
  of an Enterprise module that this addons path no longer loads, while the partner view of
  `cr-test` still names it. `TestSiteLifecycle` (1 failure, 2 errors, crewradar): `cr-test` still
  holds the state `crewradar.state_site_archived` (id 93, created 8-Oct-2026), which the test says
  must not exist. Both are stale data in the test database.

## Outcome (28-Oct-2026)

- The three remarks of decision 7 have the label "Transition" (riverflow 19.0.1.1380).
  `test_transition_audit_remark`: 0 failed of 8 on `cr-test`.
- Proof on `crmain`, in an Odoo shell transaction that was rolled back. The transition Cancel on
  A1 application 20564 wrote "Transition: Cancelled. Executed by parent." on its child service
  22221. AUV service 38560 was set to Done, followed by `_cascade_done_to_children()`, because the
  save check of Signed AUV Received holds without the signed AUV. That wrote "Transition: Done.
  Executed by parent." on child service 38561.
- The parent auto-progress cannot run on `crmain`. Its only state, Send Issued A1 of Arrange A1
  Application, has no transition into it, and the next state, Done, is hidden in the statusbar,
  which `_auto_progress_to_next_state` skips.
