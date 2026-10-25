# Oops Plan 1 — The Oops Log on Odoo Community

Status: built on 25-Oct-2026, not committed (Ries asks for the commit).
Decisions of Ries on
25-Oct-2026: the record is made when the dialog opens (D2), plan 1 covers
the faults that a user sees (D4), the name Oops Log (D5), 1 MB per record
(D8), the section "Oteny" under Settings › Technical with the new module
`oteny_base` as its owner (D10, D13), and the clean-up after 30 days (D12).

**Goal:** a generic Odoo 19 Community module `oteny_oops` that writes every
fault a user meets in the web client to the Oops Log in the database. A
Settings administrator reads the Oops Log under Settings › Technical ›
Oteny. The user gets a link to the record in the error dialog, to paste in a
bug report. One Oops Log record holds at most 1 MB of text, and a daily
clean-up deletes the records that are older than a maximum age (default 30
days).

Two more changes come with it: the new module `oteny_base` owns the section
"Oteny" under Settings › Technical, and the menu "Cloud Filestore Cache" of
`oteny_cloud_filestore` moves into that section.

**Tech stack:** Odoo 19 Community, Python 3.12, Owl, `TransactionCase`,
`HttpCase` and hoot tests. `oteny_oops` depends on `base`, `web` and
`oteny_base`.

## Why

On Odoo Enterprise, the Documents app adds a **Share** button to the error
dialog ("Oops! Something went wrong…"). A click saves the error text as a file
in the Documents folder "Support" and copies a link to it
(`documents/static/src/web/error_dialog/error_dialog_patch.js`,
`shareTraceback`; route `/documents/upload_traceback` in
`documents/controllers/documents.py`). The file holds the error, the message,
the server, the model, the time, the server traceback and the client
traceback.

- Radar's production had 74 such files from 12 users on 24-Oct-2026. They
  were dropped with Documents at the switch to Community (radar community
  plan, D11, Ries 21-Oct-2026).
- On Community, the error dialog makes no record. "See technical details"
  shows the traceback, and its clipboard button copies the text.
