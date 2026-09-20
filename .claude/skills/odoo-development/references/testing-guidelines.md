# Testing Guidelines

Unit testing and browser testing procedures for Odoo 19 addons in this workspace.

## Unit Testing

### Test Command Structure

For crewradar, riverflow, and rivercreds tests:

```zsh
cd /Users/ries/oteny/radar && ../../odoo/venv/bin/python3 ../../odoo/odoo19/odoo-bin \
  --stop-after-init \
  -p 8068 \
  --addons-path=../../odoo/odoo19/addons,/Users/ries/odoo/enterprise19,/Users/ries/oteny/otenydoo,/Users/ries/oteny/radar \
  --without-demo=True \
  --http-interface 127.0.0.1 \
  --test-enable \
  --test-tags=.test_method_name \
  -d cr-test \
  -r ries -w ries \
  --max-cron-threads 0 \
  --limit-time-real 0 \
  --log-level=test
```

### Full radar workspace suite (Cursor `/radar/test`)

The radar workspace defines a command in `.claude/commands/test.md` that instructs running the default **combined** tag set from `.vscode/settings.json` → `odoo.testTags`. That list must equal `odoo.installModules`: every first-party module we built and install for CrewRadar. In the command palette, the slash command may appear with the workspace or folder prefix (e.g. `/radar/test`).

**Tag semantics**: Comma-separated values in `--test-tags` are **OR** in Odoo’s TagsSelector: a test runs if its class matches **any** of the listed tags.

Manual equivalent on database `cr-test` (same addons path and credentials as above; assumes that DB is installed with the full `odoo.installModules` list, not a single-module `-i`):

```zsh
cd /Users/ries/oteny/radar && ../../odoo/venv/bin/python3 ../../odoo/odoo19/odoo-bin \
  --stop-after-init \
  -p 8068 \
  --addons-path=../../odoo/odoo19/addons,/Users/ries/odoo/enterprise19,/Users/ries/oteny/otenydoo,/Users/ries/oteny/radar \
  --without-demo=True \
  --http-interface 127.0.0.1 \
  --test-enable \
  --test-tags=oteny_audit,oteny_shortcut,oteny_bot,oteny_backup_trigger,odoo_parallel_tests,oteny_knowledge_sync,riverflow,rivercreds,crewradar,crewradar_wilma,crewradar_cuneus_sign,crewradar_creds,crewradar_sign,crewradar_marinetraffic \
  -d cr-test \
  -r ries -w ries \
  --max-cron-threads 0 \
  --limit-time-real 0 \
  --log-level=test \
  | tee /tmp/radar_test_full_suite.txt
```

**Suite size**: The `crewradar` tag selects every class that includes it, including derived addons (e.g. `crewradar_wilma`, `crewradar_cuneus_sign`) that tag tests with both their module tag and `crewradar`. Together with the other module tags the run covers 470 classes and 4263 tests, and it finishes in about 85 seconds on 21 parallel workers (2026-08-21). A module that does not carry the `crewradar` tag (`oteny_bot`, `odoo_parallel_tests`, `oteny_knowledge_sync`, `oteny_audit`, `riverflow`) still gates a deploy because `odoo.testTags` lists every installed first-party module.

**`-u` also gates test discovery.** A combined `-u <modules> --test-enable` run loads tests only from the modules named in the `-u` list. So a tag in `odoo.testTags` selects nothing when its module is missing from `odoo.installModules`. `odoo_parallel_tests` hit exactly that on 2026-08-21: the tag was set, the module was not in the `-u` list, and `TestCloneReuseKey` dropped 6 tests from the run with no warning. Keep the two settings in step — every module whose tag you list must also sit in the install list.

**Example red test from a full combined-tag run**: `crewradar_cuneus_sign.tests.test_a1_workflow.TestA1SendIssuedA1AutoProgress.test_full_workflow_credentials_linked_to_parent` can fail with an `AssertionError` comparing two `riverflow.state` records (expected vs actual state after Send Issued A1 / parent credential linking). Numeric ids in the message are database-specific. Reproduce in isolation with `--test-tags=.test_full_workflow_credentials_linked_to_parent` on a database installed with the full `odoo.installModules` list.

