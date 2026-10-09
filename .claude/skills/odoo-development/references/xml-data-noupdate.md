# XML data, `ir.model.data.noupdate`, and module upgrades

Reference for why a second XML file or a `<data noupdate="0">` block often **does not** add new fields to existing rows when the record was first created under `noupdate="1"`.

## Root mechanism (Odoo 19)

Two layers apply on module update (`-u`):

1. **XML loader** (`odoo/tools/convert.py`, `_tag_record`): For a `<record>` under a `noupdate` context, if the xml id **already exists** and the mode is **not** init, the loader may **skip** the entire node (no write).
2. **ORM** (`odoo/orm/models.py`, `_load_records`): For an existing xml id, a write runs only when `not (update and d_noupdate)`. Here **`d_noupdate` comes from the `ir.model.data` row in the database** for that xml id — it is not overridden by putting the same id in a later file with `noupdate="0"`.

**Manifest order does not change `d_noupdate`.** Reordering `__manifest__.py` data files does not unlock updates for existing databases.

## Fresh install vs upgrade

| Phase | Behaviour |
|-------|-----------|
| **Initial install** | Loader mode is init; `_load_records` gets `update=False`, so `not (update and d_noupdate)` allows writes even after the `ir.model.data` row exists with `noupdate=True`. A second XML fragment for the **same** xml id can still merge fields if load order creates the base record first. |
| **Upgrade** (`-u`) | `update=True`. If `d_noupdate` is true for that xml id, **new fields declared only in XML do not reach existing rows** via the normal data path. |

## Row `noupdate` is stamped at creation — check the ROW, not the file

`ir.model.data.noupdate` is written when the record is **first created** and is never re-synced from the XML file afterwards. A file that is *not* `noupdate` today can therefore still have rows with `noupdate=true` — e.g. the record was first loaded while the file (or an enclosing `<data>` block) was `noupdate="1"`, or an early draft of the file differed. Since `_load_records` consults the **row's** `d_noupdate`, `-u` then silently skips a record even though the current file says it should update. The reverse also holds: a row with `noupdate=false` updates fine on `-u` even when sibling records in the same module need a migration.

**Before trusting `-u` to land a changed field value, check the row on the target database:**

```sql
SELECT module, name, noupdate FROM ir_model_data
WHERE module = 'crewradar' AND name = 'workflow_tft_contract';
```

Verified cases from the 2026-08-10 workflow icon audit (checked on crmain and cr-test): `crewradar.workflow_tft_contract` (`service_workflow_tft.xml`) and `crewradar_cuneus_sign.workflow_auv_contract` (`auv_workflow.xml`) — neither file is `noupdate` today, but both rows carry `noupdate=true` from their creation context, so their icon changes needed a post-migrate. Conversely `crewradar.log_entry_workflow_to_be_planned`'s records (Needs Planning / Needs Crew) have `noupdate=false` file AND rows, so their fix needed no migration.

## A new record in a `noupdate="1"` file is created on update

The skip applies to an XML id that **exists**. For a `<record>` whose id is not yet
in `ir.model.data`, `_tag_record` falls through to a normal create on `-u`
(`forcecreate` defaults to true; `odoo/tools/convert.py`, the `noupdate` branch).
So a new transition, state or rule added to a `noupdate` workflow file reaches
existing databases without a migration. Only new **fields** and changed
**values** on existing records need a post-migrate. Proven by radar crewradar
19.0.11.17 (`transition_site_revert_to_prospect` in the noupdate
`site_workflow_lifecycle.xml`, created on crmain by `-u crewradar`).

## Best practice for new fields on `noupdate="1"` records

1. **Canonical XML**: Add the field on the **same** `<record id="…">` in the original data file so **new databases** get a complete row from one definition.
2. **Post-migrate**: Implement `migrations/<version>/post-migrate.py` (SQL with idempotent guards such as `WHERE field IS NULL`, or ORM) so **existing** production and restored databases receive the value.

Avoid duplicate definitions (same field on the canonical record **and** a second “patch” file) unless transitioning — it is redundant on fresh install.

## Changing an existing field value on a `noupdate` record

The best practice above covers **new** fields (idempotent guards like `WHERE field IS NULL`). Changing a field that already **holds** a shipped value needs a different guard: the post-migrate updates only when the row still carries the originally shipped value, so a database-specific manual override survives the migration:

```python
def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    workflow = env.ref("crewradar.employee_workflow_lifecycle", raise_if_not_found=False)
    if workflow and workflow.icon == "fa-id-card":  # the shipped value — a manual override skips the update
        workflow.icon = "fa-user"
```

Pair the migration with the same change on the canonical `<record>` so fresh installs get the new value from XML directly. The guard may accept a tuple of old values when interim picks reached test databases (`workflow.icon in ("fa-file-text", "fa-clock-o")`). Canonical examples: the 2026-08-10 workflow icon migrations — `crewradar/migrations/19.0.9.174/post-migrate.py`, `crewradar_cuneus_sign/migrations/19.0.5.53` + `19.0.5.54`, `riverflow/migrations/19.0.1.1198/post-migrate.py`.

## What does not fix upgrades

- A **separate** XML file loaded later with `<data noupdate="0">` **alone** — the stored `ir.model.data.noupdate` on that xml id still blocks `_load_records` when `update and d_noupdate`.
- **Inlining** only in a new file without post-migrate — existing DBs still need the migration path.

Comments in XML that claim `noupdate="0"` on a follow-up file “forces the link on upgrade despite the type living in `noupdate="1"` data” are **misleading** relative to `_load_records`; prefer accurate comments or this reference.

## Re-importing a whole `noupdate` file: only `mode="init"` works

When a migration has to push a **large** body of new content onto existing rows — a rewritten mail template body, several renamed records in one file — writing the values by hand duplicates the data between the XML and the script and rots. Re-import the file instead. There is exactly one call shape that works:

```python
from odoo.tools.convert import convert_file

convert_file(env, "my_module", "data/my_file.xml", {}, mode="init", noupdate=False)
```

**`mode="init"` is load-bearing and is not interchangeable with the usual `"update"`.** `xml_import` skips every record in a file whose root is `<odoo noupdate="1">` unless the mode is exactly `init` — `odoo/tools/convert.py`:

```python
if self.noupdate and self.mode != 'init':
    return
```

**Passing `noupdate=False` alone does nothing**, because the file's own root attribute overrides the argument. The failure is silent in the worst way: the migration runs, logs whatever success line you wrote, and changes not one row. `crewradar_creds/migrations/19.0.2.208` lost a debugging round to exactly this.

So: **verify the row in the database** after such a migration rather than trusting the log line. `riverflow.workflow.reset_workflow_to_xml()` is the in-repo precedent — it builds an `xml_import` with `mode="init"`, `noupdate=False` for the same reason, and calls `_tag_record` on selected elements only when it wants to avoid touching the rest of the file.

Re-importing the whole file resets **every** record in it to its XML values, which is usually what you want but is worth stating in the migration docstring. It also creates nothing it should not: `_load_records` matches on xml id, so existing rows are updated in place.

## Policy alternatives (explicit trade-offs)

- Set **`noupdate="0"`** on the whole data file: every `-u` reapplies all declared fields — good for automation, weaker protection of database-specific tweaks.
- **One-time migration** setting `ir.model.data.noupdate = false` for chosen xml ids, then maintaining those fields in `noupdate="0"` XML — future upgrades apply from XML without SQL.

## Cross-module xml ids

Updating a record whose external id is owned by another module (e.g. patching `crewradar.*` types from a dependent module) is subject to the same **`d_noupdate`** rule. Use **post-migrate**, or **`post_init_hook`** only for cross-module enrichment that XML cannot express — not for one-off production backfills (those belong in migrations). See [Migrations — Module boundary rules](migrations.md#module-boundary-rules).

The trap that bites hardest is a **foreign record parked inside your own `noupdate="1"` file**: e.g. a `<record id="hr.group_hr_user">` adding `Command.link(...)` to `implied_ids`, written into a data file whose top-level element is `<odoo noupdate="1">`. It works on a fresh database and silently does nothing everywhere else, because `hr`'s own `ir.model.data` row for that group already exists with the update path gated. Keep every enrichment of a **foreign** record in a separate file with **no** `noupdate` (`crewradar_cuneus_sign/data/barney_groups.xml` is the worked example) — the file's own records are then re-applied on every `-u`, which is exactly what a group implication wants.

**Group implications need a clone-cache flush when testing.** The parallel test runner fingerprints schema + `ir_model_data`, and an `implied_ids` change touches neither — worker clones stay stale and the new implication tests fail while `cr-test` itself is correct. See [Parallel test runner](parallel-test-runner.md).
