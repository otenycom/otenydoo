---
name: odoo-development
description: Odoo 19 development patterns and best practices for this workspace. Covers ORM patterns, compute methods, testing guidelines, XML conventions, and SCSS handling. Use when writing or reviewing Python models, XML views, tests, or frontend code in any Odoo addon.
---

# Odoo 19 Development

Development patterns and best practices for Odoo 19 addons in this workspace. This skill applies to crewradar, riverflow, rivercreds, and oteny_audit modules. Based on official [Odoo Coding Guidelines](https://www.odoo.com/documentation/19.0/contributing/development/coding_guidelines.html).

## When to Use This Skill

- **Developers**: Writing Python models, compute methods, XML views, or SCSS
- **AI Agents**: Generating or reviewing Odoo code, running tests, quick-checking data via [Odoo Shell](references/odoo-shell.md)
- **Code Review**: Validating code follows Odoo 19 patterns
- **Performance**: Analyzing Odoo profiler traces via [Profiler Analysis](references/profiler-analysis.md)

## Quick Start

Key patterns to follow:

1. **Use Odoo 19 syntax**: `<list />` not `<tree />`, no `<data />` wrapper unless `noupdate=1`
2. **Compute methods**: Use `@api.depends()` for side effects, not `write()`/`create()` overrides
3. **Active records**: Always check `active` field explicitly when iterating models with archiving
4. **Testing**: Use `--stop-after-init` flag, don't call `_compute` methods directly, prefer `setUpClass` over `setUp` for shared test data. In Crewradar-derived addons such as `crewradar_wilma`, `crewradar_cuneus_sign`, and `crewradar_creds`, every tagged test class should include the generic `crewradar` tag in addition to the module-specific tag, so `--test-tags=crewradar` runs the full Crewradar suite in one go. That convention is a convenience, not the deploy gate: the gate runs the tag list in `.vscode/settings.json` → `odoo.testTags`, so a class in a non-Crewradar addon (`riverflow`, `oteny_audit`) gates a deploy through its own addon tag and must not be given a `crewradar` tag it does not deserve
5. **Readability first**: Favor readability over conciseness or clever idioms
6. **Think extendable**: Small methods, hardcoded logic extracted to overridable helpers
7. **Module boundaries**: A module's migrations and hooks must only reference its own fields, data, and dependencies — never fields or data from modules higher in the dependency tree. See [Migrations — Module Boundary Rules](references/migrations.md#module-boundary-rules)
8. **Migrations**: When removing fields, use pre-migration to delete stale views. When deleting records with computed dependencies, use ORM `unlink()` in post-migrate, not raw SQL DELETE (see [Migrations](references/migrations.md)). **Folder version safety**: every `git merge` into dev/main is gated by an auto-renumber hook + CI workflow that prevents stale migration folders from landing — see [Migration Version Gate](references/migration-version-gate.md) for the architecture, developer briefing, and troubleshooting Q&A. **On a long-lived feature branch, author migrations at a higher MINOR** (dev at `19.0.4.x` → use `19.0.5.1`, `19.0.5.2`, …) so the pipeline's last-segment auto-bumps can never overtake them — local prod restores + `-u` on the branch aren't covered by the gate (see [Migrations — Version Format](references/migrations.md#version-format)).
9. **Prefer XML data + migrations over install hooks**: Use XML data records for new data and post-migration scripts for updating existing (live) data. Reserve `post_init_hook` only for cross-module enrichment that cannot be done in XML (e.g. setting fields on noupdate records from another module). Never put one-time data migration logic in hooks — it belongs in versioned migration scripts. See [Install Hooks](#install-hooks-vs-migrations)
10. **Always set `path` on new actions**: Every new `ir.actions.act_window` must declare a kebab-case `path` slug so it is reachable as `/odoo/<slug>` instead of only `/odoo/action-<id>`. See [XML Guidelines — URL Path Slug](references/xml-guidelines.md#url-path-slug)
11. **New fields on `noupdate="1"` data**: Put the field on the canonical `<record>` for fresh installs; use a **post-migrate** for existing DBs — a follow-up XML file with `noupdate="0"` does not reliably update rows when `ir.model.data.noupdate` is already true. See [XML data and `noupdate`](references/xml-data-noupdate.md)
12. **Notebook tab toolbars**: List tabs use solid buttons in a `d-flex align-items-center gap-2 mb-3` wrapper — `btn btn-primary` for the one Add/New action, `btn btn-secondary` for Full Screen (N) and navigation — never `oe_link`. Give each list tab a `Full Screen (N)` button that opens the tab's model as a filtered full list. See [XML Guidelines — Notebook Tab Toolbars](references/xml-guidelines.md#notebook-tab-toolbars-list-tabs)
13. **`--dev xml` does not reload Python.** A form that Oops with `"model"."field" field is undefined` after a view edit usually means the arch on disk already names a new field while the running process still has the old `_fields`. Restart the process so `-u` loads the field. A live `button_immediate_upgrade` reuses the Python already in memory and does not fix it.

## Module Structure

Standard Odoo module directory layout:

```
addons/module_name/
├── __init__.py
├── __manifest__.py
├── controllers/          # HTTP routes
├── data/                 # Demo and data XML files
├── models/               # Model definitions
├── report/               # Reports (SQL views, printables)
├── security/             # Access rights, groups, rules
├── static/               # Web assets (js, scss, img, lib)
│   └── src/
│       ├── js/
│       ├── scss/
│       └── xml/          # QWeb templates for JS
├── tests/                # Python tests
├── views/                # Backend views and templates
└── wizard/               # Transient models and views
```

### File Naming

- **Models**: Split by main model, one file per model set (e.g., `sale_order.py`, `res_partner.py`)
- **Views**: `<model_name>_views.xml` (e.g., `sale_order_views.xml`)
- **Security**: `ir.model.access.csv`, `<module>_groups.xml`, `<model>_security.xml`
- **Data**: `<model>_data.xml`, `<model>_demo.xml`
- **Wizards**: `<transient>.py` and `<transient>_views.xml` in `wizard/` directory
- File names: lowercase alphanumerics and underscores only (`[a-z0-9_]`)

## Core Concepts

### ORM Best Practices

Odoo's ORM handles caching, flushing, and transactions automatically. Work with it, not against it:

- **Property assignment over write()**: Use `order.partner_id = x` not `order.write({"partner_id": x})`
- **Rely on ORM cache**: Navigate recordsets with `filtered()`, `sorted()` instead of new `search()` calls
- **No explicit flushes**: The ORM handles flush timing; avoid `invalidate_recordset()` in tests
- **Command syntax**: Use `Command.create()`, `Command.set()`, `Command.link()` for One2many assignments
- **SQL constraints as class attributes**: Odoo 19 declares them as `_code_unique = models.Constraint("unique(code)", "The document type code must be unique")` — the legacy `_sql_constraints` list is deprecated (workspace examples: `rivercreds/models/rivercreds_credential_type.py`)
- **Never commit transactions**: The framework handles `cr.commit()`; manual commits break atomicity
- **No FK to separate-cursor records**: Records created via `self.env.registry.cursor()` and committed independently are invisible to the main transaction's FK constraint checks. Writing a Many2one pointing to such a record causes `ForeignKeyViolation` at flush. Use a plain Integer reference field (e.g., `channel_id_ref`) and look up via domain filter instead of a relational field.

### Compute Method Patterns

Compute methods are the Odoo way to implement business logic:

- **@api.depends()**: List all field dependencies; ORM triggers recomputation automatically
- **Stored computed fields**: Set `store=True` for computed fields that need database storage
- **`.exists()`-guard the compute that ASSIGNS a stored Many2one, never a compute that merely reads it**: the guard on the assignment (`_compute_log_entry_id`) keeps a dangling id out of the database; the same guard on a reader (`_compute_employee_id_site_id`) would let the id be written and flush would raise `ForeignKeyViolation` instead. Tests should still use a real `res_id`. See [Testing Guidelines](references/testing-guidelines.md) and [Coding Patterns — Stored FK computes](references/coding-patterns.md#stored-fk-computes-guard-the-assignment-never-the-reader)
- **One2many computed fields**: For creating multiple related records as side effects, use a stored computed One2many field
- **Consume a side-effecting compute THROUGH its field, never around it**: a pending stored compute only executes on read/flush — reading the field (e.g. `entry.service_ids`, what a UI read does) runs it; a generic `search()` on the *comodel* (e.g. by `res_model`/`res_id`) does **not** flush it, so a headless script that searches around the relation sees stale/absent side-effect records and, if it then creates its own, produces a duplicate once the compute later fires. Bit the Barney fixture seeder live (`crewradar_cuneus_sign/tools/seed_mfnl_fixtures.py`) — auto-add services materialized only after a manual create. `@api.depends` cannot fix this: it controls *invalidation*, not *when* the pending compute runs
- **Editable computed One2many**: For wizard lines users need to edit (e.g., checkboxes), use `store=True` + `readonly=False` on the model field, `force_save="1"` on readonly/invisible child fields in the view, and hidden fields to preserve round-trip values (see [Coding Patterns](references/coding-patterns.md))
- **One2many line-to-line cascade**: `@api.onchange` on a line model can only modify `self` in the browser — sibling line changes are silently discarded. Place the onchange on the parent model instead (`@api.onchange('one2many_field')`). See [Coding Patterns — One2many Line-to-Line Cascade](references/coding-patterns.md)
- **Never call _compute directly**: In tests, rely on `@api.depends` triggers instead
- **A compute that unlinks records must publish its scalar results FIRST**: core `unlink()` calls `env.flush_all()` before it deletes, so every pending compute in the transaction runs *inside* the compute that called unlink. A dependent computed in that nested flush reads the caller's stored fields as they were before the compute, and an assignment made inside a compute never calls `modified()`, so the dependent is never re-marked. Assign the scalar fields before the delete/create/write of the child records (agreed 09-Sep-2026 for `rivercreds.plan.slot._compute_item_ids`; incident: a Renew Passport successor service dated at the expired passport's marker). A consuming business documents its own worked example of this pattern in its own skill bundle.

### Install Hooks vs Migrations

The live database already has all modules installed, so `post_init_hook` only runs on fresh installs (primarily the test database `cr-test`). Use the right tool for each scenario:

| Scenario | Tool | Why |
|----------|------|-----|
| New data records | XML data files (`noupdate="1"`) | Loaded on first install, skipped on upgrade |
| Enrich upstream records from a downstream module | XML `<data noupdate="0">` block in the downstream module | Applies on both fresh install and `-u`; references upstream records via `module.xml_id` |
| Update existing live data | Post-migration script (`migrations/x.y.z/post-migrate.py`) | Runs once per version bump, targets specific records |
| Cross-module enrichment that XML cannot express | `post_init_hook` | Reserve for cases where downstream XML IDs are not yet loaded when upstream needs them, or where the value depends on runtime data |
| One-time data migration | Post-migration script only | Never in hooks — hooks run on every fresh install but the source data doesn't exist on fresh DBs |

**Prefer XML for cross-module enrichment**: When a downstream module needs to set fields on a record owned by an upstream module (e.g. `crewradar_cuneus_sign` linking `crewradar.auto_add_template_service_send_auv_information.credential_type_group_id` to a group defined in the downstream module), use a `<data noupdate="0">` section in the downstream module that references the upstream record by its full external id:

```xml
<data noupdate="0">
    <record id="crewradar.auto_add_template_service_send_auv_information"
            model="riverflow.auto.add.service">
        <field name="credential_type_group_id" ref="credential_type_group_auv" />
    </record>
</data>
```

This is declarative, version-controlled, applies on both fresh install and `-u`, and keeps plan flags / linkage data visible in the data file rather than scattered across Python hooks. Reserve `post_init_hook` for enrichment that XML cannot express.

**Common anti-pattern**: Setting `show_in_credential_plan`, `scope`, `action_start_before_days`, `renew_before_expiry_days`, `has_expiry_date`, etc. via `post_init_hook` when the record itself is defined in XML. All such flags belong in the `<record>` definition directly — records should be fully self-describing in XML.

**Another anti-pattern**: Putting migration logic (workflow state remapping, data backfilling, record consolidation) in `post_init_hook`. These are no-ops on fresh install and should be migration scripts instead. Keep the hook lean.

**Cross-module ref constraint**: XML data files must not reference records from modules that are not in `depends`. If module A's XML references `module_b.some_record` but A doesn't depend on B, fresh installs fail with `External ID not found`. The `noupdate=0` enrichment pattern above works only in the *downstream* direction (depending module references its dependency's records), never upward.

Adding **new columns** to records that already ship with `noupdate="1"` requires a **post-migrate** on existing databases; see [XML data and `noupdate`](references/xml-data-noupdate.md). Note that `ir.model.data.noupdate` is stamped on the **row** at creation and never re-synced from the file — a file that is not `noupdate` today can still have `noupdate=true` rows that `-u` skips, so check the row on the target database before trusting `-u` (see [Row noupdate is stamped at creation](references/xml-data-noupdate.md#row-noupdate-is-stamped-at-creation--check-the-row-not-the-file)).

### Active Records and Archiving

Models with an `active` field need special handling because `active_test` context varies:

**Models with active field in this workspace**:

- `crewradar.log.entry`
- `crewradar.site`
- `rivercreds.credential`
- `riverflow.auto.add.service`
- `riverflow.service`
- `riverflow.state.record`
- `riverflow.state`
- `riverflow.transition.action`
- `riverflow.transition`
- `riverflow.workflow`

**Rule**: When iterating or searching these models, always explicitly check `active`:

```python
# Good: explicit active check
entries = self.env['crewradar.log.entry'].search([
    ('employee_id', '=', employee.id),
    ('active', '=', True),  # Always explicit
])

# Good: filtered with active check
active_entries = entries.filtered(lambda e: e.active)
```

**This applies to One2many traversals too**: `self.log_entry_ids.filtered(...)` must include `e.active` in the lambda. The Odoo UI sets `active_test=False` on certain write operations, which cascades into compute methods. Without the explicit check, archived records appear in One2many fields and can pollute computed results with stale data.

**Prefer FK comparison over One2many containment** when excluding a record's own related records: use `c.service_id != service` instead of `c not in service.credential_ids`. One2many traversals are sensitive to ambient `active_test` context, which varies between callers (wizard `default_get` vs `@api.depends` compute). Direct FK comparisons are context-independent. See [Coding Patterns — FK vs One2many Containment](references/coding-patterns.md#prefer-fk-field-comparison-over-one2many-containment).

### Date Handling

Use Odoo's date utilities:

```python
from odoo import fields

# Current date
today = fields.Date.today()

# Current datetime
now = fields.Datetime.now()
```

### Python Conventions

#### Import Order

```python
# 1. Python stdlib (one per line, alphabetically sorted)
import base64
import re
from datetime import datetime

# 2. Odoo submodules (alphabetically sorted)
from odoo import Command, _, api, fields, models
from odoo.tools.safe_eval import safe_eval

# 3. Odoo addons (rarely, only if necessary)
from odoo.addons.website.models.website import slug
```

#### Naming Conventions

| Item | Convention | Example |
|------|------------|---------|
| Model name | Singular, dot notation | `sale.order`, `res.partner` |
| Transient | `<base_model>.<action>` | `account.invoice.make` |
| Python class | PascalCase | `SaleOrder` |
| Variable (record) | PascalCase | `Partner = self.env['res.partner']` |
| Variable (common) | snake_case | `partner_ids`, `total_amount` |
| Many2one field | `_id` suffix | `partner_id`, `user_id` |
| One2many/Many2many | `_ids` suffix | `line_ids`, `tag_ids` |

#### Method Naming

| Method Type | Pattern | Example |
|-------------|---------|---------|
| Compute | `_compute_<field_name>` | `_compute_total` |
| Search | `_search_<field_name>` | `_search_display_name` |
| Default | `_default_<field_name>` | `_default_company_id` |
| Selection | `_selection_<field_name>` | `_selection_state` |
| Onchange | `_onchange_<field_name>` | `_onchange_partner_id` |
| Constraint | `_check_<constraint_name>` | `_check_date_order` |
| Action | `action_<name>` | `action_confirm` |

#### Model Attribute Order

1. Private attributes (`_name`, `_description`, `_inherit`, ...)
2. Default methods and `default_get`
3. Field declarations
4. Compute, inverse and search methods (same order as field declaration)
5. Selection methods
6. Constraints (`@api.constrains`) and onchange (`@api.onchange`)
7. CRUD methods (ORM overrides)
8. Action methods
9. Business methods

### File Organization

When adding files to an Odoo module:

- **Python files**: Add to `__init__.py` in the same folder
- **XML files**: Add path to `__manifest__.py` in `data` or `views` array
- **SCSS files**: Add to `__manifest__.py` `assets` section

### SCSS and Dark Mode

Odoo loads dark mode SCSS files only when dark mode is active:

```python
# In __manifest__.py
'assets': {
    'web.assets_backend': [
        'crewradar/static/src/scss/planning.scss',      # Light/default mode
        ('after', 'web/static/src/scss/primary_variables.dark.scss',
         'crewradar/static/src/scss/planning.dark.scss'),  # Dark mode
    ],
}
```

**Rule**: Don't use `@media (prefers-color-scheme: dark)` queries. Use separate `.dark.scss` files.

**When no `.dark.scss` is needed**: `web.assets_web_dark` is `('include', 'web.assets_web')` plus the dark variable files, so every backend SCSS file is compiled a second time with the dark variable set. A file whose colours all come from Odoo or Bootstrap variables (`$o-action`, `$o-component-active-bg`, `$input-border-color`, `$body-color`, ...) gets its dark colours for free; only hard-coded colours need a `.dark.scss` override. Example (historical): `service_filter.scss` of the former Services tab Show control, retired in crewradar 19.0.10.69.

**Bootstrap 5.3 gotchas in Odoo**: Odoo compiles Bootstrap with an empty variable prefix, so button custom properties are `--btn-bg`, `--btn-active-bg`, ... (not `--bs-btn-*`); write them as `--#{$prefix}btn-bg` and they come out right. `$btn-border-width` is `var(--border-width)`, so `-$btn-border-width` compiles to the invalid `-var(--border-width)`; negate with `calc(#{$btn-border-width} * -1)` as Bootstrap's own `.btn-group` does.

**A field inside a flex toolbar**: Odoo renders every `.o_field_widget` as inline-block with a form margin-bottom (`web/static/src/views/fields/fields.scss`, `form_controller.scss`). Next to `.btn` elements in a `d-flex align-items-center` row, the inline-block baseline gap and the margin make the wrapper taller than its content and push the content above the buttons' centre line. Set `display: flex; margin-bottom: 0` on the wrapper (with `.o_field_widget.<hook>` for specificity). Example (historical): the former Services tab Show control, retired in crewradar 19.0.10.69 in favour of oteny_shortcut Forms shortcuts, which are plain buttons and need none of this.

**Compile check without a browser**: a throw-away test can compile both bundles and fail on SCSS errors: `bundle = self.env["ir.qweb"]._get_asset_bundle("web.assets_web", js=False)`, `css = bundle.preprocess_css()`, then assert `bundle.css_errors` is empty (repeat for `web.assets_web_dark`). Register the file in `tests/__init__.py` for the run (the package imports test files explicitly) and remove both afterwards. One bundle compiles in about a second on cr-test.

## Technical Reference

### Fresh Database for Testing

For a new test database, install the full module set for the business repo
you are testing against — installing only the base module leaves its
derived addons untested or skipped. The consuming business repo documents
its own module list and dependency table in its own skill bundle.

### Prod restore then `-i` / `-u`

A production dump can be missing a module its on-disk schema now expects,
so a plain `-u` after `odoo-bin db load` skips the dependent module and
leaves stale columns undefined. Restore tooling must run `-i` then `-u` of
the full module list, and restart the server process afterward — browser
Refresh is not enough. The restore script and the full incident are owned
by the consuming business, in its own skill bundle.

### Skill sync XML-RPC return

`oteny.knowledge.sync.sync_skills_to_knowledge` must return a marshalable
dict (`{"articles_synced": N}`), never `None` — Odoo 19 XML-RPC dumps with
`allow_none=False`. Full history and the manual-call recipe:
[oteny-knowledge-sync — Manual admin call](../oteny-knowledge-sync/SKILL.md#manual-admin-call-xml-rpc).

### Odoo test run issues

When tests fail in odd ways (empty recordsets, missing refs, or many failures on **one** parallel worker only), first verify the database was installed with the **complete** `oteny_audit,oteny_shortcut,oteny_bot,oteny_backup_trigger,odoo_parallel_tests,riverflow,rivercreds,crewradar,crewradar_wilma,crewradar_cuneus_sign,crewradar_creds,crewradar_sign,crewradar_marinetraffic` list—not a subset such as `-i rivercreds` or `-i crewradar` alone. Post-install tests in this workspace assume that full module graph; omitting installed modules changes which models and data exist at runtime. Do not adapt tests to pass under partial installs. See [Testing Guidelines — Odoo test run issues](references/testing-guidelines.md#odoo-test-run-issues) and [Parallel Test Runner — Troubleshooting](references/parallel-test-runner.md#troubleshooting).

### Browser tests (tours)

A `HttpCase.start_tour` test needs Google Chrome and the `websocket-client` package in `~/odoo/venv`. Without the package Odoo logs `skipped ... websocket-client module is not installed` at INFO level and the summary still reads `0 failed, 0 error(s)`, so a red tour looks green. Look for `Chrome pid:` and `tour succeeded` in the log before you trust the summary line.

Browser tests run inside the regular parallel suite: since 2026-09-04 the parallel runner gives every worker clone a hardlinked replica of the base filestore (see [Parallel Test Runner — Worker filestore](references/parallel-test-runner.md#worker-filestore)). Before that a worker's web client could not load its asset bundles and a tour never became ready ("The ready code was always falsy"). The guard test `rivercreds/tests/test_browser_prerequisites.py` fails loudly, in the regular suite, when `websocket-client` or a Chrome binary is missing, so the silent skip cannot hide again.

Three tour rules learned on the first tour in this repo (`rivercreds_document_import_upload_race`, 2026-09-04): a trigger must be visible, so a hidden `<input type="file">` needs `:not(:visible)`; a tour that ends with a dirty form open fails after every step succeeded, so close the wizard as the last step; and tours live in `static/tests/tours/` under the `web.assets_tests` manifest bundle, driven by a `post_install` HttpCase.




### Odoo.sh CI/CD Build Compatibility

Odoo.sh dev builds are marked "Test: Failed" based on two mechanisms — not just test assertion failures:

1. **ir_logging table**: The install command uses `--log-db <dbname>`, which writes WARNING+ log entries to the `ir_logging` PostgreSQL table. Any entry causes the build to go red, even when all tests pass.
2. **Console output**: Tracebacks in stdout/stderr during install (even from caught exceptions logged with `exc_info=True`) can also trigger failure detection.

**Key rules for CI-safe code:**

- Use valid PDF data in tests (not fake bytes) — Odoo provides `base/tests/minimal.pdf`
- Decorate SQL constraint violation tests with `@mute_logger("odoo.sql_db")`
- Use `_logger.info()` (not `.warning()`) for graceful fallbacks where the operation continues successfully
- Avoid `exc_info=True` on WARNING calls in module install code paths
- When removing features, ensure `noupdate="1"` XML data still works on fresh installs (set `active=False`, no-op code body, remove `binding_model_id`)

See [Testing Guidelines — CI-Safe Test Patterns](references/testing-guidelines.md#ci-safe-test-patterns-odoosh) for code examples. A consuming business documents its own build-debugging procedures (SSH, ir_logging queries, log inspection) in its own skill bundle.

### Mail templates (inline_template)

Bodies rendered with **`engine='inline_template'`** (e.g. via `mail.render.mixin`) support **`{{ }}` expressions only**, not Jinja2 **`{% %}`** blocks — block tags render as literal text. Use inline ternaries for conditionals; watch fields like `sent_date` that are unset until a later action (wizard preview). See [Mail template rendering (inline_template)](references/coding-patterns.md#mail-template-rendering-inline_template).

### Workspace Paths

| Module | Path |
|--------|------|
| A business's own modules (e.g. `crewradar`, `rivercreds`) | `<business-repo>/<module>` |
| `riverflow` | `~/oteny/otenydoo/riverflow` |
| `oteny_audit` | `~/oteny/otenydoo/oteny_audit` |
| `oteny_knowledge_sync` | `~/oteny/otenydoo/oteny_knowledge_sync` |
| Odoo core | `~/odoo/odoo19` |
| Odoo enterprise | `~/odoo/enterprise19` |

### Command Syntax Reference

For One2many field assignments, use `Command` class:

```python
from odoo.fields import Command

# Link existing record
order.line_ids = [Command.link(line.id)]

# Create new record
order.line_ids = [Command.create({'product_id': product.id, 'qty': 1})]

# Set to specific IDs (replaces all)
order.line_ids = [Command.set([line1.id, line2.id])]

# Unlink (remove from relation)
order.line_ids = [Command.unlink(line.id)]

# Clear all
order.line_ids = [Command.clear()]

# Update existing
order.line_ids = [Command.update(line.id, {'qty': 5})]

# Delete from database
order.line_ids = [Command.delete(line.id)]
```

Command constants: `CREATE=0, UPDATE=1, DELETE=2, UNLINK=3, LINK=4, CLEAR=5, SET=6`

## Roadmap

### Future Ideas

- [x] Parallel test runner: Odoo module `odoo_parallel_tests` that patches the test runner for parallelized test execution via database cloning and worker subprocesses. Workers pull one test class at a time from a shared queue (longest first, from stats persisted across runs), so the makespan does not depend on estimates; clones form a pool reused via schema + XML-ID + module-version fingerprints whatever the worker count, copied with `STRATEGY = FILE_COPY` on PostgreSQL 15+. Installed as dependency of crewradar. ~11x speedup on full suite (repeated runs). Falls back gracefully to sequential on Odoo.sh. (see [Parallel Test Runner](references/parallel-test-runner.md))
- [x] SH backup fast restore, audit-history filtering, and 30s lite restore: a consuming business's own restore tooling can auto-detect lite (no filestore) SH backups, strip audit COPY data, and force a SQL-only replay when the zip bundles a filestore, skipping the filestore unzip and Odoo registry boot entirely. Owned by the consuming business, in its own skill bundle.
- [x] Deploy tool CI pipeline, feature-branch merge migration safety, and auto-merge after restore: a consuming business's own CLI can enforce a strict promotion pipeline and a migration-renumbering safety net on merge. Owned by the consuming business, in its own skill bundle.

## References

- [Testing Guidelines](references/testing-guidelines.md) - Unit and browser testing procedures
- [Coding Patterns](references/coding-patterns.md) - ORM patterns, Python idioms, extendability, mail `inline_template` pitfalls
- [XML Guidelines](references/xml-guidelines.md) - XML formatting, naming conventions, inheritance
- [XML data and `noupdate`](references/xml-data-noupdate.md) - `ir.model.data.noupdate`, upgrades vs fresh install, post-migrate for new fields
- [SCSS Guidelines](references/scss-guidelines.md) - CSS/SCSS formatting, naming, variables
- [Migrations](references/migrations.md) - Pre/post-migration scripts, view cache cleanup
- [Odoo Colors](references/odoo-colors.md) - Color integer reference for Odoo views
- [Parallel Test Runner](references/parallel-test-runner.md) - Parallelized Odoo test execution with LPT balancing (Developers)
- [Profiler Analysis](references/profiler-analysis.md) - Analyzing Odoo's built-in profiler traces from `ir_profile` for SQL query patterns, N+1 detection, and before/after benchmarking (AI Agents, Developers)