After changes to Arrange Work Permit or Cuneus DE employee lifecycle code in `crewradar_cuneus_sign`, a focused run with `--test-tags=crewradar_cuneus_sign` (or `.test_auto_complete_state` for a single method) validates faster than only relying on the full combined tag set; see [crewradar-cuneus — Testing](../../crewradar-cuneus/SKILL.md#testing) for the command line and a common failure interpretation.

For oteny_audit tests (not in use / disregard):

```zsh
cd /Users/ries/oteny/radar && ../../odoo/venv/bin/python3 ../../odoo/odoo19/odoo-bin \
  --stop-after-init \
  -p 8068 \
  --addons-path=~/odoo/odoo19/addons,~/odoo/enterprise19,~/oteny/otenydoo,~/oteny/radar \
  --without-demo=True \
  --http-interface 127.0.0.1 \
  -i oteny_audit \
  --test-enable \
  --test-tags=oteny_audit \
  -d oteny-audit-test \
  -r ries -w ries \
  --max-cron-threads 0 \
  --limit-time-real 0 \
  --log-level test
```

### Test Tag Formats

| Scope | Format | Example |
|-------|--------|---------|
| Single method | `.method_name` | `--test-tags=.test_site_id_required_for_actual_work` |
| Functional area | `tag_name` | `--test-tags=test_billing` |
| All module tests | `module_name` | `--test-tags=crewradar` |

**Important**: Use `.method_name` format (with dot prefix), NOT `Class.method_name`.

### Test Class Tags

Add tags to new test classes:

- For base `crewradar` module tests, use `crewradar` as the module tag.
- CrewRadar-derived addons (`crewradar_wilma`, `crewradar_cuneus_sign`, `crewradar_creds`, `crewradar_sign`, `crewradar_marinetraffic`) and `rivercreds`: include `crewradar` in `@tagged(...)` alongside the module-specific tag so `--test-tags=crewradar` still runs that stack as a convenience.
- Generic modules in `otenydoo` (`oteny_shortcut`, `oteny_knowledge_sync`, `riverflow`, `oteny_audit`, …) gate a deploy through their **own** addon tag. `odoo.testTags` equals `odoo.installModules`. Do not add a leftover `crewradar` tag on a generic module — that is how `oteny_shortcut` used to hide from the gate, and how a class tagged only `oteny_knowledge_sync` never ran.

```python
from odoo.tests import tagged

@tagged("crewradar", "post_install", "-at_install", "test_billing")
class TestBilling(TransactionCase):
    """Tests for billing functionality."""
    
    def test_invoice_creation(self):
        # Test implementation
        pass
```

Tags explained:

- `crewradar` - Module identifier
- `post_install` - Run after module installation
- `-at_install` - Don't run during installation
- `test_billing` - Functional area tag for selective running

Derived addon example:

```python
@tagged("crewradar_creds", "crewradar", "post_install", "-at_install", "test_credential_plan")
class TestCrewradarCredentialPlan(TransactionCase):
    pass
```

### Database Management

| Scenario | Action |
|----------|--------|
| Fresh start | `dropdb -f cr-test` then run with `-i` and the full module list from `odoo.installModules` in `.vscode/settings.json`. See [Fresh DB Install](../../../../../radar/.claude/skills/crewradar-development/references/fresh-db-install.md) — **do not** use `-i crewradar` alone; `crewradar_creds`, `crewradar_sign`, and others must be installed explicitly. |
| Schema changes | Add `-u riverflow,crewradar,crewradar_creds` etc. to update modules |
| Subsequent runs | No `-i` or `-u` needed if schema unchanged |

### Output Handling

Always redirect test output for analysis:

```zsh
cd /Users/ries/oteny/radar && ../../odoo/venv/bin/python3 ../../odoo/odoo19/odoo-bin \
  --stop-after-init \
  --test-enable \
  --test-tags=.test_method_name \
  ... other flags ... \
  | tee /tmp/test_output.txt
```

Then analyze:

```zsh
# Check for failures
grep -E "(failed|error\(s\))" /tmp/test_output.txt

# View full output
tail -100 /tmp/test_output.txt
```

### Test Result Keywords

The Odoo test runner reports results as:

```
X failed, Y error(s) of Z tests
```

Check for **both** `failed` and `error(s)` when verifying test results.

## Odoo test run issues

### Culprit: not all intended modules installed on the test database

Post-install tests in this workspace are written for a database that matches **local/CI practice**: install the full comma-separated module list from `.vscode/settings.json` → `odoo.installModules` (see [Fresh DB Install](../../../../../radar/.claude/skills/crewradar-development/references/fresh-db-install.md)). That list includes `crewradar`, `crewradar_creds`, `crewradar_sign`, `crewradar_wilma`, `crewradar_cuneus_sign`, and other addons the suite depends on—not a single module such as `-i rivercreds` or `-i crewradar` in isolation.

**Why failures happen**: Odoo only loads Python and XML for modules in `installed` / `to upgrade` state on that database. Skipping modules from the intended set drops their models, records, and workflow data. The workspace post_install tests assume the same installed module graph as a full `-i` using `odoo.installModules`, not a hand-picked subset.

**Symptoms**:

- `env.ref(...)` or searches for standard records return nothing.
- Computes or plan logic return empty recordsets where the full install has rows.

**What not to do**: Do not change tests to “support” partial module installs (e.g. branching setUp on whether another addon is installed). Fix the database: `dropdb`, then `-i` with the full `odoo.installModules` list.

**Quick check**: Compare `SELECT name, state FROM ir_module_module WHERE name LIKE 'crewradar%' ORDER BY name;` against the modules you expect from `odoo.installModules`.

### Culprit: Homebrew took the `vector` extension away from PostgreSQL 16

**Symptom**: the run ends before a single test executes, with
`0 failed, 0 error(s) of 0 tests`, and above it:

```
psycopg2.errors.UndefinedFile: could not access file "$libdir/vector": No such file or directory
CONTEXT:  SQL statement "DELETE FROM ONLY "public"."ai_embedding" WHERE $1 OPERATOR(pg_catalog.=) "attachment_id""
CRITICAL cr-test odoo.service.server: Failed to initialize database `cr-test`.
```

**Cause**: Odoo pregenerates asset bundles at start-up and deletes the
attachment each rebuilt bundle replaces. `ai_embedding.attachment_id` cascades
from `ir_attachment`, and Postgres must load the `vector` extension library to
process that cascade. The library goes missing whenever Homebrew's `pgvector`
formula is (re)installed, because its bottle is built against the *current*
PostgreSQL major versions only — `postgresql@17` / `postgresql@18` — while this
workspace runs `postgresql@16`. Nothing warns you: `pg_extension` still lists
`vector`, the database still opens, and the failure appears only when a
statement touches the type. Any `brew upgrade` can trigger it.

**Cure**: rebuild pgvector for PostgreSQL 16 with the repo's own script, then
stop Homebrew from replacing it again:

```zsh
cd ~/oteny/radar/crewradar && ./install_pgvector.sh
brew pin pgvector
```

The script clones pgvector v0.8.1, compiles it with `postgresql@16`'s
`pg_config` (overriding `PG_SYSROOT` for the installed macOS SDK), and copies
`vector.dylib` plus the SQL into the pg16 tree. Full notes in
[`crewradar/PGVECTOR_INSTALL.md`](../../../../crewradar/PGVECTOR_INSTALL.md).

**Diagnosis in one command** — the build targets are visible in the Cellar, so
this shows at a glance whether a pg16 library exists at all:

```zsh
ls /opt/homebrew/Cellar/pgvector/*/lib/
```

### Culprit: the test database runs older module code than the working tree

**Symptom**: a test that reads data Odoo loads from XML fails, while the file on
disk plainly contains what the test expects. The clearest case asserts on a
view: `TestOtenyBot.test_session_form_has_summary_and_technical_pages` reads
`ir.ui.view.arch_db` and looks for `name="action_open_origin"`. The button sits
in `oteny_bot/views/oteny_bot_views.xml`, but `cr-test` carried module version
`19.0.1.61` and the button shipped in `19.0.1.62`.

**Cause**: Odoo re-reads a module's XML only on `-i` or `-u`, or when the
manifest version rises during a load. A test database left alone drifts behind
the working tree, one merge at a time. The drift is invisible: nothing in the
log says a view is stale.

**Quick check** — compare disk against database for every installed module:

```zsh
for m in $(python3 -c "import json,pathlib;print(json.loads(pathlib.Path('.vscode/settings.json').read_text())['odoo']['installModules'].replace(',',' '))"); do
  disk=$(grep -m1 '"version"' $m/__manifest__.py | sed -E 's/.*"version": *"([^"]+)".*/\1/')
  db=$(psql -d cr-test -tAc "SELECT latest_version FROM ir_module_module WHERE name='$m';")
  [ "$disk" != "$db" ] && echo "STALE $m: disk=$disk db=$db"
done
```

**Cure**: re-run with `-u`. Pass the **full** install list, not only the stale
modules — `-u` also gates test discovery, so a short list silently shrinks the
suite (see the note under [Full radar workspace suite](#full-radar-workspace-suite-cursor-radartest)).

### Culprit: a missing binary or package makes the test a no-op

Odoo skips a test whose prerequisite is absent and still reports the run green, so a feature that never ran looks healthy. Two known cases in this workspace:

- **`websocket-client` / Chrome** — without either, Odoo skips every `start_tour` and `browser_js` test. `rivercreds/tests/test_browser_prerequisites.py` fails loudly instead, and names the remedy. `websocket-client` is declared in `requirements.txt`; when the guard fires, the venv has drifted from it. Fix the venv with `~/odoo/venv/bin/pip install -r requirements.txt`, never the test.
- **`wkhtmltopdf`** — without it, `crewradar_sign`'s QWeb overlay is dropped and the document still renders as a valid PDF. There is no Homebrew formula (upstream archived the project in 2023; the one macOS build is Intel-only, from 2020), so most laptops run without it. Odoo.sh ships it.

**Write the test so the gap is visible.** Ask for the prerequisite, then `skipTest` with a reason you can read in the log:

```python
state = self.env["ir.actions.report"].get_wkhtmltopdf_state()
if state in ("install", "broken"):
    self.skipTest("wkhtmltopdf is %s, so the overlay never reaches the page" % state)
```

The states `ok`, `upgrade` and `workers` all still render, because `sign_document._html_to_pdf()` calls `_run_wkhtmltopdf` directly and never consults the state. Only `install` (no binary) and `broken` (no version answer) mean no output.

**Assert the thing, not a proxy.** `TestQwebSignItem.test_render_document_with_qweb_items` used to assert that a document with a QWeb overlay was *larger in bytes* than a text-only one. With the binary absent neither document had an overlay, so the two sizes differed only by incidental PDF structure, and the assertion became a coin flip. It failed on 2026-09-06 at `1084 not greater than 1085`. It now extracts the page text with `pypdf` and asserts the QWeb cells are on the page.

**Choose assertion values that only the feature can produce.** The first rewrite asserted on the cells `Test` and `42`, but the sign template's text item renders `Test Value` onto the same page through Odoo's base renderer, with no overlay involved — so two of the assertions could pass while the overlay was missing. The row data is now `Rivermate` / `4032`, which nothing else on the page produces.

## Writing Tests

### Test Setup: Prefer setUpClass Over setUp

Use `setUpClass` (runs once per class) instead of `setUp` (runs before every test method) for shared test data. Odoo's `TransactionCase` creates savepoints per test method, so data created in `setUpClass` persists while per-test changes are rolled back automatically. This is safe and significantly faster for classes with many tests.

**Pattern**: Create shared base records (employees, ships, contracts, rates, state refs) in `setUpClass`. Keep per-test data creation (log entries, invoices, services that tests modify) in individual test methods or helper methods.

**Conversion rules**:

- `def setUp(self)` becomes `@classmethod def setUpClass(cls)`
- `super().setUp()` becomes `super().setUpClass()`
- All `self.` references become `cls.` within the setUpClass body only
- Test methods continue using `self.xxx` (Python class attribute lookup)
- Helper methods called from test methods stay as instance methods
- Assertions (`self.assertTrue`) cannot be used in `setUpClass` -- use `if not ...: raise ValueError(...)` instead
- If `setUpClass` calls a helper that is an instance method, either inline the call or make the helper a classmethod

**When to keep setUp**: Only if a test class genuinely needs fresh records before every single test method (rare -- savepoint rollback handles most cases).

### General Best Practices

- Don't rely on global demo data
- Set up test records in `setUpClass` (preferred) or `setUp`
- A `riverflow.service` on `crewradar.log.entry` should use a **real** `res_id`. A fake `1` or `999999` no longer raises — `_compute_log_entry_id` resolves `res_id` through a batched `exists()`, so the subject degrades to "no log entry" and `employee_id` / `site_id` come back empty. That is a silent pass, not a correct test: an invented id gives the clone hooks nothing to bind to and quietly hollows out whatever the test meant to assert. Reuse the fixture entry when the test only needs a second **service**. Pin: `test_dangling_subject_reference_resolves_to_no_subject` in `crewradar/tests/test_log_entry_service.py`.
- Combine related edge cases in single test for performance
- Test both positive and negative scenarios

```python
@tagged("crewradar", "post_install", "-at_install", "test_logbook")
class TestLogEntry(TransactionCase):
    
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Create shared test data once for all tests in class
        cls.employee = cls.env['hr.employee'].create({
            'name': 'Test Employee',
            'department_id': cls.env.ref('crewradar.hr_department_crewradar').id,
        })
        cls.ship = cls.env['crewradar.site'].create({
            'name': 'Test Ship',
            'ignore_for_planning': True,
        })
        cls.state_work = cls.env.ref('crewradar.log_entry_crew_change_working')
    
    def test_work_entry_requires_ship(self):
        """Work entries must have a ship assigned."""
        with self.assertRaises(ValidationError):
            self.env['crewradar.log.entry'].create({
                'employee_id': self.employee.id,
                'work_status': 'work',
                'start_date': '2024-01-01',
                'end_date': '2024-01-15',
                # Missing site_id - should raise
            })
```

### ACLs Are Bypassed by Superuser — Add a Non-Superuser Access Test

`TransactionCase` tests run as **superuser (`SUPERUSER_ID`/admin)**, which **bypasses all `ir.model.access` and record rules**. So a wizard/model test can pass in CI while real users hit *"You are not allowed to access …"*. This blind spot shipped a missing ACL on `crewradar_cuneus_sign.wp.waive.wizard` (a new transient model needs its own access rule — see [coding-patterns.md](coding-patterns.md#inheriting-transient-wizards-need-their-own-irmodelaccess)).

When adding a new model — especially a wizard reachable by non-admin users — add a cheap regression test that checks a plain internal user has access, using `has_access(operation)` (Odoo 19; returns a bool — `check_access(op)` raises, `check_access_rights` is the legacy name):

```python
def test_wizard_accessible_to_internal_user(self):
    user = self.env["res.users"].create({
        "name": "Test HR User", "login": "test_hr_user",
        "group_ids": [Command.set([self.env.ref("base.group_user").id])],  # Odoo 19: group_ids, not groups_id
    })
    Wizard = self.env["x.new.wizard"].with_user(user)
    self.assertTrue(Wizard.has_access("create"))
    self.assertTrue(Wizard.has_access("write"))
```

### Asserting on Rendered HTML — Parse It, Never Just `assertIn`

`assertIn("role=\"combobox\"", body)` proves a **string** contains markup. It does not prove a browser will build the DOM you meant. The two differ whenever HTML's tag-omission rules apply, and then the test is green while the feature is dead.

The MFNL stub shipped exactly that. `_select()` rendered a dropdown as `<p data-mfnl-combo>…<ul role="listbox">…</ul></p>`. A `<p>` **cannot** contain a `<ul>`, so every parser closes the `<p>` first and the list becomes a **sibling** of the wrapper. The page's click handler looked the list up with `box.querySelector('[role="listbox"]')`, got `null`, and the widget could never open. Eleven `assertIn` assertions passed on that markup.

Assert on the parsed tree instead. `lxml` ships with Odoo and reproduces the same tag-omission behaviour as the browser, so no headless browser is needed for this class:

```python
from lxml import html as lxml_html

doc = lxml_html.fromstring(body)
trigger = doc.xpath('//*[@role="combobox"][@aria-controls="sector-list"]')
self.assertTrue(trigger)
wrapper = trigger[0].xpath('ancestor::*[@data-mfnl-combo][1]')
# the list must really be INSIDE the wrapper in the parsed tree
self.assertTrue(wrapper[0].xpath('.//*[@role="listbox"]'))
```

**The rule**: when a test's subject is *interactive behaviour* — a widget opening, a handler finding its target, a control being reachable — assert against a parsed DOM (or a real browser). Reserve `assertIn` for content and labels, where the string genuinely is the fact.

The block-level tags that close an open `<p>` include `ul`, `ol`, `div`, `table`, `form`, `h1`–`h6`, and another `p`. Wrapping any of them in a `<p>` is always a bug, even when the emitted string looks correct.

### Compute Method Testing

**Never call `_compute_*` methods directly**. Rely on `@api.depends` triggers:

```python
def test_total_computed_on_line_change(self):
    """Total should recompute when lines change."""
    order = self.env['sale.order'].create({...})
    
    # Don't do this:
    # order._compute_total()
    
    # Do this: modify dependency and check result
    order.line_ids = [Command.create({'amount': 100})]
    self.assertEqual(order.total, 100)
    
    order.line_ids = [Command.create({'amount': 50})]
    self.assertEqual(order.total, 150)
```

### Avoiding Flushes

Don't call `invalidate_recordset()` or rely on database flushes:

```python
def test_entry_count(self):
    """Employee entry count should update automatically."""
    employee = self.env['hr.employee'].create({'name': 'Test'})
    
    # Create entry
    entry = self.env['crewradar.log.entry'].create({
        'employee_id': employee.id,
        ...
    })
    
    # Don't do this:
    # employee.invalidate_recordset()
    # self.env.cr.commit()
    
    # Just access the field - ORM handles cache
    self.assertEqual(employee.log_entry_count, 1)
```

### Testing a Recompute Migration (the flush + SQL exception)

The two rules above (no direct `_compute_*` calls, no `invalidate_recordset` / flushes) are for tests of **normal ORM reactivity**. A test that verifies a **recompute migration** (see [migrations.md — Recomputing a Stored Field](migrations.md#recomputing-a-stored-field-after-its-formula-changes)) is a sanctioned exception, because it deliberately reproduces a **stale stored column** — a state normal ORM operations never produce.

**Load the migration** — its folder name has dots, so it is not importable; use `importlib` and call the extracted helper (same pattern as `test_auv_email_wizard.py`, `test_wp_credential_migration.py`):

```python
def _run_migration(self):
    migration_file = (
        Path(__file__).resolve().parent.parent / "migrations" / "19.0.2.151" / "post-migrate.py"
    )
    spec = importlib.util.spec_from_file_location("post_migrate_2_151", migration_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._recompute_credential_renewal_deadlines(self.env)
```

**Plant the stale value** — the order matters:

```python
self.env.flush_all()          # settle pending recomputes: DB now holds the CORRECT value, nothing is queued
self.env.cr.execute(          # raw SQL bypasses both the compute and the field inverse
    "UPDATE riverflow_service SET project_deadline = %s WHERE id = %s", (stale, service.id))
service.invalidate_recordset(["project_deadline"])   # drop cache so the next read hits the DB
self.assertEqual(service.project_deadline, stale)    # precondition: the stale value is really planted

self._run_migration()

service.invalidate_recordset(["project_deadline"])
self.assertEqual(service.project_deadline, expected) # migration healed it
```

**Why the `flush_all()` first**: without it, creating the record leaves `project_deadline` queued for recompute (or dirty-but-unflushed). The raw `UPDATE` writes the column, but the next read sees the field is still to-compute and **recomputes it, overwriting your planted value** — the precondition assert then fails reading the correct value instead of the stale one. Flushing first clears the queue so the column read is authoritative. Cover both branches of the compute (e.g. permit-backed → renewal point, and permit-less → contract-start fallback).

## External HTTP Requests in Tests

### Odoo's HTTP Request Blocker

Odoo's `BaseCase.setUpClass` monkey-patches `requests.Session.send` to block all outbound HTTP requests when the test class has the `standard` tag. Since `standard` is in the default tag set (`{'standard', 'at_install'}`), all regular tests get this blocker. Blocked requests raise `BlockedRequest` with the message "External requests verboten". Only requests to `localhost` and `file://` URLs are allowed through.

The relevant code is in `odoo/tests/common.py`:

```python
# BaseCase._request_handler — allows localhost, blocks everything else
if url.hostname in (HOST, 'localhost'):
    return _super_send(s, r, **kw)
raise BlockedRequest(f"External requests verboten (was {r.method} {r.url})")

# Installed only when 'standard' is in the class tags:
if 'standard' in cls.test_tags or 'click_all' in cls.test_tags:
    patcher = patch.object(requests.sessions.Session, 'send', ...)
```

### Writing Live Integration Tests

Tests that need real external API access (e.g., Google APIs, Gemini) must opt out of the HTTP blocker by removing the `standard` tag with `-standard`. This has two effects:

1. The HTTP request blocker is not installed for the class
2. The tests are excluded from regular suite runs (e.g., `--test-tags=crewradar` selects `standard` tests)

**Pattern**: Use a dedicated functional tag (e.g., `test_wilma_live`) so tests can be targeted explicitly:

```python
@tagged("crewradar_wilma", "crewradar", "post_install", "-at_install", "-standard", "test_wilma_live")
class TestWilmaLiveIntegration(TransactionCase):
    """Live tests hitting real APIs — run with --test-tags=test_wilma_live"""
```

Run explicitly:

```zsh
--test-tags=test_wilma_live
```

**Note**: `-standard` in `--test-tags` acts as a filter that *excludes* tests with `standard`. It does **not** remove the `standard` tag from a class at runtime. The `-standard` must be in the `@tagged()` decorator on the class itself to prevent the HTTP blocker from being installed.

### API Key Gating and Secret Hygiene

Live / e2e tests — and the CLIs that exercise the same code paths — **must not disclose API keys or client secrets** anywhere they could be observed, retained, or replayed. Treat any leak as a key-rotation event.

**Never:**

- Commit keys to the repo. They live only in `.vscode/secrets.json` (gitignored, and denied to AI sessions via `.claude/settings.json`; see `.vscode/secrets.example.json` for the expected shape) or a gitignored file under `$HOME/.config/`. No Python constants, no XML data, no test fixtures, no `secrets.example.json` with a real value.
- Pass keys on the CLI (`--api-key <KEY>`, `psql ... -w PASSWORD`, `curl -H "Authorization: Bearer $KEY"`). argv leaks to `ps aux`, shell history, tmux scrollback, and `$HISTFILE`. Use a `--api-key-file PATH` flag (the tool reads + strips in-process) or pipe the key into the child's stdin.
- Echo a key. No `print(key)`, no `_logger.info("Using key %s", key)`, not even `key[:8] + "…"`. Avoid `exc_info=True` on paths that hold the key in a local; pre-redact HTTP request reprs before any WARNING/ERROR log.
- Disclose a key to an AI-agent prompt or transcript. When debugging, paste the config key name (`ai.google_key`) — never the secret value.
- Persist a key in `ir_config_parameter` without cleanup. `TransactionCase` rolls back the main cursor, but a commit inside the code under test — or a second cursor — can leave the row behind. Always pair `set_param(...)` with an `addCleanup` that unlinks the row.

**Do:**

- Load from a gitignored file once at module import (see the `_load_vscode_settings()` pattern below).
- Call `self.skipTest(...)` — with a reason that names the missing config *key*, not the value — when the key is absent (CI, pristine dev box).
- Wrap the test-only ICP write with `addCleanup` so the row is unlinked at teardown under all exit paths:

```python
def _require_api_key(test):
    if not API_KEY:
        test.skipTest("API_KEY not set in .vscode/secrets.json — skipping live test")
    ICP = test.env["ir.config_parameter"].sudo()
    ICP.set_param("api.key_param", API_KEY)
    # Belt-and-suspenders: drop the row at teardown even if the code under
    # test commits or uses a nested cursor that escapes the savepoint.
    test.addCleanup(lambda: ICP.search([("key", "=", "api.key_param")]).unlink())
```

#### Canonical loader: merge `secrets.json` over `settings.json`

API keys live in `.vscode/secrets.json` under the `odoo.*` key — split out of `.vscode/settings.json` so AI coding sessions cannot read them. The canonical loader reads both files and overlays secrets on top of settings, so existing call sites like `settings.get("geminiApiKey")` keep working unchanged and older developer setups (keys still in `settings.json`) continue to work as a fallback:

```python
def _load_vscode_settings():
    """Load .vscode/settings.json + secrets.json under the "odoo" key."""
    vscode_dir = Path(__file__).resolve().parents[N] / ".vscode"
    merged = {}
    for name in ("settings.json", "secrets.json"):
        path = vscode_dir / name
        if path.exists():
            with open(path) as f:
                merged.update(json.load(f).get("odoo", {}))
    return merged
```

Reference implementations: [`crewradar_wilma/tests/test_wilma_live_integration.py`](../../../../crewradar_wilma/tests/test_wilma_live_integration.py) (Odoo test), [`riverdeploy/deploy_tools/utils.py`](../../../../riverdeploy/deploy_tools/utils.py) (`load_settings()` for deployment CLI), [`tools/nas_analysis/analyzer.py`](../../../../tools/nas_analysis/analyzer.py) (standalone tool). Non-secret settings (database names, addons paths, `installModules`) stay in `settings.json`.

#### Defense-in-depth: Claude Code permission rules

`.claude/settings.json` blocks two exfiltration paths:

```json
"deny": [
  "Read(/.vscode/secrets.json)",    // blocks the Read tool
  "Bash(*secrets.json*)"            // blocks cat/jq/grep/python -c/etc.
]
```

The `Bash(*secrets.json*)` glob is a substring match on the full command, so it also blocks legitimate searches like `grep -rn secrets.json .` — use Grep/Read tools in AI sessions instead. The Read deny blocks the file and (as observed) anything in a denied-directory pattern; together they make API keys unavailable to AI sessions even if the bot tries every known read path.

For CLI tools (e.g. the NAS importer `wp_analyzer.py`) the equivalent pattern is `--api-key-file PATH`: a mode-`600` file under `~/.config/<tool>/` that the tool reads, strips, and keeps in process memory only. See [rivercreds-docs — Generate API key](../../rivercreds-docs/references/import-pipeline.md#3-generate-api-key) for the end-to-end minting-to-file flow.

## CI-Safe Test Patterns (Odoo.sh)

Odoo.sh dev builds fail not only on test assertion errors but also on WARNING/ERROR entries in the `ir_logging` table (written via `--log-db`). The patterns below prevent test code from generating noise that turns builds red even when all tests pass. See [Odoo.sh CI Reference](../../../../../radar/.claude/skills/crewradar-development/references/odoosh-ci.md) for the full failure detection mechanism.

### Valid PDF Data in Tests

Odoo enterprise's `documents` module calls `_get_is_multipage` on every PDF attachment. Fake PDF bytes (e.g., `b"dGVzdA=="`, `b"%PDF-1.4 test content"`, or base64 of just `"%PDF-1.4\n"`) fail PyPDF2 parsing, producing WARNING entries in `ir_logging`.

**Always use valid PDF data**. Odoo provides `base/tests/minimal.pdf`:

```python
import base64
from odoo.tools.misc import file_open

# In setUpClass:
cls.valid_pdf_data = base64.b64encode(
    file_open("base/tests/minimal.pdf", "rb").read()
)

# Or as module-level constant:
_VALID_PDF_B64 = base64.b64encode(file_open("base/tests/minimal.pdf", "rb").read())
```

### Valid VAT Numbers in Tests

`base_vat` is a declared dependency of `crewradar`, so `res.partner.check_vat`
runs on every partner and company write. An invented number fails its country
checksum and raises `ValidationError` — often inside `setUpClass`, which reports
as an **error**, not a failure, and takes the whole class down.

Use a checksum-valid number per country. The map in
`crewradar_cuneus_sign/tests/test_mfnl_all_ships.py` is the reference:

```python
_VALID_VAT = {"NL": "NL864162236B01", "DE": "DE999999903", "LU": "LU99999984",
              "CH": "CHE-100.000.006 MWST"}
```

Verify a candidate before using it:

```bash
python3 -c "import stdnum.de.vat as v; print(v.validate('999999903'))"
```

### Stubbing the VIES Lookup

`vies_valid` is computed by calling a paid IAP service. Never let a test reach
it. Patch the method, following `addons/base_vat/tests/test_vat_numbers.py`:

```python
_VIES_IAP = "odoo.addons.base_vat.models.res_partner.ResPartner._check_vies_iap"

# Give an answer: a plain function binds as a method, so it receives the record.
with patch(_VIES_IAP, lambda record: "valid"):
    ...

# Or assert no lookup happens at all: a MagicMock does not bind, so it is
# called with no arguments and raises.
with patch(_VIES_IAP, side_effect=AssertionError("VIES/IAP must not be called")):
    ...
```

The second form is the stronger assertion. It turns "the value is correct" into
"the value is correct *and* was obtained without paying for it", which is the
whole point of inheriting a VIES status from the commercial entity.

`_compute_vies_valid` also short-circuits to `False` unless some company has
`vat_check_vies = True`, and `perform_vies_validation` requires the partner's VAT
prefix to differ from the company's `account_fiscal_country_id`. A test that
needs the flag to matter must set both.

### Muting Expected Log Noise

Tests that deliberately trigger SQL constraint violations (e.g., UNIQUE) cause `odoo.sql_db` to log an ERROR before the exception propagates to `assertRaises`. This ERROR ends up in `ir_logging` on Odoo.sh.

Decorate the test method with `@mute_logger`:

```python
from odoo.tests.common import mute_logger

@mute_logger("odoo.sql_db")
def test_unique_constraint(self):
    with self.assertRaises(IntegrityError):
        ...
```

This is standard Odoo practice — many tests in the Odoo source tree use the same pattern.

**The same rule covers every deliberate fault, not only SQL.** Any test that
proves a graceful path — a refused claim, an unresolvable BSN, a missing image,
a mocked LLM timeout, a search backend that is rate limited, a migration branch
that declines to delete — makes the code narrate it at WARNING, which is right
for a real operator and fatal on Odoo.sh. Mute the logger the *code* uses, not
the test's own. A test that loads a migration through
`importlib.util.spec_from_file_location("wp_post_migrate_5_1", path)` gives that
migration `wp_post_migrate_5_1` as its logger name, so that bare name is what
you mute.

**Finding them all.** A pasted build log is usually partial, so do not work from
it. Run the suite locally and attribute every WARNING to the test that caused it
by tracking each worker's most recent `Starting <test> ...` line — the parallel
runner interleaves output, so a plain `grep -B` reads the wrong test. Then judge
each one: deliberate fault, mute it; graceful fallback the run survives, lower it
to INFO. A sweep on 2026-09-09 cleared 11 reported rows and 24 more that the
paste never showed.

### Log Level Discipline for Graceful Fallbacks

Code that gracefully handles failures (external API timeouts, missing configurations) should use `_logger.info()` not `_logger.warning()` when:

- The operation continues successfully without the failed component
- The failure is expected in certain environments (e.g., no API keys in CI)
- The caller handles the return value appropriately

Reserve `_logger.warning()` for conditions that actually require operator attention. Also avoid `exc_info=True` on INFO/WARNING calls in module install paths — the traceback in console output can independently trigger Odoo.sh failure detection.

### Do Not Launch a Browser Yourself — Use `browser_js`

`HttpCase.browser_js(url_path, code)` loads a URL in headless Chrome and runs
your snippet. `console.log("test successful")` passes; any `console.error`
fails the test with that message. `ChromeBrowser` is the **only** Chrome
launcher in Odoo — 250 test files drive a browser and every one goes through
it — so a hand-rolled subprocess is on its own for everything the framework
already solved:

- the container flags (`--no-sandbox`, `--disable-namespace-sandbox`,
  `--disable-dev-shm-usage`) without which Chrome dies on Odoo.sh;
- the viewport, which `browser_js` names through the `browser_size` class
  attribute (default `'1366x768'`, applied with
  `Emulation.setDeviceMetricsOverride`) — geometry assertions are meaningless
  against an unnamed default, which differs between macOS and Linux;
- `unittest.SkipTest` on every startup and connection fault.

`TestMfnlStub` learned this the long way. It drove its own Chrome over the
DevTools protocol against `file://` pages, and each of those three was
rediscovered as a red build. It was written before this repo could run a
browser test at all (`websocket-client` and the parallel runner's worker
filestore both landed 2026-09-04), which was a good reason then and not one
afterwards.

The one thing `browser_js` needs is a **URL**, so a test that measures markup
has to serve it. Point the test at the route that already renders that markup —
which is better anyway, because it measures what a browser really receives.

One WARNING still needs handling: a Chrome that cannot start logs
`Chrome headless failed to start` before it skips, and that row reddens an
Odoo.sh build. Wrap the call in `lower_logging(logging.INFO, logging.INFO)`,
which relabels it `_WARNING` at INFO instead of hiding it. Do **not** reach for
`mute_logger` here: it swallows the whole logger, including the `Chrome pid:`
line you are told to look for before trusting a green summary.

### A Local Tool That Cannot Start Is a Skip, Not a Failure

The rule above covers a remote service. The same reasoning covers a **local**
tool a test drives — a headless browser, a converter, a CLI. When the tool
cannot be **started**, the test learns nothing about the code under test, so it
must skip. Odoo does exactly this: `ChromeBrowser` in `odoo/tests/common.py`
raises `unittest.SkipTest` for a missing executable, an `OSError` on spawn, a
DevTools port that never appears, a connection it cannot make, and a page
target it never finds.

Guard on **"can it start"**, not on **"is it installed"**. `TestMfnlStub`
checked only that the Chrome binary existed, and on Odoo.sh the binary IS
found — it starts and then dies, because the container has no user namespaces
(`chrome exited with -5 before it published a DevTools port`). The narrow guard
never fired and the class errored a build that could never have run it.

Two details that matter:

- **Remember the failure on the class.** Otherwise every test pays the start
  cost again, and a tool that hangs rather than exits pays the full start
  timeout each time.
- **Log at `_logger.info()`.** `--log-db` copies WARNING and ERROR into
  `ir_logging` and any row turns the build red, so Odoo's own WARNING for this
  case is not available to us.

Say in the test file which assertions do not run on Odoo.sh. A skip is honest
only while somebody knows the cover is missing there.

### Resilient Live Integration Tests

Tests calling external APIs (Gemini, Google Maps, etc.) should use `skipTest()` for transient third-party errors instead of hard assertion failures. This prevents flaky CI from third-party outages:

```python
def _skip_on_third_party_error(test, result):
    """Skip test instead of failing when third-party API returns a transient error."""
    if '"error"' in result:
        _logger.info(
            "Third-party API error in %s — skipping: %s",
            test._testMethodName, result[:300],
        )
        test.skipTest(f"Third-party API error: {result[:200]}")
```

**INFO, not WARNING** — the helper exists so a third-party outage cannot redden
CI, and on Odoo.sh a WARNING does exactly that by itself. This example said
`_logger.warning` until 2026-09-09, when the live Google tests were found
reddening builds through the very helper meant to protect them.

**Call this helper on every live-API result before parsing or asserting.** The check is `'"error"' in result`, which catches more than network failures — it also catches business-level "no results" responses our wrappers return as `{"error": "...", ...}` (e.g. Google reverse-geocode returning empty `results[]` for valid-looking coordinates). Skipping this call in even one test method means a transient or no-data condition turns the whole suite red. Pattern:

```python
result = Service._ai_resolve_location_and_route(route_action="resolve_url", query=...)
_skip_on_third_party_error(self, result)
parsed = json.loads(result)
self.assertIn("place_id", parsed, ...)
```

### noupdate="1" XML Data and Fresh Installs

When removing features, migration scripts only run on existing databases. XML data with `noupdate="1"` is still applied on fresh installs (like Odoo.sh dev builds). If a cron or server action references a removed Python method:

- Set `active=False` in the XML record
- Provide a no-op code body (e.g., empty `code` field for server actions)
- Remove any `binding_model_id` from server actions

This prevents `ValueError` or `AttributeError` during fresh module install on Odoo.sh.

## Common Pitfalls

| Pitfall | Solution |
|---------|----------|
| Wrong test tag format | Use `.method_name` not `Class.method_name` |
| Tests don't terminate | Add `--stop-after-init` flag |
| Missing dependencies | Verify addons paths include all modules |
| Odd failures or one parallel worker red | See [Odoo test run issues](#odoo-test-run-issues)—often the test DB was not installed with the full `odoo.installModules` list |
| Tests not found | Check module loading and `@tagged` decorator |
| Schema errors | Add `-u module1,module2` to update |
| Slow tests | Combine related tests, use `setUpClass` instead of `setUp` |
| setUp runs per test | Convert to `setUpClass` -- runs once per class, savepoints handle isolation |
| "External requests verboten" | Test class has `standard` tag — add `-standard` to `@tagged()` for live API tests |
| Odoo.sh build red despite passing tests | WARNING/ERROR in `ir_logging` from fake PDFs, SQL constraint noise, or `_logger.warning()` in fallback code — see [CI-Safe Test Patterns](#ci-safe-test-patterns-odoosh) |
| Flaky CI from third-party API outages | Live integration tests hard-fail on transient errors — use `skipTest()` pattern for graceful degradation |
| `assertEqual(sum(records.mapped("monetary_field")), stored_total)` fails with `9131.779999999999 != 9131.78` | Python float arithmetic accumulates imprecision when summing many values. Wrap the sum in `currency.round(...)` before comparing: `assertEqual(currency.round(sum(records.mapped("amount"))), stored_total)`. The individual values are already correct — only the sum needs rounding. |
| SQL-scaffolded rows lose their computed children mid-test | Rows inserted via raw SQL have NULL stored-compute columns and **no recompute queued**. If a later flush triggers a dependent compute, it reads those NULLs as real values — e.g. a `rivercreds.plan.slot` scaffolded without `requirement_start`/`requirement_end` hits the "no requirement window" branch of `_compute_item_ids`, which **deletes all the slot's items** (scaffolded ones included) and regenerates none. Symptom: a fixture that survives simple tests breaks in tests that run a heavier save (an import, a wizard). Fix: scaffold every stored-compute column a downstream compute reads (the WP slot scaffold in `test_wp_close_with_existing_permit.py` includes the requirement window for this reason). |

## Test Validation Checklist

- [ ] Test runs without errors
- [ ] Both positive and negative scenarios covered
- [ ] Edge cases and boundaries tested
- [ ] Execution time reasonable (< 1 second for simple tests)
- [ ] No side effects on other tests
- [ ] Clear error messages on failure
- [ ] Follows existing patterns in codebase

## JS Unit Tests (Hoot)

JS unit tests live in `riverflow/static/tests/` (pure timeline maths: window snapping, range state, projection, tracks, row heights, `layout_range`), `rivercreds/static/tests/` (`today_position.test.js`, the today line / pill / edge-note position) and `crewradar/static/tests/` (the first **component** test, `plan_timeline_track.test.js`, mounting the shared `CrewPlanTimelineTrack`). Use them as templates. A new module's suite must also be added to `TestHootSuites.SUITES` in `crewradar/tests/test_hoot.py`, or the command-line runner never loads it.

**Hoot matcher gotchas** (09-Sep-2026): `toHaveProperty` is a **DOM** matcher (it wants a node), not an object matcher — check `obj.edge` with `toBe(undefined)` instead. A computed percentage is a float (`55.000…01`), so `toEqual({ leftPct: 55 })` fails: round it (`Math.round(x * 1000) / 1000`) before `toBe`.

**Framework**: Odoo 19's Hoot (`@odoo/hoot` — `describe` / `test` / `expect`). Pure-function tests that need no DOM declare `describe.current.tags("headless")`:

```js
import { describe, expect, test } from "@odoo/hoot";
import { parseStoredRange } from "@riverflow/components/timeline/range_persistence";

describe.current.tags("headless");

describe("parseStoredRange", () => {
    test("legacy bare-string rangeId still parses", () => {
        expect(parseStoredRange("weekly").rangeId).toBe("weekly");
    });
});
```

**Registration**: add the test glob to the `web.assets_unit_tests` bundle in the module manifest (pattern established in `riverflow/__manifest__.py`):

```python
"assets": {
    "web.assets_backend": [
        "riverflow/static/src/components/**/*",
        # Dark mode files load via web.assets_web_dark only — the components
        # glob above would otherwise pull the .dark.scss into the light bundle
        ("remove", "riverflow/static/src/components/timeline/timeline_scale_selector.dark.scss"),
        ...
    ],
    "web.assets_web_dark": [
        "riverflow/static/src/components/timeline/timeline_scale_selector.dark.scss",
    ],
    "web.assets_unit_tests": [
        "riverflow/static/tests/**/*",
    ],
},
```

(The `("remove", ...)` + re-add is the dark-SCSS variant to use when a `**/*` glob already swept the `.dark.scss` into `web.assets_backend`; with explicit file lists, just listing the file under `web.assets_web_dark` suffices — see [SCSS and Dark Mode](../SKILL.md#scss-and-dark-mode).)

**Odoo.sh rule (agreed 09-Sep-2026)**: Hoot tests must not run on Odoo.sh. They are loaded only by Odoo's own `WebSuite.test_unit_desktop` (module `web`, not in this repository), and any local headless runner test class is tagged `hoot` only — not `standard`, not the module name — so it runs solely with `--test-tags hoot`.

**Running from the command line**: `crewradar/tests/test_hoot.py` (`TestHootSuites`, tagged `hoot` only) opens `/web/tests` in headless Chrome through Odoo's own `browser_js`, filtered to the `@riverflow` and `@crewradar` suites by their hoot id hash (the same hash Odoo's `HOOTCommon` uses), and passes on `[HOOT] Test suite succeeded`:

```zsh
cd ~/oteny/radar && ~/odoo/venv/bin/python3 ~/odoo/odoo19/odoo-bin --stop-after-init -p 8068 --addons-path=~/odoo/odoo19/addons,~/odoo/enterprise19,~/oteny/radar --without-demo=True --http-interface 127.0.0.1 --test-enable --test-tags=hoot -u crewradar -d cr-test --max-cron-threads 0 --limit-time-real 0 --log-level=test 2>&1 | tee /tmp/hoot.log
```

`-u crewradar` is what makes Odoo run the crewradar test module; the JS bundle is rebuilt from the files on disk at page load, so no further update is needed after a JS edit. Per-test results are in the log as `[HOOT] Test "…" failed:` with the expected/received values. Do not edit JS or Python files while a run is going: the assets are read when the page loads and the Python packages at startup.

**Component test gotchas** (learned 09-Sep-2026): `mountWithCleanup` boots the web **and mail** services (crewradar depends on mail), which read `res.users` and `discuss.channel` — call `defineMailModels()` from `@mail/../tests/mail_test_helpers` at module level, `defineWebModels()` is not enough. Keep luxon `DateTime` instances out of `useState` (use a plain object plus a reactive version counter). The browser stores a style percentage with four decimals, so compare `parseFloat(el.style.left)` rounded, not the raw string. To prove a component test red, swap the fixed file for `git show HEAD:<path>` and rerun, then restore.

**Running in the browser**: open the Hoot runner at `http://localhost:8069/web/tests` (login required). The runner UI lists suites by module/file and supports filtering to a single suite or test; failures report per-assertion diffs in the browser console and page. Test files are only served from the `web.assets_unit_tests` bundle — they are never part of the production asset bundles.

## Browser Testing

### AI Agent Policy

**Browser/UI testing requires explicit user approval** before starting a session. It is slow, can interfere with the user's work, and is only justified for client-side changes (QWeb, JS, SCSS) that cannot be verified any other way.

**Prefer the Odoo shell** for verifying data, business logic, and ORM behavior — it's faster (~1.2s startup), non-intrusive, and read-only queries need no approval. See [Odoo Shell](odoo-shell.md) for usage and AI agent guidelines.

### Overview

AI agents with browser capabilities can test client-side code interactively. This is useful for:

- QWeb template changes
- JavaScript component testing
- SCSS styling verification

### Accessing the Application

Navigate to the application with debug mode:

```
http://localhost:8069/odoo/crew-planning?debug=assets
```

Login credentials: `admin/admin`

### Refreshing Changes

| Change Type | Action |
|-------------|--------|
| QWeb templates | Browser refresh (Cmd+R) |
| JavaScript | Browser refresh (Cmd+R) |
| SCSS | Browser refresh (Cmd+R) |
| Python | Restart Odoo server |

If refresh doesn't pick up changes, use the debug menu's "Regenerate Assets" option (available with `?debug=assets`).

### Stale Assets: Service Worker Beats Cmd+R

When a JS/SCSS change refuses to appear even after a server restart **and** "Regenerate Assets", the culprit is usually **Odoo's PWA service worker** caching the bundle in Cache Storage — it serves the old bundle without revalidating, so a soft **Cmd+R** loses. JS and CSS are **separate bundles** (`web.assets_web.js` vs `web.assets_web.css`) cached independently, so you can end up with fresh JS (e.g. a new class appears on the element) but stale CSS (the rule is missing) — a confusing half-applied state.

Fix order: **Cmd+Shift+R** (hard reload bypasses the SW for sub-resources) → if still stale, DevTools → **Application → Service Workers → Unregister** + **Cache Storage** clear (or **Clear site data**) → DevTools → **Network → Disable cache**, reload.

**Verify what the server actually serves with `curl`** (bypasses the browser entirely; needs the bundle is `no-cache` so the server side is always fresh). Authenticate, then fetch the debug bundles the page links (`/web/assets/debug/web.assets_web.js` and `.css`) and grep for your change:

```bash
curl -s -c /tmp/cj.txt -X POST http://127.0.0.1:8069/web/session/authenticate \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","params":{"db":"<db>","login":"admin","password":"admin"}}' -o /dev/null
curl -s -b /tmp/cj.txt http://127.0.0.1:8069/web/assets/debug/web.assets_web.js | grep -c "myNewClass"
curl -s -b /tmp/cj.txt http://127.0.0.1:8069/web/assets/debug/web.assets_web.css | grep -n "my_new_rule"
```

If the bundle contains your change but the browser doesn't reflect it, the problem is 100% browser-side caching (the SW). The individual static-file route (`/<module>/static/src/.../file.js`) always serves the raw on-disk file, so it confirms the disk is current but **not** what the bundle/browser uses — check the actual bundle.

### Debug Mode Features

With `?debug=assets`:

- Debug menu visible in UI
- Asset regeneration available
- Component inspection tools
- Network request visibility

### Browser Environment

- Browser: Chrome on MacOS
- Refresh shortcut: Cmd+R
- The browser MCP tools can navigate, click, type, and take snapshots

### Testing Workflow

1. Navigate to the page under test
2. Take a snapshot to see page elements
3. Interact with elements (click, type, etc.)
4. Re-snapshot to verify changes
5. Use screenshot tool for visual inspection if needed
6. Repeat for each feature/scenario

### Important Notes

- Don't attempt to start the dev server unless prompted
- Don't guess the port - find it in codebase or ask
- Tests should not run in sandbox (need database access)
