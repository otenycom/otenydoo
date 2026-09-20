# Odoo Migration Patterns

Guide for writing pre-migration and post-migration scripts for Odoo module upgrades.

## XML `noupdate` and new fields on existing data

If a record was created from XML with `noupdate="1"`, its `ir.model.data` row stores `noupdate=True`. On **module upgrade**, Odoo often **does not** apply new fields from XML to that row, even from another file with `noupdate="0"`. **Post-migrate** (or an explicit policy change on `ir.model.data`) is the supported way to set new field values on existing databases. Canonical XML should still declare the field for **fresh installs**. See [XML data and `noupdate`](xml-data-noupdate.md).

## Version Format

Every module here uses **4-segment versions**: `19.0.<major>.<minor>` (e.g. `19.0.9.63`). The live `oteny_knowledge_sync` version is `19.0.2.0`. Five segments (`19.0.1.0.1`) are history only. Odoo 19.0 rejects a version that does not start with `19.0.` (`19.1.0.0` was uninstallable / `installable=False`). `19.0.1.0` is not greater than `19.0.1.0.1`. Migration folder names must match this format. Radar's `increment_version.py` bumps the last segment of any dotted version. A missing generic file is an error, not a skip.

When creating a new migration **on `dev`**, set the folder name to the module's current manifest version (or one higher). The `merge-branches` tool (`python -m riverdeploy merge-branches`) automatically detects and renumbers migration folders on feature branches that have become stale relative to `max(dev, main)`. A consuming business documents its own deploy-tool docs, Feature Branch Merges, in its own skill bundle.