- The server writes the server traceback to its log file ("Exception during
  request handling.", `odoo/http.py`, `Application.__call__`), but not to the
  database. On the production copy test3, `ir.logging` has 68 rows, all
  start-up warnings of `odoo.tools.config`, and no error of a user's request.
  An administrator cannot see the log file from Odoo.
- An error that happens only in the browser (an Owl error, a JavaScript
  `TypeError`) never reaches the server log.

Example of the value: radar Basecamp to-do 10436098078 (23-Oct-2026). The
user's file (document 7518 on test3) named the faulty line and the cause in
one read: `_get_credential_plan_note` calls `rstrip` on the empty company name
of a log entry without an employee.

## How it works

1. The web client opens the error dialog (`ErrorDialog` in
   `web/static/src/core/errors/error_dialogs.js`, or one of its subclasses).
2. A patch on `ErrorDialog` sends the error to the route `/oteny_oops/log`
   when the dialog is mounted. It sends the text that the dialog shows: the
   error name, the message, the model, the server traceback and the client
   traceback. It adds the URL of the page.
3. The route checks that the user is an internal user. It finds an Oops Log
   record of the same user with the same fingerprint (D6) from the last 10
   minutes, or it creates a new record, cut to 1 MB (D8). It returns the id
   of the record and its link.
4. The dialog shows "Oops Log <id>" and a copy button with the link.

The browser sends the error in a new request, because the request that
failed has rolled back its transaction: a write there is lost. The browser
also holds both halves of the error (the server traceback in `data.debug`
and the client stack), and the page URL names the record that the user had
open.

## Decisions

- **D1 — What is written: the faults, not the business messages.** A record
  is made for every `ErrorDialog` and its subclasses: `RPCErrorDialog` (a
  server error that has no own dialog), `ClientErrorDialog`,
  `NetworkErrorDialog` and `RequestEntityTooLargeErrorDialog`. A
  `UserError`, `ValidationError`, `AccessError`, `AccessDenied`,
  `MissingError` or `RedirectWarning` opens `WarningDialog` or
  `RedirectWarningDialog`, and a session expiry or a 504 opens its own dialog
  (`error_dialogs.js`, the `error_dialogs` registry). Those are messages the
  code means to show, not faults, so no record is made for them.
- **D2 — The record is made when the dialog opens, without a click** (Ries,
  25-Oct-2026). Enterprise saves only on a click on Share, so the
  administrator sees only the faults that a user chose to share. On Radar's
  production, 12 users made 74 files in a year; nobody knows how many faults
  nobody shared. Cost: more records; D6 keeps a repeat to one record.
- **D3 — Only internal users send errors.** The route refuses a portal or
  public user with `Forbidden`, as the Enterprise route does
  (`request.env.user._is_internal()`). The patch sends nothing when
  `user.isInternalUser` is false. A visitor sees no error dialog anyway
  (`swallowAllVisitorErrors` in `error_handlers.js`).
- **D4 — Plan 1 covers the faults that a user sees** (Ries, 25-Oct-2026).
  An error without a dialog (a scheduled action, a JSON-2 or XML-RPC call of
  a bot, a portal page) stays in the server log only. After Radar's move off
  odoo.sh (radar community plan, W8) that log lives on the bot host. A
  server-side capture needs its own database cursor, because the failed
  request rolls back its own, and a guard against two records of one fault
  (one from the server, one from the dialog). It can come as plan 2 when the
  server log is not enough.
- **D5 — The model `oteny.oops.log`, "Oops Log"** (Ries, 25-Oct-2026). Odoo
  shows "Oops Log" for one record too, as it shows "Logging" for a record of
  `ir.logging`. This plan calls one record an Oops Log record. Fields:
  - `name` (Char, required): the title, "<exception name>: <first line of
    the message>";
  - `error_type` (Selection: `server`, `client`, `network`): from the
    dialog's `type` prop, else from the dialog class;
  - `exception_name` (Char): for example `builtins.AttributeError` or
    `OwlError`;
  - `message` (Text);
  - `traceback` (Text): the text that the dialog shows, server part first;
  - `model` (Char): the model of the failed call, when the dialog has one;
  - `url` (Char): the URL of the page;
  - `user_id` (Many2one `res.users`) and `company_id` (Many2one
    `res.company`): the user and the current company of the request;
  - `user_agent` (Char): from the request header, because a client fault
    can depend on the browser;
  - `fingerprint` (Char, index), `occurrence_count` (Integer, default 1),
    `last_date` (Datetime, index, default now): see D6 and D12;
  - `active` (Boolean): the administrator archives a record when it is
    handled.
- **D6 — One fingerprint for one fault.** The fingerprint is the SHA-1 of
  the exception name and the frames of the traceback without values: for
  Python the file and the function of each `File "…", line …, in …` line,
  for JavaScript the function name of each `at …` line. Line numbers and
  asset URLs are left out, because a deploy changes them and the same fault
  must keep its fingerprint. When the traceback has Python frames, only those
  count: the client part of a server error is the same call machinery for
  every server fault, and it differs per screen (found in the build). Two
  faults in the same function share a
  fingerprint; the traceback still shows the line. The list groups the
  records by fingerprint, so the administrator sees how many users met one
  fault.
  **Repeat guard:** when the same user sends the same fingerprint again
  within 10 minutes, the route adds 1 to `occurrence_count` and sets
  `last_date` on that record instead of a new one. Why: an error in a render
  loop or a retry can open the dialog many times. The 10 minutes are a
  constant in the code, not a setting.
- **D7 — Only Settings administrators read the Oops Log.**
  `base.group_system` can read, write, archive and delete; no other group has
  access. Nobody creates a record by hand: the route creates it with
  `sudo()` after the D3 check. Why: a traceback can hold personal data (a
  name in a message, a value in the arguments). The link in the dialog
  therefore opens only for an administrator; the user pastes it, the
  administrator opens it. The Enterprise link opened for anybody who had it.
- **D8 — One Oops Log record holds at most 1 MB of text** (Ries,
  25-Oct-2026). 1 MB is 1,048,576 bytes (1 MiB), the limit of the Enterprise
  route, counted in UTF-8 over all text fields of the record. The route cuts
  before it saves:
  - `name` is the first 200 characters of the first line of the message;
  - `url` and `user_agent` are cut at 2,000 characters;
  - `message` and `traceback` share the rest. When they do not fit, the
    route cuts the traceback first and then the message. It keeps the start
    of each, because the server traceback comes first and the dialog's text
    starts with it, and it ends a cut text with the line
    `[cut: an Oops Log record holds at most 1 MB]`.

  A real traceback is 5 to 20 KB (the file of to-do 10436098078 has 4,435
  bytes), so a cut is rare. The repeat guard of D6 only counts, so a record
  never grows after it is saved.
  **The request limit:** the route sets `max_content_length` to 2 MiB
  (`@http.route(..., max_content_length=2 << 20)`, read by `odoo/http.py`).
  JSON escapes every line break and quote, so a request for a full 1 MiB
  record is a little larger than 1 MiB. The server does not read a larger
  request: it answers with the JSON-RPC error
  `werkzeug.exceptions.RequestEntityTooLarge`, with HTTP status 200 (the
  build showed this; the draft said HTTP 413). Cost: a fault whose text is
  above about 2 MiB gets no record; the dialog then shows no link (D9).
- **D9 — A failed call never opens a second dialog.** The patch catches
  every error of its call to `/oteny_oops/log` and shows nothing. Why: a
  second error dialog would send the error again, and a fault in the route
  would loop. The dialog then shows no link; the clipboard button still
  works.
- **D10 — The menu and the link** (Ries, 25-Oct-2026). The menu "Oops Log"
  (`oteny_oops.menu_oteny_oops_log`, groups `base.group_system`) sits in the
  section "Oteny" under Settings › Technical (`oteny_base.menu_oteny`, D13).
  The section also holds "Cloud Filestore Cache"
  (`oteny_cloud_filestore.menu_cloud_filestore_cache`), which today sits
  straight under Technical with sequence 100. In the section, "Cloud
  Filestore Cache" has sequence 10 and "Oops Log" sequence 20, so they show
  in the order of their names. The Technical menu needs debug mode
  (`base.group_no_one`), so the section and its menus do too. The link is
  `/odoo/action-oteny_oops.action_oteny_oops_log/<id>`, the form of the
  record.
- **D11 — License and manifest of `oteny_oops`.** `"license": "LGPL-3"`
  (radar community plan, D12: otenydoo stays open source),
  `"version": "19.0.1.0"`, `"depends": ["base", "web", "oteny_base"]`,
  `"category": "Hidden"`, `"author": "Oteny"`. The module is made for
  Community. With Enterprise Documents installed too, the dialog shows both
  the Share button and the Oops Log link; both work.
- **D12 — A daily clean-up deletes old records** (Ries, 25-Oct-2026). The
  system parameter `oteny_oops.max_age_days` holds the maximum age in days,
  default `30`. A `noupdate` data record creates it, so an administrator
  finds it under Settings › Technical › System Parameters and an upgrade
  keeps a changed value. The code uses 30 when the parameter is missing.
  - The method `_gc_old_records`, marked `@api.autovacuum`, deletes every
    Oops Log record whose `last_date` is older than the maximum age,
    archived records too (`active_test=False`).
  - It runs once a day in Odoo's own scheduled action "Base: Auto-vacuum
    internal data" (`base.autovacuum_job`), which runs every
    `@api.autovacuum` method. Odoo uses that scheduled action for its own
    clean-ups, so the module needs no scheduled action of its own. The
    scheduled action catches an error per method and goes on with the next
    (`ir.autovacuum._run_vacuum_cleaner`).
  - The age counts from `last_date`, not from `create_date`. Why: a fault
    that users still meet keeps its record and its count; a record goes when
    nobody met its fault for the maximum age.
  - Why a clean-up: a record can hold personal data (D7), and 1 MB per
    record (D8) times a fault that many users meet can fill the table.
- **D13 — The new module `oteny_base` owns the section "Oteny"** (Ries,
  25-Oct-2026). A menu record belongs to one module, and `oteny_oops` and
  `oteny_cloud_filestore` do not depend on each other; no otenydoo module
  sits under both. So a small module holds the section, and both depend on
  it. A later Oteny module can put its menu in the section too. Today
  `oteny_base` holds only the section: `menu_oteny`, name "Oteny", parent
  `base.menu_custom`, sequence 100 (the place of "Cloud Filestore Cache"
  today, after the sections of Odoo). Manifest: `"license": "LGPL-3"`,
  `"version": "19.0.1.0"`, `"depends": ["base"]`, `"category": "Hidden"`,
  `"author": "Oteny"`. A database that has `oteny_cloud_filestore` gets
  `oteny_base` with the next upgrade of `oteny_cloud_filestore`, because
  `ir.module.module.button_upgrade` installs a dependency in the state
  `uninstalled`.

## Alternatives that do not meet the need

- **`--log-db` with `--log-db-level=error`** (`odoo/netsvc.py`,
  `PostgreSQLHandler`): writes every log record of that level into
  `ir.logging`. It has no user, no page, no client error and no grouping. It
  is a server option, so the host sets it, not the module (odoo.sh sets the
  command line).
- **Sentry (OCA `sentry`)**: sends server errors to an external Sentry
  service. It needs that service and a key, and the errors live outside
  Odoo.

## Global constraints

- Generic modules: no business name or business model in code, data, help
  text or error text. Add `oteny_base` and `oteny_oops` to `SCOPES` in
  `.git_hooks/customer_template_gate.py` and to the `files:` pattern in
  `.pre-commit-config.yaml`.
- Both new modules are Community-only, so the GitHub workflow `tests.yml`
  installs and tests them on the official Odoo 19 image: add them to its
  `-i` and `--test-tags` lists.
- Test tags: `@tagged("oteny_base", "post_install", "-at_install")` and
  `@tagged("oteny_oops", "post_install", "-at_install")`.
- Craft rules: `odoo-development` (`SKILL.md`, `coding-patterns.md`,
  `xml-guidelines.md`, `testing-guidelines.md`).
- Test command for the new modules (a dedicated database; `-i` on the first
  run, `-u oteny_base,oteny_oops` after a change):

```zsh
~/odoo/venv/bin/python3 ~/odoo/odoo19/odoo-bin --stop-after-init -p 8071 \
  --addons-path=~/odoo/odoo19/addons,~/oteny/otenydoo \
  -d oops-test -i oteny_base,oteny_oops --without-demo=True \
  --http-interface 127.0.0.1 --max-cron-threads 0 --test-enable \
  --test-tags=oteny_base,oteny_oops --log-level=test 2>&1 \
  | tee /tmp/oops-test.log
```

  Pass: the log ends with `0 failed, 0 error(s)`. The addons path has no
  Enterprise folder, so the run also proves that the modules need no
  Enterprise module. The hoot tests run in the same run through the
  `web.assets_unit_tests` bundle. `oteny_cloud_filestore` keeps its own test
  command (its plan 1, "Global Constraints").

## Review focus

1. A failed call to `/oteny_oops/log` never opens a second dialog (D9; JS
   test `failed call opens no second dialog`).
2. A portal or public user never creates an Oops Log record, and a user
   without `base.group_system` never reads one (D3, D7; Python tests
   `test_portal_user_is_refused`, `test_internal_user_cannot_read`).
3. A `UserError` or `ValidationError` makes no record (D1; JS test
   `warning dialog sends nothing`).
4. No record holds more than 1 MiB of text, counted in bytes, not in
   characters (D8; Python test `test_record_is_cut_to_one_megabyte`).
5. The clean-up deletes by `last_date` and reads the parameter (D12; Python
   tests `test_old_records_are_deleted`, `test_max_age_parameter_is_read`).
6. Both menus sit in the section "Oteny" after an upgrade of a database that
   has `oteny_cloud_filestore` and no `oteny_base` (D10, D13; Task 6 check).

## Tasks

### Task 1 — The module `oteny_base`

- [x] `oteny_base/__init__.py`, `__manifest__.py` (D13), `README.md` (what
  the module holds and why), `views/oteny_base_menus.xml` with
  `menu_oteny`, `tests/__init__.py`.
- [x] Gate scope, pre-commit pattern and `tests.yml` (Global constraints).
- [x] Test (`tests/test_oteny_base_menus.py`):
  `test_oteny_section_is_under_technical` (the parent of `menu_oteny` is
  `base.menu_custom`).

### Task 2 — The skeleton of `oteny_oops`

- [x] `oteny_oops/__init__.py`, `__manifest__.py` (D11),
  `models/__init__.py`, `controllers/__init__.py`, `tests/__init__.py`.
- [x] Gate scope, pre-commit pattern and `tests.yml` (Global constraints).
- [x] Check: `-i oteny_oops` installs `oteny_base` with it on a
  Community-only addons path, without a warning in the log.

### Task 3 — Model, access, views and menu

- [x] `models/oteny_oops_log.py`: the fields of D5, `_order = "id desc"`,
  and `_log_error(values)`, which applies D6 and D8 and returns the record.
  The fingerprint is a separate function, so a test can call it alone.
- [x] The same file: `_gc_old_records`, marked `@api.autovacuum` (D12).
- [x] `data/ir_config_parameter.xml` (`noupdate="1"`):
  `oteny_oops.max_age_days` = `30` (D12).
- [x] `security/ir.model.access.csv`: `base.group_system` only (D7).
- [x] `views/oteny_oops_log_views.xml`: a list (date, user, title, model,
  count, last date), a search view (group by fingerprint, user, error type;
  a filter for the last 7 days; archived records), a form with the
  traceback in a monospace text field, the action `action_oteny_oops_log`
  and the menu of D10.
- [x] Tests (`tests/test_oteny_oops_log.py`, `TransactionCase`):
  `test_fingerprint_ignores_line_numbers` (two tracebacks that differ only in
  line numbers and asset hashes get one fingerprint; another function gets
  another), `test_same_fault_within_ten_minutes_counts`,
  `test_same_fault_after_ten_minutes_is_new_record`,
  `test_same_fault_other_user_is_new_record`,
  `test_record_is_cut_to_one_megabyte` (a 3 MiB traceback with multi-byte
  characters: the text fields of the record sum to at most 1,048,576 UTF-8
  bytes, the traceback keeps its start and ends with the cut line),
  `test_internal_user_cannot_read`, `test_old_records_are_deleted` (with the
  default of 30 days: a record with `last_date` 31 days ago goes, also when
  archived; one of 29 days stays; one created 40 days ago with `last_date`
  yesterday stays), `test_max_age_parameter_is_read` (with the parameter at
  `7`, a record of 8 days goes), `test_menu_is_in_oteny_section` (the
  parent of `menu_oteny_oops_log` is `oteny_base.menu_oteny`). The clean-up
  tests call `_gc_old_records` directly.

### Task 4 — The route

- [x] `controllers/main.py`: `@http.route("/oteny_oops/log",
  type="jsonrpc", auth="user", methods=["POST"],
  max_content_length=2 << 20)`. It raises `Forbidden` for a user that is
  not internal (D3), adds the user, the company and the user agent from the
  request, calls `_log_error` with `sudo()` and returns
  `{"id": …, "url": …}`.
- [x] Tests (`tests/test_oteny_oops_route.py`, `HttpCase`,
  `make_jsonrpc_request`): `test_internal_user_error_is_logged` (user,
  company, URL, model, traceback, fingerprint; the answer has the id and the
  link of D10), `test_portal_user_is_refused` (no record),
  `test_request_above_two_megabytes_is_refused` (the error
  `RequestEntityTooLarge`, no record).

### Task 5 — The dialog

- [x] `static/src/error_dialog_patch.js`: patch `ErrorDialog.prototype.setup`
  to register an `onMounted` callback that sends the error. The callback
  runs after the whole `setup` chain, so `RPCErrorDialog` has built
  `this.traceback` (server part, then the client part) by then. Send
  `this.traceback || this.props.traceback`, `props.name`, `props.message`,
  `props.exceptionName || props.data?.name`, `props.type`, `props.model` and
  `browser.location.href`. Catch every error of the call (D9). Add
  `CopyButton` (`@web/core/copy_button/copy_button`) to the components.
- [x] `static/src/error_dialog_patch.xml`: in the footer of `web.ErrorDialog`,
  after the Close button, "Oops Log <id>" and the copy button with the link,
  when the record is saved.
- [x] Tests (`static/tests/error_dialog_patch.test.js`, hoot; the Enterprise
  test `documents/static/tests/error_dialog_patch.test.js` shows the
  pattern): `server error writes one record` (the payload holds the server
  traceback and the page URL; the dialog shows the id and the copy button),
  `failed call opens no second dialog`, `warning dialog sends nothing`,
  `portal user sends nothing`.

### Task 6 — "Cloud Filestore Cache" moves into the section

- [x] `oteny_cloud_filestore/__manifest__.py`: add `oteny_base` to
  `depends`; `version` 19.0.1.26 → 19.0.1.27 (a deploy had moved it from
  19.0.1.25 meanwhile), because its data changes and the new version makes
  odoo.sh upgrade the module.
- [x] `oteny_cloud_filestore/views/cloud_filestore_cache_views.xml`: the
  parent of `menu_cloud_filestore_cache` becomes `oteny_base.menu_oteny`,
  the sequence 10. The file has no `noupdate`, so the upgrade moves the
  menu.
- [x] Test (in `oteny_cloud_filestore/tests`):
  `test_menu_is_in_oteny_section`.
- [x] Check: on a copy of a database that has `oteny_cloud_filestore` and no
  `oteny_base`, `-u oteny_cloud_filestore` installs `oteny_base` and the
  menu shows under Settings › Technical › Oteny.

### Task 7 — Check in the browser

- [x] On a local database with the modules, in debug mode: Settings ›
  Technical shows the section "Oteny" with "Cloud Filestore Cache" and "Oops
  Log", and no "Cloud Filestore Cache" straight under Technical. Without
  debug mode, the Technical menu is hidden.
- [x] Open any form, then call a missing method from the browser console
  without `await` (`odoo.__WOWL_DEBUG__.root.env.services.orm.call(
  "res.partner", "oteny_oops_no_such_method")`). The dialog shows "Oops Log
  <id>" and the link. Open the link as an administrator: the form shows the
  traceback and the page URL. Do it again: the same record shows count 2.
  Log in as an internal user without administrator rights: the dialog shows
  the link, the link answers with an access error.

### Task 8 — Radar installs it

- [x] `crewradar/__manifest__.py`: add `oteny_oops` to `depends`, beside
  `oteny_home` and `oteny_color_scheme`. An upgrade of `crewradar` installs
  a new dependency, so the odoo.sh deploy installs `oteny_oops` and
  `oteny_base`.
- [x] Add `@oteny_oops` to `TestHootSuites.SUITES` in
  `crewradar/tests/test_hoot.py`, or the command-line runner of the hoot
  suites never loads it (odoo-development testing-guidelines, found in the
  build).
- [x] Add `oteny_base` and `oteny_oops` to `odoo.installModules` in
  `.vscode/settings.json` and `.vscode/settings.example.json`, and to the
  module list in radar `crewradar-development/references/running-tests.md`
  (18 modules → 20).
- [x] Check: the full radar suite on a fresh database, 0 failed (run on
  `cr-oops`, so `cr-test` of other sessions stayed untouched).
- [ ] Commit with `python -m riverdeploy commit-with-otenydoo` from
  `~/oteny/radar` (otenydoo first).

### Task 9 — Skills

- [x] New skill `oteny-oops/SKILL.md`: what an Oops Log record holds, when
  it is made, who reads it, the menu, the 1 MB limit, the clean-up and its
  parameter `oteny_oops.max_age_days`, the files, the tests.
- [x] otenydoo index `.claude/skills/SKILL.MD`: a line for `oteny-oops`, and
  `oteny_base` in the sentence on the modules that a `README.md` covers;
  `otenydoo/SKILL.md` ("The layering"): `oteny_base` sits under
  `oteny_oops` and `oteny_cloud_filestore`.
- [x] `oteny-cloud-filestore/SKILL.md` ("Inspection in debug mode"): the
  path becomes Settings › Technical › Oteny › Cloud Filestore Cache.
- [x] Radar: a line in the community plan, D11, that `oteny_oops` replaces
  the Share button; a release note (crewradar-release-notes) that the error
  dialog shows a link to the Oops Log.

## Outcome (25-Oct-2026)

- otenydoo (side clone, `main`, not committed): `oteny_base`, `oteny_oops`,
  the move of the Cloud Filestore Cache menu, the gate scope, the
  pre-commit pattern and `tests.yml`. radar: worktree branch
  `claude/oteny-oops` (from `origin/main`, not committed); the laptop's
  `.vscode/settings.json` has both modules in `installModules` and
  `installModulesCommunity`.
- Tests on `oops-test` (Community addons only): `oteny_base` and
  `oteny_oops`, 0 failed of 14; hoot 4 passed, and 2 fail without the patch
  file. On `oops-cf`, the committed `oteny_cloud_filestore` 19.0.1.26, then
  `-u` with this change: `oteny_base` installed, the menu under Technical ›
  Oteny, 0 failed of 178.
- Full radar suite on a fresh `cr-oops` with the 20 modules: 0 failed, 0
  error(s) of 5,644 tests; the hoot suites passed, `@oteny_oops` 4 of 4.
- Browser check (headless Chrome over the DevTools protocol, because the
  Browser pane of the session was in no window): the section "Oteny" is
  the last of Technical; the dialog showed "Oops Log 17"; the same fault
  again gave count 2 on record 17; the link opened the form; an internal
  user without administrator rights got "Oops Log 18" and an access error
  on the link.

## Not in this plan

- Errors without a dialog: scheduled actions, JSON-2 and XML-RPC calls, portal
  pages (D4).
- A message to the administrator for a new record, and a state for triage.
  The administrator archives or deletes a record.
- A field for the maximum age on the Settings screen. The administrator
  changes the system parameter (D12).
- Errors from the portal or the website.
