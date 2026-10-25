---
name: oteny-oops
description: Generic Odoo 19 Community module oteny_oops — the Oops Log. Every time the web client opens its error dialog ("Oops! Something went wrong…"), the dialog writes the fault to the model oteny.oops.log (the error, the message, the server and client traceback, the page, the user, the time) and shows a link to the record. Only Settings administrators read it, under Settings › Technical › Oteny › Oops Log (debug mode). Covers what is written and what is not, the fingerprint and the repeat guard, the 1 MB limit, the daily clean-up (oteny_oops.max_age_days, default 30), the files and the tests. Use when you read a user's fault, debug why the dialog shows no link, change the Oops Log, or replace the Share button of Enterprise Documents on Community.
---

# Oops Log (oteny_oops)

On Odoo Enterprise, the Documents app adds a **Share** button to the error
dialog: a click saves the error text as a file in the folder "Support". Odoo
Community saves nothing: the server writes the server traceback only to its
log file, and an error that happens only in the browser is not written
anywhere. This module writes every fault that a user meets in the web client
to the Oops Log in the database.

## When to Use This Skill

- **Administrators**: Read the fault behind a user's bug report: open the link
  that the user pasted, or Settings › Technical › Oteny › Oops Log in debug
  mode.
- **Developers**: Change what is written, debug a dialog that shows no link,
  run the tests.
- **AI Agents**: Read the record of a fault (`oteny.oops.log`, over JSON-2 as
  an administrator) before you guess a cause from a screenshot.

The build plan and its decisions (D1 to D13) are in
[plans/2026-10-25-plan-1-oops-log.md](plans/2026-10-25-plan-1-oops-log.md).

## What Happens

1. The web client opens `ErrorDialog` or one of its subclasses
   (`RPCErrorDialog`, `ClientErrorDialog`, `NetworkErrorDialog`,
   `RequestEntityTooLargeErrorDialog`).
2. The patch `static/src/error_dialog_patch.js` sends the text of the dialog
   to `/oteny_oops/log` when the dialog is mounted: the exception name, the
   message, the traceback (the server part first, then the client part), the
   type, the model of the failed call and the URL of the page. It sends in a
   new request, because the request that failed has rolled back its
   transaction. It sends the user's context, so the record gets the current
   company.
3. The route refuses a user who is not internal (`Forbidden`) and calls
   `oteny.oops.log._log_error` with `sudo()`.
4. The dialog shows "Oops Log <id>" as a link and a copy button.

**Not written:** a `UserError`, `ValidationError`, `AccessError`,
`AccessDenied`, `MissingError` or `RedirectWarning` opens `WarningDialog` or
`RedirectWarningDialog`, which are no `ErrorDialog`; a session expiry and a 504
open their own dialogs. Those are messages the code means to show. Errors
without a dialog (a scheduled action, a JSON-2 or XML-RPC call, a portal page)
stay in the server log only (plan, D4).

**A failed call shows nothing.** The patch catches every error of its call to
`/oteny_oops/log`. A second error dialog would send the error again, and a
fault in the route would loop. The dialog then shows no link; its clipboard
button still copies the text.

## One Record per Fault and User

- **Fingerprint**: the SHA-1 of the exception name and the frames of the
  traceback, without line numbers and asset URLs, because a deploy changes
  them. When the traceback has Python frames (`File "…", line …, in …`), only
  their file and function count: the client part of a server error is the
  same call machinery for every server fault, and it differs per screen. Else
  the function names of the JavaScript frames (`at <function> (…)`) count;
  without any frame, the first line of the message. Two faults in the same
  function share a fingerprint; the traceback still shows the line. Search
  view: group by **Fault**.
- **Repeat guard**: the same user with the same fingerprint within 10 minutes
  of the last time adds 1 to **Count** and sets **Last Seen** on that record
  (`REPEAT_MINUTES`, a constant). An archived record does not count: the next
  time makes a new record.

## Limits and Clean-up

- **1 MB per record**: 1,048,576 bytes, counted in UTF-8 over all text fields.
  The title is cut at 200 characters, the URL and the browser at 2,000; the
  message and the traceback share the rest. The traceback gives way first,
  then the message; each keeps its start and ends with the line
  `[cut: an Oops Log record holds at most 1 MB]`.
- **Request limit**: the route reads at most 2 MiB
  (`max_content_length=2 << 20`), because JSON escapes line breaks and quotes.
  A larger request gets the JSON-RPC error
  `werkzeug.exceptions.RequestEntityTooLarge` (the HTTP status stays 200), and
  the dialog shows no link.
- **Clean-up**: `_gc_old_records` (`@api.autovacuum`) deletes the records whose
  **Last Seen** is older than the system parameter `oteny_oops.max_age_days`
  (default `30`, created `noupdate`), archived records too. Odoo's scheduled
  action "Base: Auto-vacuum internal data" runs it once a day; the module has
  no scheduled action of its own.

## Access and Screens

- Only `base.group_system` reads, archives and deletes; nobody creates or
  edits a record by hand (no create right, every data field is read-only).
  Why: a traceback can hold personal data. A user without administrator
  rights who opens the link gets "You are not allowed to access 'Oops Log'".
- Menu: Settings › Technical › Oteny › Oops Log (`menu_oteny_oops_log`,
  sequence 20, in the section `oteny_base.menu_oteny`). The Technical menu
  needs debug mode.
- Link: `/odoo/action-oteny_oops.action_oteny_oops_log/<id>`; the dialog makes
  it absolute with the page's origin.
- With Enterprise Documents installed too, the dialog shows both the Share
  button and the Oops Log link.

## Files

| File | Role |
|------|------|
| `models/oteny_oops_log.py` | The model, `_fingerprint`, `_log_error` (repeat guard, 1 MB cut), `_gc_old_records` |
| `controllers/main.py` | `/oteny_oops/log` |
| `static/src/error_dialog_patch.js` / `.xml` | Sends the fault when the dialog opens; shows the link and the copy button |
| `views/oteny_oops_log_views.xml` | List, form, search, action, menu |
| `data/ir_config_parameter.xml` | `oteny_oops.max_age_days` = 30 |

## Tests

- Python (tag `oteny_oops`): `tests/test_oteny_oops_log.py` (fingerprint, repeat
  guard, 1 MB cut in bytes, access, clean-up, menu) and
  `tests/test_oteny_oops_route.py` (an internal user's fault is written; a
  portal user and a request above 2 MiB are refused).
- Hoot: `static/tests/error_dialog_patch.test.js` (a server error writes one
  record and shows the link; a failed call opens no second dialog; a warning
  dialog and a portal user send nothing). One file:
  `--test-tags "/web:WebSuite.test_unit_desktop[@oteny_oops/error_dialog_patch]"`
  without `-u`. Without the patch file, the first two fail (checked
  25-Oct-2026).
- The command in the plan's "Global constraints" runs both on a Community-only
  addons path.