**On a long-lived feature branch, bump the MINOR segment instead** (e.g. dev is at `19.0.4.26` → number your migrations `19.0.5.1`, `19.0.5.2`, … and set the manifest to match) — but take the minor from the allocation table below, never by picking "dev's minor + 1" yourself. Rationale: the deploy pipeline keeps auto-incrementing dev/main's last segment while your branch lives, and the moment the ceiling catches up with your folder version, a **local prod restore + `-u` on the branch silently skips your migration** — the merge gate only fires on merges into dev/main, never on a local restore. `increment_version.py` only ever bumps the LAST segment (`parts[-1] + 1`), so a higher minor can't be caught by auto-bumps, restores keep upgrading correctly for the branch's whole lifetime, and on merge the gate leaves folders above the ceiling untouched (dev's version then continues from your minor). See [Migration Version Gate — When you add a new migration](migration-version-gate.md#when-you-add-a-new-migration).

### Minor allocation

**A minor is a per-module namespace owned by exactly one branch at a time. Claim yours in this table *before* authoring, and never author in a minor you don't own.** "Pick dev's minor + 1" is NOT a rule you can apply on your own — two concurrent branches that both apply it choose the same number.

| Module | Minor | Owner |
|---|---|---|
| `crewradar_cuneus_sign` | `19.0.5.x` | dev / main (mainline; `feature/work-permit-v2` merged its 5.x here) |
| `crewradar_cuneus_sign` | `19.0.6.x` | dev / main (mainline — the `barney` branch merged and was deleted on 2026-08-20, so its minor folded in; `dev`'s manifest continues from `19.0.6.x`) |
| `crewradar` | `19.0.10.x` | dev / main (mainline — same fold; claimed by `barney` on 2026-08-11 for the ship-contact-type split, retired 2026-08-25) |

Allocation is **per module** — the same number in another module is unrelated (`crewradar` has had `19.0.6.x` folders since long before barney claimed `crewradar_cuneus_sign` 6.x). Only claim a minor in a module you actually add migrations to. When a branch merges and dies, its minor folds into mainline and the row retires; dev's manifest continues from that minor.

Two case studies, both the same bug:

1. **The auto-bump catch-up (2026-07).** A long-lived branch authored a migration at the same folder version dev's own auto-bumps independently reached, so a prod restore + `-u` skipped the folder and the migration never applied. Renumbering to a fresh minor fixed it — and motivated the higher-minor rule above. Full incident documented with the consuming business that hit it, in its own skill bundle.
2. **The un-allocated minor (2026-07, the dev→barney merge).** Barney *and* `feature/work-permit-v2` each applied "dev is at 4.x → use 5.x" independently and both landed on `19.0.5.1`–`5.5`. work-permit-v2 merged to dev first, so barney's five folders collided path-for-path with five unrelated dev folders (add/add conflicts on identical paths, different content), and dev's own bumps had climbed to `19.0.5.39`. The `merge-branches` ceiling renumber parks stale folders at *ceiling + 1* (`19.0.5.40`–`5.44`) — correct for a merge-and-done branch, but on a long-lived one that margin is a single bump, and this module burns ~16 bumps/month, so case study 1 would have re-fired within days. Fixed by allocating barney the `19.0.6.x` minor. **Takeaway: after `merge-branches` renumbers a long-lived branch, check the margin — ceiling + 1 is not a durable home.**

**Folder version safety is enforced**: every `git merge` into `dev` or `main` is gated by an auto-renumber pre-merge-commit hook + a GitHub Actions CI workflow. The hook auto-fixes stale folder versions in place; CI is the read-only backstop for PR-button merges and bypassed local hooks. Full architecture, developer briefing, and troubleshooting Q&A: [Migration Version Gate](migration-version-gate.md).

## Troubleshooting

### Error: Field does not exist in model

```
odoo.tools.convert.ParseError: while parsing .../views/riverflow_service_views.xml:3
Error while validating view near:
...
Field "supply_journey_id" does not exist in model "riverflow.service"

View error context:
{'file': '.../views/riverflow_service_views.xml',
 'view.model': 'riverflow.service',
 'view.parent': ir.ui.view(1278,),
 'xmlid': 'view_service_form_crewradar'}
```

**Cause**: A field was removed from the model, but views in `ir_ui_view.arch_db` still reference it. The `arch_db` column stores the cached/combined view architecture.

**Fix**: Create a pre-migration script that deletes the stale views:

```python
# migrations/X.X.X/pre-migrate.py
import logging

_logger = logging.getLogger(__name__)

def migrate(cr, version):
    """Delete views that reference the removed field."""
    _logger.info("Pre-migration: Removing views with removed_field references")
    
    cr.execute("""
        DELETE FROM ir_ui_view
        WHERE arch_db::text LIKE '%removed_field_name%'
    """)
    
    deleted_count = cr.rowcount
    _logger.info(f"Deleted {deleted_count} view(s) - Odoo will recreate from XML")
```

Odoo will recreate the views from your XML files (which no longer have the field reference) during the module update.

### Error: can only parse strings

```
odoo.tools.convert.ParseError: while parsing .../views/riverflow_service_views.xml:3
Error while parsing or validating view:
can only parse strings

View error context:
'-no context-'
```

**Cause**: A pre-migration set `arch_db = NULL` for views, breaking view inheritance. When `arch_db` is NULL, it becomes `False` in Python, which lxml cannot parse.

**Fix**: Don't set `arch_db = NULL`. Delete the specific views instead (see above).

### Reproducing View Errors

These errors only appear when running module updates with `--stop-after-init`. When running the web server normally, view issues may be silently resolved and you won't see the error.

To reproduce view-related errors, use this command pattern:

```bash
~/odoo/venv/bin/python3 ~/odoo/odoo19/odoo-bin \
    --addons-path=~/odoo/odoo19/addons,~/odoo/enterprise19,~/oteny/radar \
    --without-demo=True \
    -u riverflow,rivercreds,crewradar,oteny_audit \
    -d crmain \
    -r thijsvanwaaij -w thijsvanwaaij \
    --http-interface 127.0.0.1 \
    --max-cron-threads 0 \
    --limit-time-real 0 \
    --log-level info \
    --stop-after-init \
    2>&1 | tee /tmp/odoo-update.log
```

Key flags:

- `--stop-after-init`: Required to see view validation errors (exits after update)
- `-u <modules>`: Update the modules being tested
- `--max-cron-threads 0`: Disable cron during testing
- `2>&1 | tee /tmp/odoo-update.log`: Capture output for analysis

## Module Boundary Rules

Modules form a strict dependency hierarchy. **A module's migrations and hooks must only reference models, fields, table columns, data records, and workflow names from itself or its declared dependencies — never from modules higher in the dependency tree.**

### Why This Matters

Odoo loads modules in dependency order. When module A's post-migration runs, module B (which depends on A) has not loaded its models yet. Columns added by B's `_inherit` extensions do not exist on the database table at that point. SQL referencing those columns will fail with `column does not exist`.

Even if the column happens to exist from a previous install, the migration still violates the dependency contract: it silently depends on another module being installed, making the base module non-standalone.

### Dependency Layers in This Workspace

```
Layer 0 (base)     riverflow, rivercreds, oteny_audit
Layer 1 (platform) crewradar (depends on riverflow + rivercreds)
Layer 2 (features) crewradar_sign, crewradar_creds, crewradar_wilma
Layer 3 (client)   crewradar_cuneus_sign
```

**Loading order**: Layer 0 → Layer 1 → Layer 2 → Layer 3. Migrations for each module run after that module's models are loaded but before higher-layer modules load.

### Rules

1. **SQL in migrations**: Only reference table columns that exist in the module's own models or its dependencies' models. Never reference columns added by `_inherit` in a higher-layer module.

2. **Data references**: Only reference workflow names, credential group codes, XML IDs, and other data records defined by the module or its dependencies.

3. **Prefer declarative cross-layer enrichment via XML**: Before reaching for a hook, check whether a `<data noupdate="0">` block in the higher-layer module's XML can express the update. This works when the downstream module needs to set fields on a record defined by an upstream module — the downstream module references the upstream record by its full `module.xml_id`. The block applies on both fresh install and `-u`, keeps data declarative, and avoids hook/migration pairs. Example: `crewradar_cuneus_sign/data/credential_type_data.xml` uses a `noupdate=0` section to set `credential_type_group_id` on `crewradar.auto_add_template_service_send_auv_information` (auto-add rule defined in the parent `crewradar` module).

4. **post_init_hook for cross-layer backfills**: When XML cannot express the enrichment (e.g. value depends on runtime data, or the update touches many dynamically-queried records), use the higher-layer module's `post_init_hook` (runs on first install) or its own migration (runs on upgrade). The `post_init_hook` runs after all models from all modules are loaded, so all columns and data are available.

5. **Safe no-op migrations**: If a migration version has already been created but the logic belongs elsewhere, convert it to a no-op with a comment pointing to the correct location. Don't delete the migration file — its version folder name is tracked in the database.

### Example: What Went Wrong

`rivercreds/migrations/19.0.3.4.0/post-migrate.py` originally contained:

```python
# Step 1: Set credential_type_group_id using workflow name 'Arrange A1 Declaration'
# VIOLATION: 'Arrange A1 Declaration' is a crewradar_cuneus_sign workflow name

# Step 2: JOIN rivercreds_plan_slot ON ps.log_entry_id = rs.log_entry_id
# VIOLATION: log_entry_id on plan_slot is added by crewradar_creds, not rivercreds
```

**On staging**: When `crewradar_creds` was freshly installed, `rivercreds` loaded first and its migration fired before `crewradar_creds` added `log_entry_id` to the table. Result: `ERROR: column ps.log_entry_id does not exist`.

**Fix**: Migration converted to no-op. Both backfills moved to `crewradar_creds/hooks.py` `post_init_hook`, which runs after all models are loaded and after plan items are created.

### Checklist for New Migrations

Before writing a migration, verify every SQL column and data reference:

- [ ] Could this be expressed as a `<data noupdate="0">` block in the module's XML instead? (Declarative, no migration needed)
- [ ] Every table column referenced is defined in this module or a declared dependency
- [ ] Every workflow name, XML ID, or data record referenced is defined in this module or a declared dependency
- [ ] No JOINs on columns added by `_inherit` extensions in higher-layer modules
- [ ] If backfill involves cross-module concepts, use the highest-layer module's hook/migration instead
- [ ] If converting to no-op, keep the migration file with a comment pointing to where the logic moved

## `post_init_hook` vs Migrations: Execution Timing

`post_init_hook` (declared in `__manifest__.py`) only runs on **first install** of a module, not on upgrades. Migrations (`migrations/X.X.X/post-migrate.py`) run on every upgrade to a new version.

**Implication for data backfills**: When a hook sets a field on a template and also backfills existing instances (e.g., setting `credential_type_group_id` on all WP services), the hook covers fresh installs. But on production databases where the module is already installed, the hook does not re-run on upgrade — a migration is needed to backfill existing data.

**Pattern**: Use both a hook (for fresh installs / test databases) and a migration (for existing production databases) when a backfill must cover all scenarios:

```python
# hooks.py — post_init_hook: covers fresh install
def _enrich_template(env):
    template.credential_type_group_id = group
    env.cr.execute("UPDATE ... SET credential_type_group_id = %s WHERE ... IS NULL", ...)

# migrations/X.X.X/post-migrate.py — covers existing databases
def migrate(cr, version):
    cr.execute("UPDATE ... SET credential_type_group_id = ... WHERE ... IS NULL")
```

**Common mistake**: Setting a field on a template (noupdate=1 record) via hook or migration, but not backfilling instances that were cloned from the template before the field was set. `_get_template_clone_vals()` only copies fields from template to instance at clone time — pre-existing instances retain their old (NULL) values.

## Pre-migration vs Post-migration

Odoo runs migration scripts at specific points during module upgrade:

| Script | Runs When | Use For |
|--------|-----------|---------|
| `pre-migrate.py` | Before model changes are applied | Cleaning up stale data, deleting views with removed fields |
| `post-migrate.py` | After model changes and XML data loaded | Data transformations, creating new records, recomputing fields |

### Migration Script Location

```
module_name/
├── migrations/
│   └── X.X.X/           # Version number from __manifest__.py
│       ├── pre-migrate.py
│       └── post-migrate.py
```

The version folder must match the version in `__manifest__.py` that introduces the breaking change.

### Basic Migration Script Structure

```python
"""Migration script description."""
import logging
from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)

def migrate(cr, version):
    """Migration entry point.
    
    Args:
        cr: Database cursor (raw psycopg2 cursor)
        version: Target version being migrated to
    """
    _logger.info(f"Running migration to {version}")
    
    # For pre-migrate: use raw SQL (no ORM available yet)
    cr.execute("UPDATE ... SET ...")
    
    # For post-migrate: ORM is available
    env = api.Environment(cr, SUPERUSER_ID, {})
    records = env['model.name'].search([])
    # ... do stuff with records
```

## View Cache Cleanup Pattern

When removing a field from a model, you may encounter the "Field does not exist" error during module upgrade.

### Why This Happens

1. Views are stored in `ir_ui_view` table with `arch_db` column containing the cached view XML
2. Inherited views combine parent + child into `arch_db`
3. When you remove a field from the model, existing cached views still reference it
4. During upgrade, Odoo validates views against the model and fails

### Solution: Delete Stale Views

```python
# pre-migrate.py
def migrate(cr, version):
    """Delete views referencing the removed supply_journey_id field."""
    _logger.info("Pre-migration: Removing views with supply_journey_id")
    
    cr.execute("""
        DELETE FROM ir_ui_view
        WHERE arch_db::text LIKE '%supply_journey_id%'
    """)
    
    deleted_count = cr.rowcount
    _logger.info(f"Deleted {deleted_count} view(s) - Odoo will recreate from XML")
```

### Anti-pattern: Don't NULL arch_db

**Never do this:**

```python
# BAD - breaks view inheritance!
cr.execute("""
    UPDATE ir_ui_view 
    SET arch_db = NULL
    WHERE model = 'riverflow.service'
""")
```

This breaks view inheritance because:

1. Parent views get `arch_db = NULL`
2. Child views try to inherit from parent views
3. Odoo can't parse `NULL` as XML, causing "can only parse strings" error

## Field Removal Checklist

When removing a field from a model:

1. **Remove from Python model** - Delete the field definition
2. **Remove from XML views** - Delete `<field name="removed_field"/>` from all view files
3. **Check for references** - Search codebase for field usage in compute methods, domains, etc.
4. **Create pre-migration** - Delete cached views that reference the field (see pattern above)
5. **Bump version** - Update `__manifest__.py` version to trigger migration
6. **Test locally** - Restore a production backup and run the upgrade

### Finding Views with Field References

Query to find affected views:

```sql
SELECT id, name, model 
FROM ir_ui_view 
WHERE arch_db::text LIKE '%field_name%';
```

## ORM vs Raw SQL in Migrations

### When to Use ORM (post-migrate)

Use ORM `unlink()` when deleting records that have:

- **Stored computed fields** that depend on the deleted record via `@api.depends`
- **Custom `unlink()` overrides** with cleanup logic (e.g., `_unlink_included_items()`)
- **`ondelete` hooks** registered via `@api.ondelete`

Raw SQL `DELETE` bypasses all of these. PostgreSQL's `ON DELETE SET NULL` or `ON DELETE CASCADE` runs at the database level, which does NOT trigger Odoo's ORM dependency tracking or `@api.depends` recomputation.

### Anti-pattern: Raw SQL DELETE with Computed Dependencies

**Never do this when computed fields depend on the deleted record:**

```python
# BAD - bypasses ORM, leaves computed fields stale!
cr.execute("DELETE FROM parent_model")
```

If `child_model.parent_id` has `ondelete="set null"`, PostgreSQL sets it to NULL. But if `child_model.is_linked` is a stored computed field with `@api.depends("parent_id")`, it will NOT recompute. The child record is left in an inconsistent state: `parent_id = NULL` but `is_linked = True`.

**Correct approach: use ORM unlink in post-migrate:**

```python
# GOOD - ORM fires @api.depends recomputation and custom unlink logic
env = api.Environment(cr, SUPERUSER_ID, {})
records = env["parent.model"].search([])
records.unlink()
```

### Example: Salary Timeline Deletion

The salary timeline model has `_unlink_included_items()` that releases linked services, timesheets, and vacation payouts. `riverflow.service.has_been_paid_on_salary` is a stored computed field depending on `salary_timeline_id`. Raw SQL DELETE would leave services marked as "paid" with no timeline linked. ORM `unlink()` triggers both the cleanup method and the field recomputation.

## Post-migration Patterns

### Calling Compute Methods

During post-migration, `registry.loaded` is `False`, so compute methods with this guard won't run. Use the `_impl` pattern:

```python
# In post-migrate.py
def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    
    # Call _impl directly to bypass registry.loaded check
    employees = env["hr.employee"].search([])
    employees._compute_check_results_impl()
```

See [Coding Patterns - registry.loaded Guard](coding-patterns.md#registryloaded-guard-and-migration-pattern) for more details.

### Recomputing a Stored Field After Its Formula Changes

Odoo recomputes a **stored** computed field on upgrade only when the field is **newly added** to the model, or when a dependency's stored value actually changes. Changing **only the compute body or the `@api.depends` list** does **not** re-trigger it — every existing row keeps the value the old formula stored.

**Symptom**: after an upgrade the field shows a stale value everywhere, but editing any one of its dependencies by hand (which forces that record through the compute) fixes that single record. That hand-edit-fixes-one-record behaviour is the tell that the stored column is stale and needs a one-time recompute.

**Fix**: a post-migration that force-recomputes by calling the compute method directly on the affected recordset, then flushing.

```python
def _recompute_the_field(env):
    records = env["model.name"].with_context(active_test=False).search([("<narrowing domain>")])
    if not records:
        return records
    records._compute_the_field()          # runs the CURRENT (final MRO) compute
    records.flush_recordset(["the_field"])
    return records

def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    _recompute_the_field(env)
```

Two things to verify first:

1. **No `registry.loaded` guard on the compute.** If the compute short-circuits when `registry.loaded is False` (see [Calling Compute Methods](#calling-compute-methods)), a direct call silently no-ops during a real upgrade — yet still works in an importlib test where the registry *is* loaded. Grep the whole override chain for `registry.loaded` / `_impl`; if guarded, call the `_impl` variant instead.
2. **Put the migration in the module that owns the FINAL override in the MRO.** Migrations run in dependency order, so a lower-layer module's migration runs before higher-layer `_inherit` overrides are loaded — recomputing there would apply the base formula, not the final one. Place it in the top-most module that overrides the compute so the full chain (and its fallbacks) runs.

**Real example** (`crewradar_creds/migrations/19.0.2.151`): commit `0b847a7f` repointed the stored `riverflow.service.project_deadline` from the window-clamped `renewal_marker_date` to the unclamped `renewal_action_date` (full case in the consuming business's own credential-planning docs). No field was added, so pre-existing `credential_renewal_marker` renewal services kept their old near-today deadlines. The migration sweeps `_compute_project_deadline` across all such services. It lives in `crewradar_creds` — not `rivercreds` where the field's base compute is — because `crewradar_creds` holds the final override (adding the contract-start onboarding fallback for permit-less initial services), which only runs when that module is loaded. Extract the sweep into a named helper (`_recompute_credential_renewal_deadlines(env)`) so a test can drive it (see [testing-guidelines.md — Testing a recompute migration](testing-guidelines.md#testing-a-recompute-migration-the-flush--sql-exception)).

### Creating Records

```python
def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    
    # Create records that should exist
    Transition = env["riverflow.transition"]
    existing = Transition.search([
        ("from_state_id", "=", state_a.id),
        ("to_state_id", "=", state_b.id),
    ])
    
    if not existing:
        Transition.create({
            "name": "New Transition",
            "from_state_id": state_a.id,
            "to_state_id": state_b.id,
        })
```

### Updating noupdate Records

XML records with `noupdate="1"` won't be updated during module upgrade. Use post-migration to update them:

```python
def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    
    # Update auto-add service domains (noupdate="1" in XML)
    auto_add = env.ref("module.auto_add_service_xmlid")
    new_domain = env.ref("module.new_domain_xmlid")
    auto_add.write({"domain_id": new_domain.id})
```

A post-migrate folder runs **once**. If that version already upgraded a served DB, do **not** edit the script. Bump the manifest and add a **new** folder. A one-shot `name` rewrite on a `noupdate` state must not skip `not rec.active`: an archived row never gets a second chance, and unarchiving later restores the old label.

### Removing noupdate Records (crons especially)

Deleting a record's XML from the data files does **not** remove it from
existing databases when its `ir.model.data` row is `noupdate=1`: the
module-data GC at the end of `-u` only deletes *non-noupdate* records that
disappeared from the files. A removed `ir.cron` therefore keeps firing on its
schedule and crashes once its target method is gone from the code.

A **pre-migrate** must delete explicitly: the record row, its
`ir_model_data` row, and — for `ir.cron`, which `_inherits`
`ir.actions.server` — the delegate server-action row as well:

```python
def migrate(cr, version):
    cr.execute(
        """SELECT c.id, c.ir_actions_server_id
           FROM ir_cron c
           JOIN ir_model_data d ON d.model = 'ir.cron' AND d.res_id = c.id
           WHERE d.module = 'my_module' AND d.name = 'my_cron_xmlid'"""
    )
    row = cr.fetchone()
    if row:
        cron_id, server_action_id = row
        cr.execute("DELETE FROM ir_cron WHERE id = %s", (cron_id,))
        cr.execute("DELETE FROM ir_act_server WHERE id = %s", (server_action_id,))
            "DELETE FROM ir_model_data WHERE module = 'my_module' AND name = 'my_cron_xmlid'"
```

Related: stale **views** referencing removed methods/fields also need
pre-migrate deletion because view validation runs during module load, before
any GC (see the stale-view section above). Worked example covering both:
`crewradar_creds/migrations/19.0.2.165/pre-migrate.py` (consistency-check
re-architecture).

### Hard-deleting seeded records that have dependents (credential-type cleanup pattern)

Reference implementation: **`rivercreds/tools/credential_type_cleanup.py`**, called from
`crewradar/migrations/19.0.9.170/post-migrate.py` (retired base credential types) and
`crewradar_cuneus_sign/migrations/19.0.5.49/post-migrate.py` (retired Cuneus types plus their
Wilma parsing articles). The helpers are generic — the calling module's migration decides
*which* codes/xmlids go — so reuse them (or copy their shape) instead of writing another
one-off delete loop. Exercised with synthetic data in
`rivercreds/tests/test_credential_type_cleanup_helper.py`.

Applies whenever seeded records have to disappear for good: the XML is `noupdate="1"`, so
deleting it from the data files does *not* remove the live rows (previous section), and the
model has no `active` field to fall back on, so retiring means hard-deleting.

The pattern has five parts:

**1. Unlink dependents before the target, archived ones included.** Required FKs are
`ON DELETE RESTRICT` (`rivercreds.credential.credential_type_id`), so the type row cannot go
until every credential pointing at it is gone — and archived credentials hold the FK just as
firmly as active ones, while the default `active_test=True` hides them from `search()`:

# active_test=False: archived dependents still hold the RESTRICT FK
Credential = env["rivercreds.credential"].with_context(active_test=False)
credentials = Credential.search([("credential_type_id", "in", cred_type.ids)])
credentials.unlink()
cred_type.unlink()

**2. Delete through the ORM, never raw SQL.** `unlink()` fires stored computes, chatter and
attachment cleanup, audit tombstones, `@api.ondelete` hooks — and it deletes the matching
**`ir.model.data` rows** (`odoo/orm/models.py::unlink`). That last part is what makes the
deletion stick: a SQL `DELETE` leaves the xmlid row behind pointing at a missing id, and
`_load_records` treats an xmlid whose record no longer exists as *to create*, so the record
comes back on the next `-u`. ORM unlink + removing the record from the XML files = gone for good.

**3. One savepoint per item, with an archive fallback.** An unexpected FK on a single code must
not roll back the whole upgrade, and an upgrade must never die on data nobody can predict:

try:
    with env.cr.savepoint():
except Exception:  # noqa: BLE001 - one code must not abort the rest
    # keep the type, archive its credentials, carry on
        credentials.exists().filtered("active").write({"active": False})

**4. Idempotent, search-then-act, with a logged summary.** Missing code/xmlid → recorded as
`skipped`, so a second `-u` is a no-op and a fresh install (where the XML records were never
created) finds nothing to do. Each helper returns a
`{"deleted": [...], "skipped": [...], "failed": [...]}` dict which the migration logs, so a
post-deploy log grep tells you exactly what happened per code.

**5. Delete User-Override children explicitly.** For `knowledge.article` parsing rules,
`delete_knowledge_articles()` unlinks the descendants (`child_of`, excluding self) *before* the
article itself. The "… — User Overrides" children that Teach-Wilma creates at runtime are
DB-only (no xmlid); left to the SQL cascade they would skip ORM `ondelete` hooks such as
`ai_knowledge`'s agent-source cleanup. Search with `active_test=False` here too — articles sent
to trash are archived, not deleted.

### Backfilling attachment FKs — prefer the chatter join over `res_model='target'`

When a new `Many2one('ir.attachment')` field needs a one-time backfill from production data, the **intuitive query** is to look for attachments at `res_model='<target_model>', res_id=<record_id>` — i.e. directly hung off the target record. **This is not enough** for any record that surfaces attachments via a wizard form.

Live data from the AUV Send Reminder 19.0.4.10 backfill on Cuneus production:

| Where AUV PDF attachments live | Count |
|---|---|
| `res_model='riverflow.service'` (Python auto-render path) | 303 |
| `res_model='riverflow.service.email.sender.wizard'`, `res_id=0` (HR uploaded via wizard form) | 370 |

The PDFs at `res_model='wizard', res_id=0` are JS-side uploads from the wizard form's attachment field. Odoo's `web/binary/upload_attachment` controller stamps `res_model` with the wizard's model and `res_id=0` (NewId) and never rewrites them, even after `message_post(attachment_ids=...)` posts the outgoing email. The 19.0.4.10 backfill missed ~22% of services because of this.

**The reliable source of truth is the chatter.** Both code paths end up at the same `message_post` call, so the attachment is always findable via `mail_message` + `message_attachment_rel`. The 19.0.4.11 fix:

```sql
WITH latest_outgoing_pdf AS (
    SELECT DISTINCT ON (m.res_id)
           m.res_id AS service_id,
           a.id     AS attachment_id
      FROM mail_message m
      JOIN res_users ru ON ru.partner_id = m.author_id   -- outgoing only
      JOIN message_attachment_rel ma ON ma.message_id = m.id
      JOIN ir_attachment a ON a.id = ma.attachment_id
     WHERE m.model = '<target_model>'
       AND m.message_type = 'email'
       AND a.mimetype = 'application/pdf'
       AND m.res_id IN (SELECT id FROM <target_table> WHERE <scope_filter>)
     ORDER BY m.res_id, m.date DESC, m.id DESC
)
UPDATE <target_table> tt
   SET <new_fk_field> = la.attachment_id
  FROM latest_outgoing_pdf la
 WHERE tt.id = la.service_id
   AND tt.<new_fk_field> IS NULL;
```

Key bits:

- **`JOIN res_users ru ON ru.partner_id = m.author_id`** — restricts to internal-user authors, so incoming gateway replies (where `author_id` is a plain `res.partner` matched from `email_from`) and anonymous gateway messages (`author_id IS NULL`) are excluded. Critical when the same chatter could carry both an outgoing original and an incoming reply with its own PDF.
- **`m.message_type = 'email'`** — filters out chatter notes and system messages.
- **`DISTINCT ON (m.res_id) ... ORDER BY m.res_id, m.date DESC, m.id DESC`** — Postgres idiom for "latest message per record". The Send + Regenerate case correctly picks the most recent rendering.
- **`WHERE <fk> IS NULL`** in both the CTE and the UPDATE — idempotent on re-run, doesn't trample a previously-set value.

Tested via importlib in `crewradar_cuneus_sign/tests/test_auv_email_wizard.py` (10 backfill tests across two migration generations), verified on the `crmain` restored-live database before deploy.
