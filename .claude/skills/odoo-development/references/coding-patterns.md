# Odoo 19 Coding Patterns

Detailed patterns and anti-patterns for Odoo 19 development. Based on official [Odoo Coding Guidelines](https://www.odoo.com/documentation/19.0/contributing/development/coding_guidelines.html).

## Python Idioms

### Dictionary Operations

```python
# Creation with values (good)
my_dict = {'foo': 3, 'bar': 4}

# Update dict (good)
my_dict.update(foo=3, bar=4, baz=5)
my_dict = dict(my_dict, **my_dict2)

# Avoid sequential assignments
my_dict['foo'] = 3  # ok for single
my_dict['bar'] = 4  # avoid multiple like this
my_dict['baz'] = 5
```

### Collections and Iteration

```python
# Collections are booleans - use directly
if some_collection:  # good
    ...
if len(some_collection):  # avoid
    ...

# Iterate on dict directly
for key in my_dict:  # good
    ...
for key in my_dict.keys():  # avoid (creates temp list)
    ...

# Access key-value pairs
for key, value in my_dict.items():
    ...

# Use dict.setdefault
values = {}
for element in iterable:
    values.setdefault(element, []).append(other_value)
```

### List Comprehensions

```python
# Use list comprehensions (good)
cube = [(i['id'], i['name']) for i in res]

# Avoid manual appending
cube = []
for i in res:
    cube.append((i['id'], i['name']))  # avoid
```

### Know Your Builtins

```python
# dict.get() returns None by default
value = my_dict.get('key')  # good
value = my_dict.get('key', None)  # redundant

# Different meanings - choose correctly
if 'key' in my_dict:  # checks key existence
if my_dict.get('key'):  # checks key exists AND value is truthy
```

### Multiple Return Points

Multiple returns are OK when they make code simpler:

```python
# Good: clear early return
def axes(self, axis):
    if isinstance(axis, list):
        return list(axis)
    return [axis]
```

## ORM Patterns

### Property Assignment vs write()

Prefer direct property assignment for single field updates:

```python
# Good: Direct assignment
order.partner_id = new_partner
order.state = 'confirmed'

# Avoid: write() for single fields
order.write({'partner_id': new_partner.id})
order.write({'state': 'confirmed'})
```

Use `write()` only when updating multiple fields atomically or when working with recordsets.

### Avoiding Database Flushes

The ORM handles caching and flushing automatically. Avoid patterns that require explicit flushes:

```python
# Good: Navigate through recordset relations
employee = self.env['hr.employee'].browse(employee_id)
entries = employee.log_entry_ids.filtered(lambda e: e.active and e.work_status == 'work')

# Avoid: search() when you already have the relation
employee = self.env['hr.employee'].browse(employee_id)
entries = self.env['crewradar.log.entry'].search([
    ('employee_id', '=', employee.id),
    ('work_status', '=', 'work'),
])  # This may miss unflushed records
```

### Using filtered() and sorted()

Navigate recordsets without additional database queries:

```python
# Filter active entries with specific status
work_entries = employee.log_entry_ids.filtered(
    lambda e: e.active and e.work_status == 'work'
)

# Sort by date
sorted_entries = entries.sorted('start_date')

# Reverse sort
recent_first = entries.sorted('start_date', reverse=True)

# Multiple sort keys
sorted_entries = entries.sorted(lambda e: (e.start_date, e.employee_id.name))
```

## Compute Method Patterns

### registry.loaded Guard and Migration Pattern

Compute methods often have a `registry.loaded` guard to prevent execution during module loading:

```python
def _compute_check_results(self):
    if not self.env.registry.loaded:
        # don't recalc on startup
        return

    if any(isinstance(record.id, api.NewId) for record in self):
        return

    self._compute_check_results_impl()

def _compute_check_results_impl(self):
    # Actual computation logic here
    ...
```

**Problem**: During migrations, `registry.loaded` is `False`, so calling compute methods from post-migrate scripts does nothing.

**Solution**: Split into wrapper + `_impl` pattern:

- **Wrapper method**: Contains the `registry.loaded` and `NewId` guards
- **`_impl` method**: Contains the actual computation logic

Migrations can then call `_impl()` directly to bypass the guards:

```python
# In post-migrate.py
def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    
    # Call _impl directly to bypass registry.loaded check
    employees = env["hr.employee"].search([])
    employees._compute_check_results_impl()
```

This pattern is used in `crewradar.log.entry._compute_check_result_ids()` and `hr.employee._compute_check_results()`.

For more migration patterns including pre-migration scripts and view cache cleanup, see [Migrations](migrations.md).

### _register_hook() for Post-Upgrade Actions

To run code automatically after every module upgrade (not on plain restarts), use `_register_hook()` on `ir.module.module`. This hook is called once per registry load (STEP 9 in `odoo/modules/loading.py`). Check `registry.updated_modules` to gate execution:

```python
class IrModuleModule(models.Model):
    _inherit = "ir.module.module"

    def _register_hook(self):
        super()._register_hook()
        # Only run when our module was actually installed/upgraded
        if "crewradar" not in self.env.registry.updated_modules:
            return
        # Post-upgrade logic here (has env access)
        self.env["crewradar.skill.sync"].sync_skills_to_knowledge()
```

This follows the same pattern as Odoo's `account` module (`addons/account/models/ir_module.py`). Used in crewradar for automatic skill-to-Knowledge sync on every upgrade.

The method must return a marshalable dict (`{"articles_synced": N}`), never `None` — Odoo 19 XML-RPC dumps with `allow_none=False`. Full history and the manual-call recipe: [oteny-knowledge-sync — Manual admin call](../../oteny-knowledge-sync/SKILL.md#manual-admin-call-xml-rpc).

### @api.depends Declaration

Always declare all dependencies. The ORM uses this for automatic recomputation:

```python
@api.depends('line_ids', 'line_ids.amount', 'discount_percent')
def _compute_total(self):
    for record in self:
        subtotal = sum(record.line_ids.mapped('amount'))
        record.total = subtotal * (1 - record.discount_percent / 100)
```

### Stored Computed Fields

Use `store=True` when the computed value needs to be:

- Searched/filtered on
- Used in reports
- Persisted for performance

```python
total = fields.Float(
    compute='_compute_total',
    store=True,  # Stored in database
)
```

### M2M Cascade Bypasses the Dependency Tracker

When a record is deleted, the SQL `ON DELETE CASCADE` on Many2many junction tables removes the relation rows directly at the database level. **Odoo's `@api.depends` notification machinery is not invoked**, so stored computed fields on the *inverse* side that depend on the M2M relation do **not** recompute and silently go stale.

Symptoms:

- `record.related_m2m_ids` correctly reads as empty after the parent is deleted (junction is cleared).
- But a stored compute like `is_active = bool(related_m2m_ids)` keeps the pre-deletion value in the database column.
- Search filters and code paths that read the stored boolean diverge from reality.

The cascade is already on by default — it's how Odoo creates the M2M FKs. The notification gap is the issue, not the cascade itself.

**Pattern:** Override `unlink()` on the side being deleted to capture the inverse records first, then call `modified()` on the M2M field name from the inverse side after `super().unlink()`:

```python
def unlink(self):
    # Capture inverse records before deletion. SQL ON DELETE CASCADE clears
    # the junction tables without notifying Odoo's dependency tracker, so
    # stored computes on the inverse side that depend on the M2M relation
    # go stale. modified() walks the @api.depends graph and triggers the
    # recomputation against the now-empty relation.
    inverse_a = self.inverse_a_ids
    inverse_b = self.inverse_b_ids
    result = super().unlink()
    inverse_a.modified(["my_m2m_field"])  # field name from inverse_a's side
    inverse_b.modified(["my_m2m_field"])  # field name from inverse_b's side
    return result
```

Notes:

- The field name passed to `modified()` is the field on the **inverse** model that points back at the deleted model (i.e., the same field used in the dependent compute's `@api.depends("that_field")`).
- `modified()` propagates through transitive `@api.depends` chains — e.g., a timeline whose `is_exported` depends on `salary_ids.export_file_ids` will recompute when you call `salaries.modified(["export_file_ids"])`.
- The new value lands in the ORM cache immediately and is flushed to the database on the next commit (Odoo's usual lazy flush). Don't add raw SQL assertions in tests immediately after the unlink — read via the ORM instead.
- The same notification gap exists for One2many inverses when the *one* side is deleted with cascade; the same `modified()` pattern applies.

**Reference implementation:** `crewradar.salary.export.file.unlink()` notifies `medical_expense_ids` and `salary_ids` so the stored `is_exported` compute on `crewradar.medical.expense` and `crewradar.salary.timeline` resets to False.

### `create=True` skips every many2one hop

`BaseModel.modified(..., create=True)` walks `_modified_triggers`. Any tree
item whose field type is `many2one` or `many2one_reference` is skipped:
Odoo assumes no other record can already point at a row that is being
created. That assumption fails when the dependent field sits behind a
**computed stored Many2one with no inverse One2many**.

`riverflow.state.record.service_id` is that shape. A credential created
with its `number` already in vals never marks
`riverflow.state.record.credential_search_terms`. `flush_all` and commit
do not fill a field that was never put in `tocompute`. The next HTTP
Name search reads the empty SQL column. A later `read()` of the service
field can still look correct, because `__get__` runs a pending service
compute. `search()` on the mirror does not: it flushes only the domain
field and does not `flush_all()`.

The hole is common. About 22 stored Radar mirrors depend on
`service_id.*`. Those stay correct because staff **write** the source
on an existing service. A credential is **created** with `number`
already set (File wizard `_create_credential_without_attachments`).
Radar Name search is the only product `search()` of a hop-computed
stored column, so only `_mark_credential_search_term_mirrors` closes
that hop. Services / Supply Orders can fill via the service
`credential_ids` One2many. A Concept write on an existing service
already notifies. The mark was kept.

Assigning a stored compute also does not call `modified()`. A Radar
mirror that `@api.depends` the service stored field therefore stays
unmarked when that field is assigned inside a compute. Depend on the
source fields instead (`service_id.credential_ids.number` /
`service_id.mfnl_concept_number`) and read the service concat so a
to-compute / cache-miss runs the service compute.

A later module cannot fill a stored column that `_auto_init` already
created. `load_module_graph` imports one module, then calls
`init_models`. rivercreds creates `credential_search_terms` and
backfills it before `crewradar_cuneus_sign` is imported. An override on
that field never runs on the first `-u`. Put the Concept concat on a
Cuneus-owned stored field (`mfnl_search_terms`) so the column is created
when Cuneus inits. Do not add a migrate that rewrites the rivercreds
column. Do not put Concept back on that override.

**Pattern:** from a real write path that `create=True` cannot skip, call
`env.add_to_compute` on the dependent stored field. Use a stored Many2one
that already points at the row (`state_record_id` on the service), not a
`search()` on the computed Many2one. Do not paper the test with
`flush_all()`. Do not switch the mirror to `related=` to dodge the
notification gap.

**Reference implementation:**
`riverflow.service._mark_credential_search_term_mirrors()` in rivercreds,
called from `rivercreds.credential` create/write/unlink. Cuneus extends
that mark for `mfnl_search_terms`. Decision
record lives with the consuming business's own credential module.

### Disabling Group Aggregation

Use `aggregator=None` on the field definition to disable automatic sum/avg in grouped list views. Since Odoo 18, `aggregator` replaces the deprecated `group_operator`:

```python
# Disable aggregation - no sum shown in grouped views
quantity = fields.Float(
    "Quantity",
    aggregator=None,
)

# Custom aggregation function (default for Float is "sum")
unit_price = fields.Float(
    "Unit Price",
    aggregator="avg",  # Use average instead of sum
)
```

**Important**: `aggregator` is a **field definition** attribute, not an XML view attribute. It affects all views that display the field in grouped mode.

### Editable Computed One2many (Wizard Pattern)

When a wizard has a computed One2many that users need to partially edit (e.g., toggle checkboxes),
use `readonly=False` on the model field and `force_save="1"` on readonly child fields in the view.

**Problem**: Computed stored One2many fields are readonly by default. Adding `readonly="0"` in the
view XML only affects rendering — the ORM still re-runs the compute on save, overwriting user edits.
Additionally, the web client silently drops field values that are not visible in the view or are
marked readonly, causing data loss on round-trip saves.

**Solution**: Four complementary techniques are needed for robust wizard lists:

```python
# Model: readonly=False tells the ORM to accept user writes
# without triggering recomputation (compute only runs when
# actual dependencies change).
# store=True is essential — without it, inverted compute values
# entered by the user are lost on the next server round-trip.
exporter_line_ids = fields.One2many(
    "my.wizard.line",
    "wizard_id",
    compute="_compute_lines",
    store=True,
    readonly=False,  # Allows user edits to persist
)
```

```xml
<!-- View: force_save="1" on readonly child fields ensures the web client
     includes them in the save payload. Without this, the web client strips
     readonly fields from the data, causing NOT NULL constraint violations.

     Hidden fields (invisible="1" + force_save="1") preserve values that
     the user doesn't need to see but must survive the client-server
     round-trip (e.g., IDs, computed references, intermediate state).

     editable="bottom" enables inline editing with new rows appended at
     the bottom. Omit editable to open a popup form for editing instead. -->
<field name="line_ids">
    <list create="0" delete="0" editable="bottom">
        <field name="selected" />
        <field name="name" readonly="1" force_save="1" />
        <field name="count" readonly="1" force_save="1" />
        <field name="internal_ref" invisible="1" force_save="1" />
    </list>
</field>
```

**Why all four techniques are needed**:

- `store=True` on model: Ensures inverted compute values entered by the user are persisted and not lost when the wizard re-renders
- `readonly=False` on model: Prevents ORM from re-running the compute when users edit the field (preserves user changes across saves)
- `force_save="1"` on view: Prevents web client from stripping readonly child field values from the save payload (avoids null constraint errors). Also needed on `invisible="1"` fields
- Hidden fields (`invisible="1"` + `force_save="1"`) in view: Ensures values that are not displayed are still included in the client recordset and posted back to the server on save

Neither `readonly=False` nor `force_save` works alone — `readonly=False` without `force_save` causes null errors on save;
`force_save` without `readonly=False` causes the compute to overwrite user edits.

**Inline vs popup editing**: Use `editable="bottom"` on `<list>` to allow adding and editing lines inline. Omit the `editable` attribute to have each line open in a popup form instead — useful when lines have many fields or complex sub-forms.

### `default_get` Must Be Side-Effect-Free (Idempotent or Deferred)

The web client may invoke a wizard's `default_get` **several times in a single open** — observed as 8 record-creates in one transaction on one wizard open (the onchange protocol re-evaluates defaults). So any code that **creates a persistent record inside `default_get`** (or a method it calls) multiplies that record per open, leaving orphans.

**Rules**:

- Prefer to defer persistent side effects to the action method (the button handler), not `default_get`.
- If a record must exist at open time (e.g. to preview an `ir.attachment` in the form — see the round-trip note below), make the create **idempotent**: search for an existing matching record (e.g. by content `checksum` + `create_uid`) and reuse it instead of creating a new one. Within one open, the later `default_get` passes find the first pass's record (writes are flushed before a `search`), so exactly one results.
- Do not bind such a draft to the *target business record* until the action fires — bind it to the wizard model (`res_model=self._name`, `res_id=0`) so an abandoned wizard leaves nothing on the business record; rebind on the action.

Real example: `crewradar_sign` email sender "Generate-and-Email" — `_get_or_create_draft_attachment` (checksum-idempotent, wizard-bound `res_id=0`) on open, rebound to the service in `_send_email`. Pre-fix it created a service-bound attachment in `default_get` and left 8 orphan PDFs per open.

### Per-User Memory for a Non-Stored View Toggle

A `store=False` Boolean in a form (a filter toggle above a list) belongs to the person looking at the screen, not to the record. It therefore has no home, and every user meets the same starting value on every record they open. To remember it per user:

- **Store it on `res.users.settings`** — Odoo's own per-user preference model (`base`), one row per user, created on demand by `_find_or_create_for_user`, with a record rule that limits a user to their own row. Prefix the field with the module name; it is a shared core model.
- **Read it in the field's `default`**, through a small `@api.model` helper, and **read only** — `self.env.user.sudo().res_users_settings_ids[:1]` and fall back to the shipped value when there is no row. `default_get` runs on every form open, so creating a row there would violate the side-effect rule above.
- **Write it in `@api.onchange`** on the toggle. `onchange` runs only when a person works the form, so a click is stored while code that creates records never touches anybody's preference. Guard on "value differs from the stored preference": the client also replays onchange when a form opens on a new record, and the no-op must stay free (and must not create a row).
- **Do not use `ir.default`** for a value that changes on a mouse click: `ir.default.create/write` calls `self.env.registry.clear_cache()`, flushing the whole registry cache instance-wide on every toggle.
- A new Boolean column with a Python `default` needs **no migration** — Odoo's `_init_column` fills existing rows on `-u`.
- **Pick a widget that runs the onchange.** `boolean_toggle` autosaves by default: it calls `record.update(changes, {save: true})`, which Odoo turns into `withoutOnchange: true` plus an immediate save, so the `@api.onchange` never runs and the preference is never stored (the list still refreshes, because the save re-reads the compute, which is why nobody noticed). `selection_badge` and `radio` call `record.update()` without save, so the onchange runs. Found on 2026-09-14 after the toggle version had been live for four days with nothing stored.

Real example (historical, crewradar 19.0.10.67 to 19.0.10.68; the control was retired in 19.0.10.69 in favour of oteny_shortcut Forms shortcuts, which keep no per-user memory at all — the pattern below stays valid for a preference that must be remembered): the Services tab **Show** control (`crewradar.service.filter.mixin.service_filter`, a required non-stored Selection with the `selection_badge` widget; first shipped as a `boolean_toggle` in 19.0.10.57, where the memory never fired) — documented in full in the consuming business's own skill bundle, § Services Tab Filters. Since 19.0.10.68 the control uses `crewradar_service_filter`, a subclass of the badge widget: `record.update()` also marks the record dirty, and a view preference must not show the save / discard buttons, so on a clean record the widget stores the choice with an ORM call (`set_service_filter`) and calls `record.load()`; the non-stored field re-reads its default, which reads the stored choice, and the record stays clean. On a dirty or new record it keeps `record.update()`, because a reload discards unsaved edits.

### A dotted `@api.depends` cannot cross a non-stored, search-based One2many

`@api.depends("service_ids.deadline")` only works when Odoo can find, from a changed `riverflow.service`, which parent records hold it in `service_ids`. It does that through the One2many's inverse field, or, when there is none, by `search([("service_ids", "in", ids)])` on the parent model. A `compute=` One2many without `inverse_name` and without `search=` has neither: `fields.py` warns at load time that the field "should be searchable", and the runtime search raises `Cannot convert ... to SQL because it is not stored`. The failure does not show in a test that only reads the compute; it shows the first time a service is created or written while a parent form is in cache.

- **Declare only the One2many itself** in that case, and say so in a comment beside the decorator. A non-stored compute is recomputed on every fresh read anyway, so the undeclared sub-fields only affect the same-transaction cache.
- **If the dependency is really needed**, give the parent a stored One2many with a real `inverse_name` as the dependency handle (the pattern of `hr.employee.service_dependency_ids`), or add a `search=` method to the One2many.

Real example (historical, crewradar 19.0.10.67 to 19.0.10.68; the mixin was removed in 19.0.10.69): `crewradar.service.filter.mixin._compute_filtered_service_ids` read `deadline` and `is_end_state` of each service but depended on `service_ids` only, because on `hr.employee` and `crewradar.site` that field is a non-stored One2many with neither `inverse_name` nor `search=` (crewradar 19.0.10.67, documented in the consuming business's own skill bundle, § Services Tab Filters). On `crewradar.log.entry` the same field is stored with `inverse_name="log_entry_id"`, so the limit is per model, and a mixin shared by several models must follow the weakest one.

### Transient Binary Values Do Not Survive the Save Round-Trip

A value placed in a `fields.Binary` on a **transient/new** wizard record (e.g. set in `default_get`) is **not sent back to the server** when the user triggers the action that saves the wizard — so reading it back in the action method yields empty. Two consequences:

- **Don't stash bytes in a transient Binary to read at action time.** Either re-derive the bytes in the action, or persist them as an `ir.attachment` and carry the **id** — `Many2one`/`Many2many` of ids *do* survive the round-trip (this is why wizard attachment flows pass `attachment_ids`, not bytes).
- A `widget="binary"` on an **unsaved** record can't render a download link (it builds `/web/content/<model>/<id>/<field>` and there is no real id yet). To show a downloadable document in an open-but-unsaved wizard, use a real `ir.attachment` listed in a `Many2many` (the `many2many_binary` widget downloads by attachment id), not an inline Binary.

### `ir.attachment` Re-encodes Images (checksum ≠ sha1 of the source)

Odoo re-encodes image attachments on store (its image-processing path re-saves the bitmap), so an attachment's `checksum` is the sha1 of the **transformed** bytes, not of the bytes you passed in — a 271 KB PNG comes back ~290 KB with a different sha1. **Don't dedupe image attachments by comparing your own `sha1(file_bytes)` to `ir.attachment.checksum`** — it never matches, so you create a fresh attachment on every run. Key on a marker you control instead (e.g. store `sha1(source)` in `description` and search that), and `sudo()` the search so `res_id=0` / access-restricted attachments aren't hidden. Real example: the Knowledge skill-sync image embedding (`SkillSyncService._embed_body_images`, § Duplicate image attachments in that module's own skill bundle).

### One2many Line-to-Line Cascade (Onchange Gotcha)

When one line in an editable One2many needs to update **sibling lines** (e.g., a parent checkbox selecting all children), placing `@api.onchange('field')` on the **line model** does NOT work in the browser. The web client only returns changes to `self` (the edited line); modifications to other lines in the same One2many are silently discarded. Tests pass because `_onchange_*()` is called as a plain Python method in-process.

**Wrong** — line-level onchange modifying siblings (works in tests, breaks in browser):

```python
class WizardLine(models.TransientModel):
    @api.onchange("selected")
    def _onchange_selected(self):
        # These writes to sibling records are LOST in the browser
        siblings = self.wizard_id.line_ids.filtered(lambda l: l.id != self.id)
        for sibling in siblings:
            sibling.selected = self.selected
```

**Correct** — wizard-level onchange modifying all lines (returned to browser):

```python
class Wizard(models.TransientModel):
    line_ids = fields.One2many(...)

    @api.onchange("line_ids")
    def _onchange_line_ids(self):
        # Modifications to ALL lines are returned to the browser
        for line in self.line_ids:
            if line.some_condition:
                line.some_field = new_value
```

For change detection (distinguishing which line was edited), add a tracking field (e.g., `previous_state`) on the line model. Include it in the view as `invisible="1" force_save="1"` so the web client round-trips it. See `crewradar_sign.document.wizard` (`parent_was_selected` field) for a working example of two-way parent/child checkbox cascade.

### One2many Computed Fields for Side Effects

When you need to create/update multiple records as a side effect of changes:

```python
generated_entry_ids = fields.One2many(
    'crewradar.log.entry',
    'generated_by_employee_id',
    compute='_compute_generated_entries',
    store=True,  # Required for One2many computed fields
)

@api.depends('contract_end_date', 'ignore_for_planning')
def _compute_generated_entries(self):
    for employee in self:
        if employee.ignore_for_planning:
            continue
        # Create/update entries based on contract changes
        ...
```

### Avoid write() and create() Overrides

Instead of overriding `write()` or `create()`, use computed fields:

```python
# Avoid: Overriding write
def write(self, vals):
    res = super().write(vals)
    if 'state' in vals:
        self._update_related_records()  # Side effect
    return res

# Better: Computed field triggered by dependency
related_state = fields.Selection(
    compute='_compute_related_state',
    store=True,
)

@api.depends('state')
def _compute_related_state(self):
    for record in self:
        record.related_state = self._determine_related_state()
```

### Stored FK computes: guard the assignment, never the reader

When a stored Many2one (`store=True`, usually `ondelete="cascade"`) is
assigned from `res_id` / a related browse, a missing comodel row is **not**
an empty subject. `browse(missing_id)` is truthy; reading a field on it
raises `MissingError`.

The `.exists()` belongs on the compute that **assigns** the stored FK, and
only there. `_compute_log_entry_id` on `riverflow.service` resolves
`res_id` through one batched `exists()`, so a missing row becomes
`log_entry_id = False` and a dangling id never reaches the database.

Do **not** move that guard downstream into a reader such as
`_compute_employee_id_site_id`. A guard there would let the assignment
write the dangling id anyway, and flush would then raise
`ForeignKeyViolation` — a worse error, at a later moment, further from the
cause. A reader that walks an already-guarded FK needs no guard of its own.

A plain `res_id` with no FK behind it (the `hr.employee` and
`crewradar.site` subjects) has no cascade to protect it, so the deleted
record really does leave a live row pointing nowhere. There the reader
guards, and there the miss is genuinely tolerated.

A **search** path tolerates a miss for a different reason and needs no
guard at all (see `_find_plan_item_for_clone`): a search returns what
exists, so it can only come back empty.

Tests should still use a real `res_id`. An invented `999999` is a caller
bug even where the guard now absorbs it. One such id in
`test_clone_hook_copies_group_and_auto_links` was fixed twice, in opposite
ways, on branches that did not see each other: `0e6cb9b2` guarded the
model, `44d61bc5` fixed the caller and added a test forbidding the guard,
and the merge kept both. See [Testing Guidelines](testing-guidelines.md).

### Computed-stored target vs side-effect compute

When you need a derived value (`res_id`, `res_model`, `state`, etc.) to be
eagerly maintained as dependencies change, **make the target field itself
computed-stored** — don't add a separate trigger field whose compute writes
the target as a side effect.

```python
# Anti-pattern: a trigger field that writes its real target as a side effect.
# Looks innocuous, but populating the trigger on upgrade runs the side-effect
# write for every row, and each write triggers the cascade through write()
# overrides recursively. On a 100k-row table this can take minutes.
res_id_computed = fields.Integer(
    compute="_compute_res_id_computed",
    store=True,   # forces populate on column add
    recursive=True,
)

@api.depends("parent_id.res_id")
def _compute_res_id_computed(self):
    for service in self:
        service.res_id_computed = service.root_id.res_id
        if service.id != service.root_id.id:
            service.res_id = service.parent_id.res_id      # side effect
            service.res_model = service.parent_id.res_model

# Pattern: make res_id / res_model themselves computed-stored, writable via
# readonly=False (NOT a no-op inverse), so direct writes still work (wizards,
# migrations, tests).
res_id = fields.Integer(
    required=True,
    compute="_compute_subject",
    store=True,
    readonly=False,        # writable: direct assignments land on the row
    recursive=True,
    precompute=True,       # compute runs before INSERT so required check passes
    # NB: deliberately NO literal default= -- see "precompute vs default" below
)
res_model = fields.Char(
    compute="_compute_subject",
    store=True,
    readonly=False,
    recursive=True,
    precompute=True,
)

@api.depends("subject_from", "parent_id.res_id", "parent_id.res_model")
def _compute_subject(self):
    for service in self:
        if service.subject_from == "inherit" and service.parent_id:
            service.res_id = service.parent_id.res_id
            service.res_model = service.parent_id.res_model
        elif service.subject_from == "self":
            pass  # preserve manual value
```

**Writable without an inverse**: `readonly=False` on a stored compute makes it
writable directly — no `inverse=` method needed. A direct write lands on the
column; on the next recompute the `'self'` branch (or a `parent_id` guard)
leaves it alone so it sticks. A *no-op* `inverse=` was tried first and is
worse: during `precompute` the inverse swallows the compute's own assignment
and the value is lost.

**precompute vs `default=` collision (subtle, cost us hours)**: do **not** give
a `precompute=True` field a literal `default=`. `BaseModel._add_missing_default_values`
runs *before* `_add_precomputed_values`, so a literal default makes the field
"already present in vals" and the precompute loop **skips it** — the cascade
silently no-ops. Symptom: one field of a multi-field compute populates and the
other (the one with the default) stays at its default. Drop the literal default
and let precompute own the initial value (it falls back to the column default,
e.g. `0` for Integer, when the compute leaves it unset).

**Why this is faster on upgrade**: column-add populates each row once via
Odoo's batched recompute path; the write() cascade is not re-entered, and
deeper levels are reached via the dependency on `parent_id.res_id`, not by
re-firing `write()`.

**Why this is cleaner at runtime**: `@api.depends('parent_id.res_id', …)`
becomes the single source of truth for the cascade. There is no parallel
imperative path (no `_cascade_*_to_children()` helper, no `if vals.get('res_id')`
branch in `write()`, no `_retrigger_*()` hook on a related model).

Real-world reference: `riverflow.service.res_id` / `res_model` driven by
`_compute_subject` keyed off `subject_from`. See
[riverflow/SKILL.md — Subject Cascade and subject_from](../../riverflow/SKILL.md#subject-cascade-and-subject_from).

### Deferring an expensive stored compute during bulk migration

When a stored compute does heavy per-record work (a sibling SQL scan, a tree
rebuild) and a migration re-writes hundreds of records in a loop, each write
re-fires the compute on overlapping record sets — an N+1 storm that dominates
the upgrade. A **context-flag guard** lets the migration skip the per-write
rebuild and do one batched rebuild at the end.

```python
# On the model: opt-in deferral guard at the TOP of the compute.
def _compute_display_order(self):
    if any(isinstance(r.id, api.NewId) for r in self):
        return
    if self.env.context.get("defer_display_order"):
        # PRESERVE the stored value, do NOT bare-return: this is a stored field,
        # so a bare return flushes NULL. compute_value() has already cleared
        # these records from `tocompute` and protects the field, so reading the
        # old value is safe (no recursion) and assigning it marks the field
        # computed -- a flush inside the deferred scope then converges.
        for rec in self:
            rec.display_order = rec.display_order
        return
    ...  # expensive sibling scan + tree rebuild

# The end-of-migration rebuild (flag-free env): route through the normal
# recompute machinery so it actually rebuilds.
def _rebuild_display_order(env):
    recs = env["riverflow.service"].search([...])
    env.add_to_compute(recs._fields["display_order"], recs)
    env.flush_all()

# In the migration: defer the loop, rebuild once.
denv = env(context=dict(env.context, defer_display_order=True))
for root in roots.with_context(defer_display_order=True):
    root.write({...})
_rebuild_display_order(env)
```

Three pitfalls that cost real debugging time:

- **Do NOT `add_to_compute` in the guard.** `Field.compute_value` removes the
  records from `tocompute` *before* calling the compute (and protects the
  field). Re-adding them inside the guard means any flush inside the deferred
  scope (e.g. a `flush_model()` in an index-build helper) drives
  `Environment._recompute_all` to its `MAX_FIXPOINT_ITERATIONS` cap (`Too many
  iterations for recomputing fields!`) and leaves the field uncomputed.
  Self-assigning the current value converges instead.
- **The end rebuild needs `add_to_compute` + `flush_all` on a flag-free env.**
  The self-assign guard clears the records from `tocompute`, so the natural
  end-of-load flush will NOT recompute them — you must explicitly re-queue and
  flush. Going through `add_to_compute`/`flush_all` (not a direct
  `_compute_*()` call) routes back through `compute_value`, which clears
  `tocompute` before computing, so it does the real rebuild exactly once.
- **Cache-warmth makes it all-or-nothing across a consecutive migration chain.**
  The first deferred rebuild is *cold* and pays the full one-time recompute;
  later deferred migrations reuse that warm cache and are cheap. Deferring only
  *some* migrations (e.g. 5.2/5.5 but not 5.1) sends the first deferred rebuild
  cold and is *slower* than not deferring. Defer the whole consecutive run.

**What it does and does not buy.** Deferral removes the *redundant repeated*
recomputes across the migration chain (measured: ~9% fewer queries,
deterministic, on the WP V2 4.23→5.20 upgrade). It does **not** make the one
unavoidable rebuild cheaper — if the migration genuinely changes group
membership for N trees, those N trees must be rebuilt once regardless, and that
cold rebuild is the floor. A bigger win requires making the compute itself
cheaper (bulk-load its sort keys instead of per-node ORM reads). Real-world
reference: `riverflow.service._compute_display_order` + the `defer_display_order`
flag; `crewradar_cuneus_sign.hooks._rebuild_wp_display_order`; WP migrations
19.0.5.1 / 5.2 / 5.5.

### Recursive stored compute that READS its own field → RecursionError

A `recursive=True` stored compute must **never read its own field via the ORM
getter on a record whose recompute is still pending**. Recursive fields are
recomputed **one record at a time** (`Field.recompute` → `recursive_compute`,
odoo/orm/fields.py). Reading a still-pending sibling re-enters the compute for
*that* sibling, which walks its tree and reads the next pending record — nesting
~10 Python frames per dirty record. When a single change dirties a whole tree at
once, the DFS blows Python's 1000-frame limit.

```python
# Anti-pattern: display_order is recursive=True (its deps root_name/deadline
# self-reference via root_id). This write-avoidance read re-enters the compute
# for every dirty sibling.
def assign_sequence(service_id, ...):
    service = service_dict[service_id]
    if service.display_order != display_order:   # READ on a pending sibling → recompute → recurse
        service.display_order = display_order

# Fix: guard the read. When the record is still pending, assign WITHOUT reading.
order_field = self._fields["display_order"]
...
def assign_sequence(service_id, ...):
    service = service_dict[service_id]
    if self.env.is_to_compute(order_field, service) or service.display_order != display_order:
        service.display_order = display_order
```

Why the guarded assign is safe and converges (verified against odoo/orm):

- `env.is_to_compute(field, rec)` (environments.py) is True exactly for records
  still queued in `tocompute` — i.e. the ones whose read would re-enter.
- Assigning a stored compute goes `Field.__set__` → `Field.write`, which calls
  `env.remove_to_compute(self, records)` **first** (fields.py) and **never
  triggers the getter**. So the assign both avoids the re-entrant read and clears
  the pending computation.
- Records in the batch currently being computed are **not** pending —
  `Field.compute_value` removes `self` from `tocompute` before invoking the
  compute — so they take the cheap read-and-compare path (their read is a plain
  cache/DB fetch, no recursion).

**Cost of the guard**: a pending record has no cache value, so the assign marks
it dirty even when the DB value is unchanged → one redundant `UPDATE` per record
in a whole-tree-dirty flush. Acceptable — that exact path previously *crashed*.
This is the targeted form of "bulk-load the sort keys instead of per-node ORM
reads" flagged in the deferral subsection above: it removes only the read of the
field *being computed*; the bounded sort-key reads (`deadline`, `daily_prio`,
`name`) recurse only by parent-chain depth and are left alone.

**How it surfaces (production trigger, not just theory)**: editing
`planned_end_date` on a `crewradar.log.entry` cascaded down the whole attached
service tree (entry deadline → root deadline → child `project_deadline` →
`deadline` → `display_order`), leaving every service's `display_order` pending at
once. A later `riverflow.service.search()` inside `auto_add_services` flushed the
model → per-record recompute → `RecursionError` on trees of ~100+ services.
Real-world reference: `riverflow.service._compute_display_order`; regression test
`riverflow/tests/test_service_tree.py::test_display_order_large_tree_no_recursion_error`
(builds a 150-child tree, dirties it via a root deadline change, reads a child).

### Deep o2m dependencies on a stored compute fan out to mass recompute

A computed-stored field is the right tool for a cascade keyed on *scalar* or
*immediate-parent* dependencies. It is the **wrong** tool when the reactivity
needs a deep one2many path on a large table.

```python
# Anti-pattern: res_id/res_model are stored, and this dep makes EVERY service
# whose parent-employee owns a touched log entry recompute-and-write whenever
# any of those log entries change. During an upgrade that fans out to ~20k
# recomputes (3+ min) because each recompute also walks the whole o2m.
@api.depends(
    "parent_id.employee_id.log_entry_ids.start_date",
    "parent_id.employee_id.log_entry_ids.end_date",
    "parent_id.employee_id.log_entry_ids.work_status",
    "parent_id.employee_id.log_entry_ids.active",
)
def _compute_subject(self):
    ...
```

On the **old non-stored mirror field** these same deep deps were cheap (a
non-stored compute only invalidates cache, never writes). Promoting the target
to stored turns every dep change into a write — the cost the mirror field was
quietly avoiding.

**Fix — invert the reactivity to a scoped trigger on the related model.** Keep
only the cheap deps on the compute (`parent_id.employee_id`,
`parent_id.supply_actual_date`). Put the o2m reactivity on the *log entry* side,
where it can be scoped to the handful of affected records:

```python
# On crewradar.log.entry:
@api.depends("start_date", "end_date", "work_status", "active", "employee_id")
def _compute_most_fitting_marker_trigger(self):
    for entry in self:
        entry.most_fitting_marker_trigger = bool(entry.employee_id)  # dummy host value
    if not self.env.registry.loaded:
        return                       # no-op during module load/upgrade -> fast -u
    if any(isinstance(r.id, NewId) for r in self):
        return
    markers = self.env["riverflow.service"].search([
        ("subject_from", "=", "most_fitting_log_entry"),
        ("parent_id.employee_id", "in", self.employee_id.ids),
    ])
    if markers:
        # Re-resolve synchronously: the marker runs a fresh ORM search, so a
        # deferred env.add_to_compute risks resolving before THIS entry's
        # fields are visible to that search.
        markers.invalidate_recordset(["res_id", "res_model"])
        markers._compute_subject()
```

**Trade-off**: the trigger is a stored compute, so it fires on *flush* — i.e.
at the request/commit boundary, not necessarily the same in-Python statement.
That is correct for production (the next request sees the update); tests that
assert same-transaction reactivity must call `env.flush_all()` to simulate the
boundary. The `registry.loaded` guard is what keeps the upgrade fast — it makes
the trigger a no-op during module load, when live data is already consistent.

Real-world reference: `crewradar.log.entry._compute_most_fitting_marker_trigger`
re-resolves `most_fitting_log_entry` marker services (e.g. the AB Appointment
under a Work Permit). This kept `-u` at ~27s instead of 3+ min on a ~100k-row
service table.

## Active Field Handling

### The active_test Context Problem

Odoo's `active_test` context flag controls whether archived records are included:

- `active_test=True` (default): Only active records returned
- `active_test=False`: All records returned (Odoo UI sets this on certain writes)

This can cause inconsistent behavior in compute methods.

### Solution: Explicit Active Checks

Always check the `active` field explicitly:

```python
@api.depends('employee_id', 'employee_id.log_entry_ids')
def _compute_work_entries(self):
    for record in self:
        # Explicit active check - consistent regardless of context
        work_entries = record.employee_id.log_entry_ids.filtered(
            lambda e: e.active and e.work_status == 'work'
        )
        record.work_entry_count = len(work_entries)
```

### Archiving Does Not Fire Bare One2many Depends

Explicit active checks in the compute body are only half the rule — the
**trigger side** needs `active` too. Writing `active` on a related record does
not change One2many membership (archive only filters *reads*), so a compute
that depends on `parent.child_ids` / `parent.child_ids.some_field` is never
re-triggered when a child is archived or unarchived. The stored result goes
stale until an unrelated dependency happens to change.

```python
# Bad: recomputes on create/unlink/number edits — but NOT on archive
@api.depends("credential_ids", "credential_ids.number", "credential_ids.expiry_date")

# Good: archive/unarchive re-fires the compute
@api.depends(
    "credential_ids",
    "credential_ids.number",
    "credential_ids.expiry_date",
    "credential_ids.active",
)
```

Rule of thumb: whenever the compute's *outcome* depends on which related
records are active (an explicit `c.active` filter in the body is the tell),
the depends list must carry the matching `<path>.active` entry. Real case:
`_compute_passport_details` on `hr.employee` (the passport of record) feeds
the credential consistency passport-number check — without
`credential_ids.active`, archiving an employee's passport left stale mismatch
rows on their permits indefinitely.

### Models Requiring Active Checks

These models in the workspace have `active` fields:

| Model | Module |
|-------|--------|
| `crewradar.log.entry` | crewradar |
| `crewradar.site` | crewradar |
| `rivercreds.credential` | rivercreds |
| `riverflow.auto.add.service` | riverflow |
| `riverflow.service` | riverflow |
| `riverflow.state.record` | riverflow |
| `riverflow.state` | riverflow |
| `riverflow.transition.action` | riverflow |
| `riverflow.transition` | riverflow |
| `riverflow.workflow` | riverflow |

### Prefer FK Field Comparison over One2many Containment

When excluding a record's own related records from a filtered set, prefer a direct FK field comparison over One2many containment (`in` / `not in` on a recordset traversal). One2many traversals are sensitive to the ambient `active_test` context, which can vary between callers (e.g., wizard `default_get` vs `@api.depends` compute triggered by a UI write). This makes `record not in parent.child_ids` return inconsistent results depending on context.

```python
# Bad: One2many containment — context-sensitive
own_creds = service.credential_ids
filtered = all_creds.filtered(lambda c: c.active and c not in own_creds)
# own_creds may include or exclude archived records depending on
# the ambient active_test flag, making the `not in` check unreliable.

# Good: direct FK comparison — context-independent
filtered = all_creds.filtered(lambda c: c.active and c.service_id != service)
# The FK field is a simple relational read, not affected by active_test.
```

When the One2many traversal is still needed for other purposes, stabilize it with `with_context(active_test=False)` and let the explicit `c.active` check in the lambda do the real filtering:

```python
# Stabilize One2many traversal, then filter explicitly
creds = employee.with_context(active_test=False).credential_ids.filtered(
    lambda c: c.active and c.credential_type_id == some_type
)
```

**Real-world example**: The A1 overlap detection in `_get_submitted_a1_credentials()` was bitten by this when a restarted service's own A1 Certificate appeared as a false overlap. The non-stored `a1_application_json` computed field ran in different ORM environments with different `active_test` flags (wizard `default_get` vs compute triggered by a state write), causing `c not in service.credential_ids` to behave differently. Fixed by replacing with `c.service_id != exclude_service`. Full case documented in the consuming business's own skill bundle.

## View Requirement Patterns

### Centralized `is_required_*` Computed Booleans

When a form view has multiple fields whose `required` attribute depends on complex or shared logic (e.g., type configuration flags, application-phase relaxation, holder applicability), extract the requirement logic into non-stored computed Boolean fields on the model. XML views then use `required="is_required_field_name"` instead of inline expressions.

```python
# Model: centralized requirement logic
is_required_number = fields.Boolean(compute="_compute_is_required_fields")
is_required_country = fields.Boolean(compute="_compute_is_required_fields")

@api.depends("has_number", "has_country", "application_date")
def _compute_is_required_fields(self):
    for rec in self:
        in_application = bool(rec.application_date)
        rec.is_required_number = rec.has_number and not in_application
        rec.is_required_country = rec.has_country
```

```xml
<!-- View: clean required attributes -->
<field name="number" required="is_required_number" />
<field name="country_id" required="is_required_country" />
```

**Benefits**: requirement logic is testable in Python, shared across views (form, list, wizard), and avoids duplicating complex boolean expressions in XML. The `is_required_*` fields are non-stored (no database column) and recomputed on every form load.

Applied in: `rivercreds.credential` (10 `is_required_*` fields for holder applicability, data field relaxation during application phase, validity period).

## XML View Patterns

### Developer-only server action (debug menu)

A server action bound to a model's Action (cog) menu but visible only in
developer mode: restrict it with `group_ids` referencing `base.group_no_one`.
In Odoo 19 the field on `ir.actions.server` is **`group_ids`**, not `groups_id`
— using the old name raises `ValueError: Invalid field 'groups_id'` and the
registry fails to load.

```xml
<record id="action_recompute_subject_debug" model="ir.actions.server">
    <field name="name">Recompute Subject (debug)</field>
    <field name="model_id" ref="model_riverflow_service"/>
    <field name="binding_model_id" ref="model_riverflow_service"/>
    <field name="group_ids" eval="[(4, ref('base.group_no_one'))]"/>
    <field name="binding_view_types">list,form</field>
    <field name="state">code</field>
    <field name="code">
        if records:
        records.action_recompute_subject_debug()
    </field>
</record>
```

Use this for profiling/diagnostic actions (e.g. force-recompute a stored
compute on the selected records, then capture it with the UI profiler) without
exposing the action to ordinary users.

### Odoo 19 Syntax Changes

```xml
<!-- Odoo 19: Use <list> not <tree> -->
<list string="Entries">
    <field name="name"/>
    <field name="date"/>
</list>

<!-- No <data> wrapper needed -->
<record id="view_entry_list" model="ir.ui.view">
    <field name="name">entry.list</field>
    <field name="model">crewradar.log.entry</field>
    <field name="arch" type="xml">
        <list>
            <field name="name"/>
        </list>
    </field>
</record>
```

### `<i class="fa ...">` Must Have Accessible Text

Odoo 19's `ir_ui_view` validator emits a WARNING when an `<i>` with a `fa` class has no accessible text:

```
A <i> with fa class (fa fa-exclamation-circle) must have title in its tag,
parents, descendants or have text
```

This warning lands in `ir_logging` on Odoo.sh dev builds and turns them red even though the view loads fine. Self-closing icons with the label as a sibling **do not** satisfy the rule — the validator looks at the `<i>` element's own tag/parents/descendants, not adjacent text:

```xml
<!-- ✗ WARNING: <i> has no text/title and the label is a sibling, not a descendant -->
<div class="alert alert-danger" role="alert">
    <i class="fa fa-exclamation-circle" />
    <strong>Cannot generate yet.</strong> Required fields are empty.
</div>

<!-- ✓ Add title + role + aria-label so screen readers and the linter see the icon's purpose -->
<div class="alert alert-danger" role="alert">
    <i class="fa fa-exclamation-circle" title="Warning" role="img" aria-label="Warning"/>
    <strong>Cannot generate yet.</strong> Required fields are empty.
</div>
```

`role="alert"` on the parent does **not** silence the warning — it's the `<i>` itself that needs an accessible label.

### Adding Files to Module

When creating new files, update the module configuration:

**Python files** - Add to `__init__.py`:

```python
# models/__init__.py
from . import crewradar_log_entry
from . import crewradar_site
from . import new_model  # Add new model
```

**XML files** - Add to `__manifest__.py`:

```python
# __manifest__.py
{
    'data': [
        'security/ir.model.access.csv',
        'views/existing_views.xml',
        'views/new_views.xml',  # Add new view file
        'data/new_data.xml',    # Add new data file
    ],
}
```

### Per-record scoped Selection options (`filterable_selection` + `whitelist_fname`)

A `Selection` field's options are resolved once per `fields_get` (per view load), **not per record** — so a `selection=` function cannot return options based on the specific record being edited. To scope the *shown* options per record, keep a broad base selection and filter it in the widget with the `filterable_selection` field widget:

```python
# Base selection: broad (a superset). A dynamic function keeps it maintenance-free —
# here it derives from another model's own selection so new values appear automatically.
some_type = fields.Selection(selection="_some_type_selection")

# Per-record whitelist the widget reads. Json so record.data[fname] is a JS array.
some_type_whitelist = fields.Json(compute="_compute_some_type_whitelist")

@api.model
def _some_type_selection(self):
    values = self.env["other.model"].fields_get(["kind"])["kind"]["selection"]
    return [(v, label) for v, label in values if v not in ("excluded",)]

@api.depends("driver_field")
def _compute_some_type_whitelist(self):
    allowed = {v for v, _ in self._some_type_selection()}
    for rec in self:
        rec.some_type_whitelist = sorted(allowed & rec._values_valid_here())
```

```xml
<!-- The whitelist field must be present (invisible) so it loads into record.data -->
<field name="some_type_whitelist" invisible="1"/>
<field name="some_type" widget="filterable_selection"
       options="{'whitelist_fname': 'some_type_whitelist'}"/>
```

The widget (`web/…/fields/selection/filterable_selection_field.js`) shows only options whose value is in `record.data[whitelist_fname]`, **always keeping the currently-stored value** even if outside the whitelist (so existing data never disappears). It also accepts `whitelisted_values` / `blacklisted_values` (static arrays) instead of a field name. Because a whitelist can only narrow the visible options — never enforce them at other entry points — pair it with a runtime guard where the value is consumed (e.g. an `@api.constrains`, or a client-side check before acting on it). Real example: `oteny_shortcut` scopes `ir.filters.shortcut_view_type` to the view types a filter's model actually has.

## Date and Time

### Current Date/Time

```python
from odoo import fields

# Current date (date object)
today = fields.Date.today()

# Current datetime (datetime object)
now = fields.Datetime.now()

# Convert string to date
date = fields.Date.from_string('2024-01-15')

# Convert date to string
date_str = fields.Date.to_string(date)
```

### Date Arithmetic

```python
from datetime import timedelta
from dateutil.relativedelta import relativedelta

# Add days
next_week = fields.Date.today() + timedelta(days=7)

# Add months (use relativedelta for month arithmetic)
next_month = fields.Date.today() + relativedelta(months=1)

# First/last day of month
first_of_month = date.replace(day=1)
last_of_month = (date.replace(day=1) + relativedelta(months=1)) - timedelta(days=1)
```

## Float Comparison

Python `float` values computed as `a + b - c` in binary arithmetic rarely land on exactly zero. Never use `==` or `!=` against `0` for floats derived from arithmetic on money, hours, or quantities — use Odoo's helpers from `odoo.tools`:

```python
from odoo.tools import float_is_zero, float_compare

if not float_is_zero(balance, precision_digits=2):
    ...

if float_compare(amount, threshold, precision_digits=2) > 0:
    ...
```

**Precision guidance**:

- Match the precision at which the value is **displayed** to the user. A value that displays as `0.0 hours` (via `.1f`) must not fire a "non-zero" check at a stricter precision — it produces user-visible inconsistency.
- For currency, prefer the currency's `decimal_places` (`precision_rounding=currency.rounding`) rather than a hardcoded `precision_digits`.

**Crewradar example**: The `vacation_balance_at_contract_end` check on `hr.employee` originally used `vacation_balance != 0` and fired on residue like `1.4e-14` hours while displaying "0.0 hours" to the user. The fix uses `float_is_zero(vacation_balance, precision_digits=2)` — balances under 0.01 h (≈36 s) count as zero. Full case documented in the consuming business's own skill bundle.

## Invoice Line Rounding (price_subtotal vs amount_currency)

When summarizing or exporting `account.move.line` data (e.g., DATEV exports, custom reporting wizards), prefer **`-amount_currency`** over `price_subtotal` as the per-line base. They can disagree by 0.01 cent on the same line.

### Why they disagree

The disagreement is the standard **per-item-vs-sum rounding** problem: `round(a) + round(b) + round(c) ≠ round(a + b + c)` whenever the per-item raw values land on sub-cent fractions. In Crewradar this happens routinely because the half-day boundary convention bills with quantities like `9.5` or `22.5` (half-days), and an exact 2-decimal rate multiplied by `0.5` lands on a half-cent (e.g. `9.5 × 12.61 EUR/day = 119.795`).

Odoo handles it like this:

1. `_compute_totals` runs **per line in isolation**: it stores `price_subtotal = round(quantity × price_unit)` (`total_excluded_currency`).
2. During posting, `_round_tax_details_base_lines` (always called, even for 0% tax) compares `round(sum of raw line amounts)` (the document-level truth, which becomes `invoice.amount_total`) against `sum of per-line rounded subtotals`. Any gap is **distributed onto the largest-by-raw-amount line** as `delta_total_excluded_currency`.
3. `line.amount_currency` reflects the post-distribution value (`total_excluded_currency + delta_total_excluded_currency`); `line.price_subtotal` does **not** — the field stores only the pre-distribution per-line round.

Result: `sum(line.price_subtotal) ≠ invoice.amount_untaxed` and `sum(line.price_total) ≠ invoice.amount_total` on ~12% of Crewradar invoices in production. Recomputing tax-inclusive amounts as `price_subtotal × (1 + tax_rate / 100)` per line drifts from the customer's invoice total by 0.01-0.03 EUR.

This applies under both `round_globally` (the default `res.company.tax_calculation_rounding_method`) and `round_per_line`, but the redistribution amount changes; `amount_currency` is the right field in either case because it always reflects what Odoo will actually book.

### Sign convention

For product lines, `amount_currency` is **negative** for `out_invoice` (a credit on revenue) and **positive** for `out_refund`. Using `-amount_currency` yields the correct customer-facing sign for both directions, so no separate `if move_type == "out_refund"` flip is needed.

### Residual cent reconciliation (tax-bearing invoices)

For tax-bearing invoices the same per-item-vs-sum drift can re-emerge after multiplying each rounded base by `(1 + tax_rate)`. A per-invoice reconciliation pass that compares `sum(computed DATEV amounts)` against `invoice.amount_total` and pushes any residual cent onto the largest-absolute-amount line guarantees an exact match, in line with the standard "smallest relative impact" rule for distributing rounding cents.

```python
amount_total = currency.round(
    -line.amount_currency * (1 + line.tax_ids.amount / 100)
)
```

### Empirical example (INV/2026/00445)

```
L10420 Airfare:  qty=9.5  × pu=12.61 = 119.795          → price_subtotal=119.80
L10424 Airfare:  qty=22.5 × pu=12.61 = 283.725          → price_subtotal=283.73
(four other lines have clean 2-decimal raw products)

Document raw total:    9131.78 = invoice.amount_total
Sum(price_subtotal):   9131.79  ← drifts by +0.01

Odoo absorbs delta=-0.01 into the largest line:
  L10423: price_subtotal=6037.20, amount_currency=-6037.19
```

**Crewradar example**: The DATEV export wizard (`crewradar/wizards/crewradar_datev_export_wizard.py`) was previously using `price_subtotal` and recalculating per line; switched to `-amount_currency` plus a per-invoice reconciliation pass. Full case documented in the consuming business's own skill bundle.

### This is Odoo's own pattern

The fix is not Crewradar-specific; it is what Odoo itself does in every journal-side export it ships:

- **SAF-T** (EU government bookkeeping export, `account_saft/models/account_general_ledger.py`): reads `account_move_line.amount_currency`, `debit`, `credit`, `balance` directly from journal entries; uses `_get_query_tax_details` for tax breakdowns.
- **General Ledger Report** (`account_reports/models/account_general_ledger.py`): same — reads `amount_currency`, `debit`, `credit`, `balance` from journal entries.

`price_subtotal` does **not** appear anywhere in Odoo's production code for `account_reports`, `account_saft`, `l10n_de_reports`, or `l10n_de_intrastat` — only in test fixtures. Odoo treats `price_subtotal` as a customer-facing invoice display field; journal-side exports always read the journal.

**When to use which field**:

| Audience | Field to read |
|---|---|
| Customer-facing invoice display (PDF, portal, line subtotals) | `price_subtotal` / `price_total` |
| Bookkeeping export, accounting report, tax filing, anything that must reconcile to `invoice.amount_total` | `amount_currency` / `debit` / `credit` / `balance` |

**Before writing a new accounting export**: read this section, then mirror SAF-T's pattern in `account_saft/models/account_general_ledger.py`.

## Mail template rendering (inline_template)

When `mail.render.mixin._render_template()` (or equivalent) runs with **`engine='inline_template'`**, the template is **not** full Jinja2. **Block/control tags are not supported** — `{% if %}`, `{% endif %}`, `{% for %}`, and similar appear as **literal text** in the output.

**Rule**: Use only **`{{ }}`** expression interpolation. For conditional fragments, use a Python-style ternary inside an expression, including conditional HTML:

```text
{{ '<p>Extra paragraph</p>' if condition else '' }}
```

**Preview vs send dates**: Templates that reference `object.sent_date` (or similar) may show an **empty date** in wizard previews if that field is only set when the user clicks Generate. Prefer a value available in the rendering context at preview time (e.g. `datetime.date.today().strftime('%d.%m.%Y')` when the context exposes `datetime`), or another field that is always set before render.

**Crewradar example**: A1 cover letter `mail.template` bodies in `crewradar_cuneus_sign/data/a1_cover_letter_template.xml` must follow these rules; full case documented in the consuming business's own skill bundle.

**Calling a model method from a template**: a QWeb body (or an inline field such as `partner_to`) may call a **public** method on the record — `object.site_partner_ids_for('crew_hiring')`, `object.employee_id.next_placement_start_date()` — but the template safe-eval blocks underscore names, so a `_private` helper needs a public twin. Such a method **must accept an empty recordset**: `mail.template` renders the body at save time against a record whose relations are empty ("Error while checking if template can be rendered for field body_html"), and `ensure_one()` on `hr.employee()` raises where a plain field access would just be falsy. Return `False` for an empty `self` before `ensure_one()`. Seen 2026-09-09 on the passport reminder: the failure surfaced inside a `convert_file(mode="init")` migration, before any test ran.

## Command Syntax for Relational Fields

### One2many and Many2many Operations

```python
from odoo.fields import Command

# CREATE (0): Create new record and link
record.line_ids = [Command.create({
    'name': 'New Line',
    'amount': 100.0,
})]

# UPDATE (1): Update existing linked record
record.line_ids = [Command.update(line_id, {
    'amount': 150.0,
})]

# DELETE (2): Delete record from database
record.line_ids = [Command.delete(line_id)]

# UNLINK (3): Remove link without deleting
record.line_ids = [Command.unlink(line_id)]

# LINK (4): Link existing record
record.line_ids = [Command.link(existing_line_id)]

# CLEAR (5): Remove all links
record.line_ids = [Command.clear()]

# SET (6): Replace all links with new set
record.line_ids = [Command.set([line1_id, line2_id])]
```

### Combining Commands

```python
# Multiple operations in one assignment
record.line_ids = [
    Command.create({'name': 'New'}),
    Command.update(line1_id, {'amount': 200}),
    Command.delete(line2_id),
]
```

## Context Propagation

The context is a `frozendict` - use `with_context` to modify:

```python
# Replace entire context
records.with_context(new_context).do_stuff()

# Add/override context values
records.with_context(**additional_context).do_other_stuff()
records.with_context(lang='en_US', active_test=False).search([])
```

**Warning**: Context keys propagate automatically and can have side effects:

```python
# default_my_field sets default for ALL models with 'my_field' during create
# Use prefixed keys to isolate: mail_create_nosubscribe, mail_notrack, etc.
```

### Transition Mixin Context Leak

The riverflow transition mixin (`riverflow_transition_mixin.py` lines 35-39) copies ALL fields from the entity being transitioned as `default_*` context keys when opening the transition wizard. This is necessary for the wizard to pre-populate fields like `res_id`, `res_model`, `name` etc. However, it also leaks ALL other entity fields into the context — including boolean flags, One2many relations, monetary amounts, tags, and Reference fields.

**The danger**: When code in the wizard's `action_save` call chain creates NEW records (e.g., `_create_deferred_children` creating child services, credential wizard creating credentials), fields absent from the explicit `create(vals)` dict pick up values from the leaked `default_*` context. This causes:

- One2many fields: ORM reassigns parent's related records to the new child (steals records)
- Boolean flags: Children inherit parent-specific flags (e.g., `is_service_with_journey`)
- Monetary/price fields: Children get parent's values instead of template defaults
- Reference fields: Crash during `_set_resource_ref` (AttributeError on the resolved record)

**Clean context pattern** (used in `_create_deferred_children`): Strip all `default_*` keys and explicitly pass only the needed defaults:

```python
# Build a clean context — children get values from the template only.
# Only pass the 2 defaults that _create_service_member_from_template reads:
clean_ctx = {k: v for k, v in self.env.context.items()
             if not k.startswith("default_")}
clean_ctx["default_res_model"] = self.res_model
clean_ctx["default_res_id"] = self.res_id
Service = self.env["riverflow.service"].with_context(clean_ctx)
new_child = Service._create_service_member_from_template(child_template, self.id)
```

**Testing**: Tests that fire transitions via wizard must call `_prepare_transition_action()` on the entity to get the full mixin context, then create the wizard with that context. A minimal `{transition_id, active_ids}` context does not reproduce context-leak bugs. See `TestDeferredChildrenContextIsolation` in `riverflow/tests/test_deferred_children.py`.

**Bots over `/json/2/`**: Odoo refuses underscore method names on that pipe. The public twin is `prepare_transition_action()` on `riverflow.transition.mixin` (19.0.1.1237+). The defaults loop reads `ir.model` as **sudo**; wizard verbs still run as the seam user.

**Mixin narrowing (implemented)**: The transition mixin now resolves the wizard model first and only includes `default_*` keys for fields the wizard declares. The `getattr` loop still touches ALL entity fields — this is required because reading stored computed fields triggers their compute (the state_record tracker depends on this). Only the output (which defaults go into context) is filtered, not the input (which fields are accessed). Both fixes (mixin narrowing + `_create_deferred_children` clean context) are defense-in-depth.

### Suppression flags on stored-compute side effects: consumed, not deferred

A common pattern puts reactive side effects inside a stored compute (e.g. `hr.employee.credential_auto_add_trigger` runs auto-add/auto-complete when credentials change) and silences them during a wizard save with a context flag:

```python
def _compute_credential_auto_add_trigger(self):
    if not self.env.context.get("skip_credential_auto_add_trigger"):
        self._auto_complete_holder_credential_services()
        self.env["riverflow.auto.add.service"].auto_add_services(self)
    ...
```

**The trap**: a stored compute recomputes **once per dependency change**, in the same transaction, under whatever context is active at flush time. If the suppressed workflow **itself causes the triggering change** (the credential wizard creates the very credential the trigger watches), the one recompute that would ever fire the side effect runs with the flag set — and is then *spent*. The stored value is up to date; nothing recomputes it later. The flag does not defer the side effect, it silently swallows it.

**Rule**: whenever a flow runs under such a suppression flag *and* mutates the compute's dependencies, it must **explicitly re-run the suppressed work at the end** of the flow (after its own state writes are flushed, so guards see the final state). Do not rely on "the trigger will fire" — it already did.

**Reference case**: the Upload Permit close silently dropped employees off the work-permit radar until the wizard's `create_related_records` re-ran `auto_add_services` explicitly on every end-state close. Full incident record in the consuming business's own skill bundle.

**Corollary**: an invariant that matters ("always exactly one open service") needs an *enforcer* at every path that can break it. A check result that only detects the violation ("should self-heal; investigate if this persists") is a safety net, not a repair — its message must not promise healing that no code performs.

## Backend Record Links in an Html Field

Odoo 19's router builds a record URL with no action path as
**`/odoo/m-<model>/<id>`** (`web/static/src/core/browser/router.js`, the
`path.push(\`m-${model}\`)` branch). A plain anchor with that href is clickable
anywhere an Html field is rendered:

```python
Markup('<a href="/odoo/m-%s/%s">%s</a>') % (record._name, record.id, record.display_name)
```

Do not reach for `_get_html_link()` here. That helper emits
`<a href=# data-oe-model=... data-oe-id=...>`, which only becomes clickable
where the chatter's link handler is present — it is inert in an ordinary Html
field on a form.

`crewradar.issue.mixin.get_record_link()` wraps this, and its `issues_details`
renderer shows the pattern for mixing trusted and untrusted text: an issue may
supply `description_html` (markup our own code built, emitted as-is) or
`description` (plain text, escaped through `Markup("%s") % value`). Keeping the
two keys separate means checks written before the field became Html keep
rendering correctly, and no caller can inject markup by accident.

## Commercial Fields on res.partner (company → contact sync)

A field that describes the **commercial entity** rather than the address record
must be declared in `_commercial_fields()`, never synced by a `create()` or
`write()` override. Odoo already uses this seam for `vat`; `account` and 46
`l10n_*` modules extend it.

```python
@api.model
def _commercial_fields(self):
    return super()._commercial_fields() + ["vies_valid"]
```

What the seam gives you, from `odoo/addons/base/models/res_partner.py`:

| Mechanism | When it fires | Direction |
|---|---|---|
| `_commercial_sync_from_company` | `create()` with a `parent_id`, and on reparenting | company → contact |
| `_children_sync` → `_commercial_sync_to_descendants` | a write containing a commercial field | company → descendants |
| `_synced_commercial_fields()` | a write on a child | contact → company |

Two behaviours to know before you add a field:

- **`_get_commercial_values()` filters on truthiness** (`if self[fname]`), so on
  create and reparenting only a truthy value is pushed down. A falsy company
  value leaves the contact at its own default. Later *changes* do propagate a
  falsy value, because `_commercial_sync_to_descendants` uses
  `_convert_fields_to_values`, which is not filtered.
- **`_commercial_fields()` and `_synced_commercial_fields()` are different
  lists.** The base implementation is `_synced_commercial_fields() +
  ['company_registry', 'industry_id']`. Adding to the synced list makes a child
  able to overwrite its parent. Extend `_commercial_fields()` alone unless you
  genuinely want the upward direction.

**Ordering against a computed field.** If the field is `compute=... store=True
readonly=False`, the sync's `write()` marks it protected and removes it from
`tocompute`, so a later `remove_to_compute` by another module's `create()`
override is a no-op and the synced value survives. This is what makes the seam
usable to restore an inheritance that a core compute no longer performs — a
consuming business documents its own worked example of this (the `vies_valid`
case) in its own skill bundle.

## Think Extendable

Keep methods small and logic overridable for submodules:

```python
# Avoid: hardcoded logic that can't be extended
def action(self):
    partners = self.env['res.partner'].search([
        ('is_company', '=', True),
        ('country_id', '=', self.env.ref('base.be').id),
    ])
    emails = partners.filtered(lambda r: r.email and r.active).mapped('email')

# Better: extract domain/criteria to overridable methods
def action(self):
    partners = self.env['res.partner'].search(self._get_partner_domain())
    emails = partners.filtered(lambda r: r._filter_for_mailing()).mapped('email')

def _get_partner_domain(self):
    """Override in submodule to change partner selection."""
    return [('is_company', '=', True), ('country_id', '=', self.env.ref('base.be').id)]

def _filter_for_mailing(self):
    """Override to change filtering criteria."""
    self.ensure_one()
    return self.email and self.active
```

## Transaction Management

**Never commit transactions manually** - the framework handles this:

```python
# NEVER do this (except with explicit cursor you created)
cr.commit()
cr.rollback()
```

The framework commits after successful RPC calls and rolls back on errors.

### Per-Record Cursor Isolation (Cron Workers)

For long-running cron jobs that process many records, open a fresh cursor per record to isolate failures and release DB connections between items. This mirrors Odoo's own `ir.cron` `job_cr` pattern:

```python
def _process_batch(self):
    record_ids = self.search([("state", "=", "pending")]).ids
    for record_id in record_ids:
        with self.pool.cursor() as cr:
            env = api.Environment(cr, self.env.uid, self.env.context)
            Model = env[self._name]
            # Atomic claim: only one worker succeeds per record
            cr.execute(
                "UPDATE my_table SET state = 'running' "
                "WHERE id = %s AND state = 'pending' RETURNING id",
                [record_id],
            )
            if not cr.fetchone():
                continue  # already claimed by concurrent worker
            record = Model.browse(record_id)
            record._do_work()
            # cr commits on context manager exit; rolls back on exception
```

Benefits: (a) failed records don't roll back the batch, (b) main cursor is freed between records, (c) concurrent cron invocations safely skip claimed records via the atomic `UPDATE ... WHERE state = 'pending'`. For crash recovery, reset records stuck in `running` beyond a timeout threshold.

**Testing caveat**: `TransactionCase` tests run in a savepoint that is invisible to cursors opened via `self.pool.cursor()`. Test the claiming logic indirectly (e.g., call `_generate_review()` directly) rather than through the per-cursor wrapper.

### Exception Handling with Savepoints

Catch only specific exceptions. Use savepoints to isolate error handling:

```python
# BAD: catches everything, leaves ORM in undefined state
try:
    do_something()
except Exception as e:
    _logger.warning(e)  # ValidationError wasn't rolled back!

# GOOD: use savepoint to isolate
try:
    with self.env.cr.savepoint():
        do_stuff()  # flushes on enter, rolls back on exception
except SomeSpecificError:
    handle_error()
```

**Warning**: More than 64 savepoints in a transaction slows PostgreSQL significantly.

## message_post Body Handling

In Odoo 19, `message_post` distinguishes between plain text and HTML bodies by type:

- **`str` body** → Odoo escapes the content (treats it as plain text) and wraps it in `<p>` tags. Passing `body="<p>Hello</p>"` produces double-encoded HTML: `<p>&lt;p&gt;Hello&lt;/p&gt;</p>`.
- **`Markup` body** → Odoo stores as-is (treated as trusted HTML). This is the correct way to post HTML content.

```python
from markupsafe import Markup

# Good: Markup body — stored as proper HTML
service.message_post(
    body=Markup("<p>%s</p>") % clean_text,
    message_type="comment",
    subtype_xmlid="mail.mt_note",
)

# Bad: plain str body — Odoo escapes the <p> tags
service.message_post(
    body=f"<p>{clean_text}</p>",  # double-encoded!
    message_type="comment",
    subtype_xmlid="mail.mt_note",
)
```

The `Markup("<p>%s</p>") % text` pattern is safe: the `%` operator on `Markup` automatically escapes the interpolated value, preventing XSS while keeping the `<p>` wrapper as trusted HTML.

There is also a `body_is_html=True` parameter, but Odoo logs a deprecation warning for it and recommends using `Markup` instead.

## Wizard File Download via act_url

When a wizard needs to trigger a file download AND close the dialog, use `ir.actions.act_url` with the `close` flag. Without `close: True`, the dialog stays open because Odoo's `_executeActURLAction` in `action_service.js` only dispatches `act_window_close` when `action.close` is truthy.

```python
def action_download(self):
    attachment = self.env["ir.attachment"].create({
        "name": "output.pdf",
        "datas": base64.b64encode(pdf_bytes),
        "mimetype": "application/pdf",
        "res_model": self._name,
        "res_id": self.id,
    })
    return {
        "type": "ir.actions.act_url",
        "url": f"/web/content/{attachment.id}?download=true",
        "target": "new",
        "close": True,
    }
```

**`target` values** for `act_url` (from `action_service.js`):

- `"self"` — navigates the current window (`location.assign`); for file downloads the browser downloads without navigating away
- `"new"` — opens a new tab/window via `window.open`; respects `close` flag for dialog handling
- `"download"` — opens a new tab/window; no `close` flag handling

**`close` flag** (only effective when target is not `"self"` or `"download"`):

- `True` — dispatches `act_window_close` after opening the URL, which closes the wizard dialog and refreshes the parent view
- Falsy/absent — calls `options.onClose()` if available, but the dialog may stay open

**Non-wizard buttons** (e.g., form view button actions) don't need `close: True` since there's no dialog to close. The `rivercreds.credential.action_download_combined_pdf()` pattern uses `target: "new"` without `close` because it's a form button, not a wizard.

Applied in: `sign_document_wizard.py` (`_download_combined_pdf`).

## Wizard Attachments Posted to Chatter

When a TransientModel wizard uploads attachments and posts them to a record's chatter via `message_post(attachment_ids=...)`, Odoo's `_process_attachments_for_post` only re-links attachments whose `res_model` is `'mail.compose.message'` or `'mail.scheduled.message'`. Wizard-originated attachments (e.g., `res_model='my.wizard'`) are **not** re-linked and remain orphaned with `res_id=0` after the wizard is garbage-collected.

This causes two problems:

1. **Server-side delete fails**: `_has_attachments_ownership` checks `has_access("write")` on the attachment's `res_model`/`res_id` -- a non-existent wizard record fails this check.
2. **Client-side delete button hidden**: Odoo's JS `showDelete` getter requires `hasTextContent` or 2+ attachments. A `message_post` without `body` and with a single attachment has no delete button. The message itself is also undeletable because `notification` type messages are not editable.

The fix (follows the pattern in `purchase_order.py`):

```python
# Include file names as body so showDelete works for any attachment count
att_names = ", ".join(self.attachment_ids.mapped("name"))
service.message_post(
    body=att_names,
    attachment_ids=self.attachment_ids.ids,
)
# Re-link from wizard to the target record
self.attachment_ids.write(
    {"res_model": service._name, "res_id": service.id}
)
```

**Do not use `message_type='comment'` with `subtype_xmlid='mail.mt_note'`** for these attachment messages. The riverflow mixin's `internal_notes_summary` field picks up all `comment` + `internal=True` messages into the top-3 summary, so file-name listings would pollute it. The default `notification` type is deliberately excluded from both `internal_notes_summary` and `external_messages_summary`. The tradeoff: individual attachment delete buttons work, but the message itself cannot be deleted (notification messages are not editable in Odoo's JS).

Applied in: `riverflow_enter_train_ticket.py`, `riverflow_enter_airline_ticket.py`.

## Translation Method

Use the underscore `_()` method for translatable strings:

```python
_ = self.env._

# Good: plain strings with optional parameters
error = _('This record is locked!')
error = _('Record %s cannot be modified!', record)
error = _('Record %(name)s is invalid.', name=record.name)

# Bad: formatting outside translation
error = _('Record %s cannot be modified!') % record  # loses fallback
error = _('Record %s cannot be modified!' % record)  # string not extracted

# Bad: dynamic strings
error = _("'" + question + "' is invalid")  # can't be translated

# Bad: translating field values (done automatically by framework)
error = _('Product %s out of stock!') % _(product.name)  # useless
```

Use `%` formatting over `.format()` for single variables, and named parameters `%(name)s` for multiple variables to help translators.

## Activating a Language for Translated Names

When code needs translated field values (e.g., German country names via `country.with_context(lang='de_DE').name`), the target language must be activated and its base translations loaded. Odoo ships `res.lang.csv` with all languages but only a few are active by default.

**Activation pattern** (use in post-migration or post_init_hook):

```python
from odoo import api, SUPERUSER_ID

def _activate_german_language(env):
    """Activate de_DE and load base translations for German field values."""
    ResLang = env["res.lang"]
    if not ResLang.search([("code", "=", "de_DE"), ("active", "=", True)]):
        ResLang._activate_lang("de_DE")
        # Load .po files for 'base' module only — enough for country/currency names.
        # Loading all modules is expensive; scope to base for efficiency.
        env["ir.module.module"]._update_translations(["de_DE"], modules=env["ir.module.module"].search([("name", "=", "base")]))
```

**Key details**:

- `res.lang._activate_lang('de_DE')` flips the `active` flag on the existing `de_DE` row (it exists in `base/data/res.lang.csv` but is inactive by default)
- `ir.module.module._update_translations(['de_DE'])` loads `.po` translation files. Without the `modules` kwarg, it loads translations for ALL installed modules (slow). Scoping to just `base` is enough for country names, currencies, etc.
- Place in both **migration** (for existing databases on `-u`) and **post_init_hook** (for fresh installs / test databases)
- Wrap in an idempotent check (`search` for active `de_DE`) so re-runs are safe

**Usage** — reading German translations at runtime:

```python
german_name = country_record.with_context(lang="de_DE").name
```

Applied in: `crewradar_cuneus_sign` (AUV DTO needs German country names, migration `19.0.3.69` + `_activate_german_language` in `hooks.py`).

## Injecting index_content on ir.attachment for PDFs

`ir.attachment.index_content` is `readonly=True` and `_index()` returns `None` for PDFs. To store pre-extracted text (e.g. from LLM OCR) during attachment creation:

1. Override `_get_datas_related_values` on `ir.attachment` to check for a context key:

```python
class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    def _get_datas_related_values(self, data, mimetype):
        caller_index = self.env.context.get("force_index_content")
        result = super()._get_datas_related_values(data, mimetype)
        if caller_index:
            result["index_content"] = caller_index
        return result
```

1. Pass the text via context on create:

```python
IrAttachment = self.env["ir.attachment"]
if extracted_text:
    IrAttachment = IrAttachment.with_context(force_index_content=extracted_text)
attachment = IrAttachment.create({...})
```

1. For post-creation writes, use direct property assignment (bypasses readonly):

```python
link.attachment_id.index_content = cleaned_text
```

This pattern is used by `rivercreds` to store LLM OCR text on credential attachments. The `credential.document_content` stored compute depends on `credential_attachment_ids.attachment_id.index_content` for full-text search aggregation.

## Inheriting Transient Wizards Need Their Own `ir.model.access`

A wizard created with `_name = "x.new.wizard"` **and** `_inherit = "base.wizard"` is a **new model** (prototype inheritance), not an extension of the base. It does **not** inherit the base wizard's `ir.model.access` rules — it needs its own row, or non-superuser users hit *"You are not allowed to access '…' (x.new.wizard). No group currently allows this operation."*

Grant access matching the sibling wizards in the same flow (auto-generated model xmlid is `model_` + the dotted name with dots → underscores):

```csv
crewradar_cuneus_sign.access_crewradar_cuneus_sign_wp_waive_wizard,access_crewradar_cuneus_sign_wp_waive_wizard,crewradar_cuneus_sign.model_crewradar_cuneus_sign_wp_waive_wizard,base.group_user,1,1,1,1
```

This is easy to miss because **tests run as superuser, which bypasses ACLs entirely** — a wizard test that passes in CI can still fail for real users. See [testing-guidelines.md](testing-guidelines.md#acls-are-bypassed-by-superuser--add-a-non-superuser-access-test) for the regression-test pattern. Real example: `crewradar_cuneus_sign.wp.waive.wizard` (Waive Work Permit Renewal) shipped without an ACL; the gap only surfaced when an HR user opened the transition. Second example: `crewradar_cuneus_sign.appointment.email.wizard` (Request AB) — same gap, caught by a `with_user(base.group_user)` create test.

## Subclassing a wizard into a new model — re-declare inherited Many2many fields

Giving a wizard a **new `_name`** with `_inherit = "some.base.wizard"` (prototype inheritance) creates a **separate model** that **copies** every field of the base, including its Many2many fields. Two failure modes appear at registry setup (`_setup_fields`), *before* any test runs:

1. **`Table name '…_rel' is too long`** — a base m2m **without** an explicit `relation=` derives its junction-table name from the *new* model's table (`{model_table}_{comodel_table}_rel`). Long client model names (e.g. `crewradar_cuneus_sign_appointment_email_wizard`, 46 chars) push this past Postgres's **63-char** limit.
2. **`Many2many fields A and B use the same table and columns`** — a base m2m **with** an explicit `relation=` (shared table name) can't be reused by a second model, because the `column1` FK would reference two different models.

**Fix:** re-declare the offending inherited m2m fields on the subclass with a **short, unique explicit `relation=`** (and `column1`/`column2`), keeping any inherited `compute`. Only m2m fields need this; auto-named m2m to short comodels that still fit, and non-stored computed m2m (no table), are fine. Example — `crewradar_cuneus_sign.appointment.email.wizard` subclassing `riverflow.service.email.sender.wizard` re-declares `records_to_transition_ids`, `tag_ids`, and `attachment_ids`:

```python
records_to_transition_ids = fields.Many2many(
    "riverflow.service",
    relation="appt_email_wiz_service_rel",
    column1="wizard_id", column2="service_id",
    string="Services to Transition",
)
```

The base `riverflow.service.wizard` even flags `records_to_transition_ids` as `# to be overridden` for this reason. (Also add the new model's `ir.model.access` row — see the section above.)

**The form view must be `mode="primary"`.** When the subclass reuses the base wizard's form via `inherit_id`, set `<field name="mode">primary</field>` on the view. A default (extension) inheritance is resolved against the **parent view's model**, so the web client silently **drops the subclass's own fields** (they don't exist on the parent model) — the wizard opens but the new fields are invisible, even though a server-side `env[child].get_view(view_id=...)` resolves them (it validates against the child model, masking the bug). Real example: `view_wp_request_ab_wizard_form` inherits the primary `view_service_email_sender_form` for the `crewradar_cuneus_sign.appointment.email.wizard` model; without `mode="primary"` its `appointment_date`/`appointment_time` never rendered. The sibling `view_wp_at_applied_wizard_form` / `view_wp_upload_permit_wizard_form` show the correct pattern (`mode="primary"` + `inherit_id`). Extension mode only works when the parent view shares the same model (or its base) — e.g. Book AB, whose parent is itself a `riverflow.service.wizard` extension.

## Odoo 19 Field Rename: `res.users.groups_id` → `group_ids`

In Odoo 19 the user↔groups Many2many is `group_ids` (with `all_group_ids` = explicit + implied). The old `groups_id` raises `ValueError: Invalid field 'groups_id' in 'res.users'`. When creating a user in code or tests:

```python
# Odoo 19
user = self.env["res.users"].create({
    "name": "Test User", "login": "test_user",
    "group_ids": [Command.set([self.env.ref("base.group_user").id])],
})
```

## Frontend (OWL) Patterns

### Async work must check the component is still alive

`useService("orm")` returns a **protected** service: once the component is destroyed, every call rejects with `Component is destroyed` (before the call) or never resolves (after it). `useService("http")` is **not** protected, and neither is `useFileUploader`, which sits on it. So an async chain that starts on `http` and later touches `orm` can run its second half on a dead component. The stock case is `FileInput` + `Many2ManyBinaryField`: the file posts through `http`, and when the response lands after the dialog closed, `onUpload` links the attachment through the form controller's ORM and raises `Component is destroyed`; a second `TypeError: Cannot set properties of null (setting 'value')` follows because OWL nulls `useRef` elements on destroy. Users saw it as a red "Odoo Client Error" popup after clicking OK, Send, Import or Escape before the file tile appeared, and the file was lost (three wizards, June to September 2026).

Two rules, both in `riverflow/static/src/patch/many2many_binary_upload_patch.js`:

- **After an unprotected await, check `status(this) === "destroyed"`** (`status` from `@odoo/owl`) before touching props, refs or the record; on a dead component report (the `notification` service is unprotected and still works) and return.
- **Block the UI for the whole modal-length operation** (`useService("ui")`, `block()` in a `try`, `unblock()` in `finally`): the overlay stops clicks and the hotkey service ignores Escape while blocked, so the dialog cannot be closed under the operation in the first place.

Proof pattern: a browser tour that holds the upload response (patch `browser.fetch` from `@web/core/browser/browser`, not `window.fetch`, which is bound at module load), closes the dialog under it, and asserts the warning and no error dialog (`rivercreds/static/tests/tours/document_import_upload_race_tour.js`). The upstream fix, a `status` check in `FileInput.onFileInputChange` with a Hoot test, is [odoo/odoo#286831](https://github.com/odoo/odoo/pull/286831).
