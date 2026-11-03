# XML Guidelines

XML formatting, naming conventions, and inheritance patterns for Odoo 19.

## Record Format

Use the `<record>` notation with proper attribute ordering:

```xml
<record id="view_id" model="ir.ui.view">
    <field name="name">view.name</field>
    <field name="model">object_name</field>
    <field name="priority" eval="16"/>
    <field name="arch" type="xml">
        <list>
            <field name="my_field_1"/>
            <field name="my_field_2" string="My Label" widget="statusbar"/>
        </list>
    </field>
</record>
```

### Attribute Order

1. `id` attribute first, then `model`
2. For `<field>`: `name` first, then value (in tag or `eval`), then other attributes

### The `<data>` Tag

- Only use `<data>` with `noupdate=1` for non-updatable data
- If entire file is non-updatable, set `noupdate=1` on `<odoo>` tag instead
- In Odoo 19: no `<data>` wrapper needed for regular data

```xml
<!-- Non-updatable data -->
<odoo noupdate="1">
    <record id="..." model="...">
        ...
    </record>
</odoo>

<!-- Regular data (no wrapper) -->
<odoo>
    <record id="..." model="...">
        ...
    </record>
</odoo>
```

## Shortcut Tags

Prefer these over verbose `<record>` notation:

```xml
<!-- menuitem shortcut -->
<menuitem
    id="model_name_menu_root"
    name="Main Menu"
    sequence="5"
/>

<!-- template shortcut (for QWeb views) -->
<template id="portal_my_home" inherit_id="portal.portal_my_home">
    ...
</template>
```

## XML ID Naming Conventions

### Views

Pattern: `<model_name>_view_<view_type>`

```xml
<record id="sale_order_view_form" model="ir.ui.view">
    <field name="name">sale.order.view.form</field>
    ...
</record>

<record id="sale_order_view_list" model="ir.ui.view">
    <field name="name">sale.order.view.list</field>
    ...
</record>

<record id="sale_order_view_kanban" model="ir.ui.view">
    <field name="name">sale.order.view.kanban</field>
    ...
</record>

<record id="sale_order_view_search" model="ir.ui.view">
    <field name="name">sale.order.view.search</field>
    ...
</record>
```

### Actions

Pattern: `<model_name>_action` (main), `<model_name>_action_<detail>` (others)

```xml
<!-- Main action -->
<record id="sale_order_action" model="ir.actions.act_window">
    <field name="name">Sales Orders</field>
    <field name="path">sales-orders</field>
    ...
</record>

<!-- Secondary actions -->
<record id="sale_order_action_quotation" model="ir.actions.act_window">
    <field name="name">Quotations</field>
    <field name="path">sales-quotations</field>
    ...
</record>

<!-- Window action with specific view -->
<record id="sale_order_action_view_kanban" model="ir.actions.act_window">
    <field name="name">Sales Kanban</field>
    <field name="path">sales-kanban</field>
    ...
</record>
```

#### URL Path Slug

**Always set a `path` field on every `ir.actions.act_window`** record (and any other client-navigable action). Odoo exposes the action at `/odoo/<path>` in addition to the numeric `/odoo/action-<id>`, giving stable, human-readable URLs that survive database rebuilds and are safe to share in docs, bookmarks, and chat messages.

Slug rules:

- Kebab-case, lowercase, alphanumerics and `-` only
- Short and descriptive, based on the UI label (not the model technical name)
- Unique across the whole database — namespace by UX area when needed (e.g. `credential-plans`, `credential-import-jobs`, `wilma-debug-logs`)
- Do **not** prefix with the addon technical name (no `rivercreds-`, `crewradar-`) unless it matches the user-facing app name (e.g. `wilma-...` is fine because the app menu is "Wilma")
- Don't change an existing slug without a good reason; old links will 404

### Menus

Pattern: `<model_name>_menu`, `<model_name>_menu_<detail>`

```xml
<menuitem
    id="sale_order_menu_root"
    name="Sales"
    sequence="5"
/>

<menuitem
    id="sale_order_menu_orders"
    name="Orders"
    parent="sale.sale_order_menu_root"
    action="sale_order_action"
    sequence="10"
/>
```

**`parent` only on top-level `<menuitem>` nodes**: Odoo validates data XML against `odoo/import_xml.rng`. Nested `<menuitem>` elements (children of another `<menuitem>` in the file) use the `submenuitem` pattern, which does not allow a `parent` attribute. Putting `parent="..."` on a nested node breaks RelaxNG validation; the failure is often reported as `Element odoo has extra content: record` on the first `<record>` in the same file. Fix by either nesting entries as real XML children under the section header menuitem, or by defining the entry as a sibling `<menuitem>` at the root of `<odoo>` (possibly in another XML file) with `parent="module.xml_id_of_section_or_parent"`.

**App icons (`web_icon`)**: A top-level `<menuitem web_icon="module,static/description/icon.png">` in a views file (`noupdate` off) is enough. On `-u`, `_tag_menuitem` writes `web_icon`. `ir.ui.menu.write()` rebuilds `web_icon_data` from the file. Change the filename in XML only when the path itself changes. Do not add a post-migrate that only rewrites `web_icon_data` after a PNG replace — that folder runs once, so the next icon tweak would need another folder.

The enterprise home menu draws the PNG at 70 px with 10 px CSS pad and `object-fit: cover`. Extra pad inside the PNG stacks on that pad and shrinks the mark. Crop so the artwork's own outline is complete (Wilma: original ring inside the square; Oteny cell: left and right points intact). Then let the tile pad.

### Security

| Type | Pattern | Example |
|------|---------|---------|
| Group | `<module_name>_group_<group_name>` | `sale_group_user`, `sale_group_manager` |
| Rule | `<model_name>_rule_<concerned_group>` | `sale_order_rule_user`, `sale_order_rule_company` |

```xml
<record id="sale_group_user" model="res.groups">
    <field name="name">User</field>
    ...
</record>

<record id="sale_order_rule_user" model="ir.rule">
    <field name="name">Sale Order: User</field>
    ...
</record>

<record id="sale_order_rule_company" model="ir.rule">
    <field name="name">Sale Order: Multi-company</field>
    ...
</record>
```

### Name Field Convention

The `name` field should match the XML ID with dots replacing underscores:

```xml
<record id="sale_order_view_form" model="ir.ui.view">
    <field name="name">sale.order.view.form</field>  <!-- dots, not underscores -->
    ...
</record>
```

## Inheritance

### Inheriting Views

Use the **same ID** as the original record (module prefix makes it unique):

```xml
<record id="sale_order_view_form" model="ir.ui.view">
    <field name="name">sale.order.view.form.inherit.my_module</field>
    <field name="inherit_id" ref="sale.sale_order_view_form"/>
    <field name="arch" type="xml">
        <xpath expr="//field[@name='partner_id']" position="after">
            <field name="my_custom_field"/>
        </xpath>
    </field>
</record>
```

**Naming**: Add `.inherit.<module_name>` suffix to the name field.

### Primary Views

New primary views (based on another) don't need the inherit suffix:

```xml
<record id="sale_order_view_form_simplified" model="ir.ui.view">
    <field name="name">sale.order.view.form.my_module</field>
    <field name="inherit_id" ref="sale.sale_order_view_form"/>
    <field name="mode">primary</field>
    <field name="arch" type="xml">
        ...
    </field>
</record>
```

### Cross-Model Primary Views (Child Model Wizards)

When a model uses `_name + _inherit` (creates a new model inheriting from a parent), use `mode="primary"` to base the child's view on the parent's fully resolved view. Odoo resolves the parent view chain **including all extension inherits** (even from other modules), then applies the child's xpaths on top.

This avoids duplicating the parent's field layout in the child view. Without `mode="primary"`, the child must copy-paste all fields from the parent view and every extension module that adds to it — creating a maintenance burden where changes must be applied in multiple places.

```xml
<!-- Parent model view (rivercreds.credential.wizard) gets extensions from
     other modules (crewradar_wilma adds auto_parse, warning gate, etc.).

     Child model (cuneus.wp.upload.permit.wizard) only needs its delta. -->
<record id="view_child_wizard_form" model="ir.ui.view">
    <field name="name">child.wizard.form</field>
    <field name="model">child.wizard.model</field>
    <field name="inherit_id" ref="parent_module.view_parent_wizard_form" />
    <field name="mode">primary</field>
    <field name="arch" type="xml">
        <!-- Only the child-specific additions/overrides here.
             All parent fields + extensions are already in the base. -->
        <field name="parent_field" position="before">
            <field name="child_specific_field" />
        </field>
    </field>
</record>
```

**How it works** (from `ir_ui_view.py`): the `_get_inheriting_views()` recursive CTE collects extension children per level, matching `model` at each level. The parent's extensions (same model as parent) are included in the resolved base. The child's xpaths apply on the combined result.

**When to use**: any wizard or model that uses `_name="child.model", _inherit="parent.model"` and needs a view that should include the parent's view layout plus extensions from sibling modules. Common in this workspace: `cuneus.wp.upload.permit.wizard` inherits `rivercreds.credential.wizard` and uses `mode="primary"` to get credential fields + Wilma AI parsing fields automatically.

**Caveat**: the child view must use `inherit_id` pointing to the **direct parent** view (the one that defines the fields the child needs), not the root ancestor. Extensions are collected per level in the chain.

## Odoo 19 View Syntax

### List Views

```xml
<!-- Odoo 19: use <list>, not <tree> -->
<list string="Orders">
    <field name="name"/>
    <field name="partner_id"/>
    <field name="date_order"/>
    <field name="state" widget="badge"/>
</list>
```

### Form Views

```xml
<form string="Order">
    <header>
        <button name="action_confirm" type="object" string="Confirm"/>
        <field name="state" widget="statusbar"/>
    </header>
    <sheet>
        <group>
            <field name="partner_id"/>
            <field name="date_order"/>
        </group>
        <notebook>
            <page string="Lines" name="lines">
                <field name="line_ids">
                    <list editable="bottom">
                        <field name="product_id"/>
                        <field name="quantity"/>
                    </list>
                </field>
            </page>
        </notebook>
    </sheet>
    <chatter/>
</form>
```

### A field only in debug mode

To hide a field under a condition but show it in debug mode, give the field a
twin with `groups="base.group_no_one"` right after it, as Odoo's own
`mail_template_views.xml` does:

```xml
<field name="daily_prio" invisible="is_deadline_from_appointment" />
<field name="daily_prio" groups="base.group_no_one" invisible="not is_deadline_from_appointment" />
```

- `base.group_no_one` counts only in debug mode (`res.users.has_group`), so
  the twin shows only there, and only one of the two is visible at a time.
- **Odoo 19 keeps the twin in the view of a user without debug mode.**
  `get_view` does not remove a node of a group that the user lacks; it keeps
  it with `invisible="1"`. A test that reads the arch counts the nodes whose
  `invisible` is not `"1"`, not all nodes of the field (riverflow
  `test_deadline_follows_appointment`, 3-Nov-2026).
- Put the twin **after** the first node: an inheriting view that anchors on
  `//field[@name='...']` takes the first match, so its fields keep their
  place.

### Group Layout and colspan

`<group>` uses a 2-column grid layout (label + widget). `<field>` elements automatically span both columns (label in column 1, widget in column 2). Non-field elements like `<div>`, `<separator>`, or alert banners placed directly inside `<group>` default to occupying only one column, causing them to be squeezed into the label area. Always add `colspan="2"` to make non-field elements span the full width:

```xml
<group>
    <!-- Fields automatically span both columns -->
    <field name="partner_id"/>

    <!-- Non-field elements need colspan="2" to span full width -->
    <div class="alert alert-warning" role="alert" colspan="2">
        <strong>Warning:</strong> This is an important notice.
    </div>
    <separator string="Section Title" colspan="2"/>
</group>
```

Without `colspan="2"`, a `<div>` inside `<group>` renders in a single grid cell, appearing visually broken or misaligned.

**Do not put a custom expander in the same group table as a sibling field.** A rollup
(`request_preview`, “N lines, click to open”, Show less) with `col="1"` / `colspan="2"`
can leak that caption into the sibling cell (duplicate heading, odd gap). For full-width
Request/Response: a `<separator>` **outside** the group, then the field in a wrapping
`<div>`. Keep compute helpers on the model for tests. Scope SCSS to that form
(`oteny_bot` 19.0.1.18 on the Bot Activity session form).

### Notebook Tab Toolbars (list tabs)

Every notebook `<page>` that shows an embedded list of records (Services, Logbook, Credentials, Contracts, TFT, Payouts on the Employee / Ship / Log Entry forms) uses **one shared toolbar language**: solid Bootstrap buttons in a flex wrapper. Do **not** use Odoo's `oe_link` text-links for these toolbars — the mix of `oe_link` on some tabs and solid buttons on others was the exact inconsistency this convention replaced.

```xml
<page string="Contracts" name="contracts">
    <div class="d-flex align-items-center gap-2 mb-3">
        <!-- Primary: the single Add/New action for this tab -->
        <button name="action_new_contract" type="object" class="btn btn-primary" icon="fa-plus">
            New Contract
        </button>
        <!-- Secondary: "Full Screen (N)" opens this tab's model as a filtered full list -->
        <button name="action_view_contracts" type="object" class="btn btn-secondary">
            <field name="contract_list_button_caption" string="" />
        </button>
        <!-- Secondary: any navigation/utility action (Crew Planning, Open Credential Planning, ...) -->
        <button name="open_crew_planning" type="object" class="btn btn-secondary" icon="fa-calendar">
            Crew Planning
        </button>
    </div>
    <field name="crewradar_contract_ids" widget="riverflow_one2many">
        <list create="0" delete="0" edit="0"> ... </list>
    </field>
</page>
```

Rules:

- **Wrapper**: `class="d-flex align-items-center gap-2 mb-3"` — never a bare `<div>`.
- **Primary** (`btn btn-primary` + `icon="fa-plus"`): the one "Add / New / Calculate" action for the tab. At most one per toolbar.
- **Secondary** (`btn btn-secondary`): the Full Screen (N) button and every navigation/utility action (Crew Planning, Open Credential Planning, View Segments).
- **Filters above an in-form list** are not part of the toolbar: they are oteny_shortcut shortcuts (saved `ir.filters` on the list's model with a Shortcut Seq. and a Show When expression such as `subject == 'hr.employee'`), rendered by the x2many widget itself. When the toolbar `<div class="d-flex ...">` with buttons stands directly before the field, the widget moves the row into that toolbar at its right end (`order-last ms-auto`); keep the toolbar as the element right before the field for that to work. Otherwise the row sits on its own line above the list. Nothing is added to the arch for them; ship the filters as data (`crewradar/data/riverflow_service_shortcut_filters.xml` for the Services tab) and see [oteny-shortcut](../../oteny-shortcut/SKILL.md). The former Services tab **Show** control (a field in the toolbar behind a `vr` divider with a filter icon, a `<label for>` and a restyled badge widget) was retired in crewradar 19.0.10.69. Two rules from it still hold for any field placed in a flex toolbar: an `<i class="fa-..."/>` needs a `title` (or aria-label), or the view validator logs an accessibility warning; and the field wrapper needs `display: flex; margin-bottom: 0` (Odoo renders `.o_field_widget` as inline-block with a form margin-bottom, which misaligns it next to buttons).
- **Page size of a filtered in-form list**: an inline `<list>` pages at Odoo's default of 40 rows, and a shortcut hides the non-matching rows among the loaded rows, so the list must hold every row on one page. The three `service_ids` lists of the Services tabs set `limit="1000"`, because the busiest production ship has about 440 services. Pick the limit from production counts and leave headroom; do not remove the bound. Do not use `boolean_toggle` for a preference that must be remembered: it autosaves without running the onchange (see [coding-patterns — Per-User Memory](coding-patterns.md#per-user-memory-for-a-non-stored-view-toggle)).

The toolbar *composition* legitimately varies per tab (some tabs have a planning link; the Services tab's filter buttons sit below the toolbar, above the list) — it is the button *styling and spacing* that must stay uniform.

#### The "Full Screen (N)" button

Gives a one-click jump from the embedded (create/edit-disabled) list to the model's full list view, filtered to the record. Three parts:

1. A cosmetic caption `Char` per tab, mirroring `service_list_button_caption`. **No `@api.depends`** (recomputed on render, exactly like the Services pattern — the count refreshes on form reload, not the instant an inline row is added):

   ```python
   contract_list_button_caption = fields.Char(
       "Contract list button caption",
       compute="_compute_contract_list_button_caption",
   )

   def _compute_contract_list_button_caption(self):
       for record in self:
           record.contract_list_button_caption = f"Full Screen ({len(record.crewradar_contract_ids)})"
   ```

2. An action returning the model's list `act_window`, domained to the record. **Reuse** an existing method (`action_view_employee_services`, `action_open_employee_log`, `open_site_log`, `action_view_credentials`, ...) when one already opens that filtered list; otherwise mirror `action_view_employee_services`:

   ```python
   def action_view_contracts(self):
       """Full Screen button on the Contracts tab: open the filtered list."""
       self.ensure_one()
       action = self.env["ir.actions.act_window"]._for_xml_id("crewradar.action_crewradar_contract")
       action["context"] = {"default_employee_id": self.id}
       action["domain"] = [("employee_id", "=", self.id)]
       action["name"] = f"Contracts - {self.name}"
       return action
   ```

3. The button renders the caption by **nesting the field inside it**: `<button ... class="btn btn-secondary"><field name="contract_list_button_caption" string=""/></button>`.

Reference implementation: the Services / Logbook / Credentials / Contracts / TFT / Payouts tabs in `crewradar/views/hr_employee_views.xml` and `crewradar/views/crewradar_site_views.xml`, plus the credential tabs added by `crewradar_creds`.

### Search Views

```xml
<search string="Search Orders">
    <field name="name"/>
    <field name="partner_id"/>
    <filter name="filter_confirmed" string="Confirmed" domain="[('state', '=', 'confirmed')]"/>
    <group expand="0" string="Group By">
        <filter name="group_partner" string="Partner" context="{'group_by': 'partner_id'}"/>
    </group>
</search>
```

## XPath Expressions

Common patterns for view inheritance:

```xml
<!-- After a specific field -->
<xpath expr="//field[@name='partner_id']" position="after">
    <field name="my_field"/>
</xpath>

<!-- Before a field -->
<xpath expr="//field[@name='partner_id']" position="before">
    <field name="my_field"/>
</xpath>

<!-- Replace a field -->
<xpath expr="//field[@name='partner_id']" position="replace">
    <field name="partner_id" readonly="1"/>
</xpath>

<!-- Add attributes -->
<xpath expr="//field[@name='partner_id']" position="attributes">
    <attribute name="invisible">state == 'draft'</attribute>
</xpath>

<!-- Inside a group -->
<xpath expr="//group[@name='main_info']" position="inside">
    <field name="my_field"/>
</xpath>

<!-- Target by position (use sparingly) -->
<xpath expr="//field[@name='name']/following-sibling::field[1]" position="after">
    <field name="my_field"/>
</xpath>
```

## Field Widget Gotchas

### JSON Fields

`fields.Json` fields must use `widget="json"` in form views. Do **not** use `widget="ace"` — the ace editor expects string input and calls `.toString()` on JavaScript objects, rendering `[object Object]` instead of the actual JSON content.

```xml
<!-- Correct: json widget serializes via JSON.stringify() -->
<field name="my_json_field" widget="json" />

<!-- Wrong: ace widget can't handle parsed JSON objects -->
<field name="my_json_field" widget="ace" options="{'mode': 'javascript'}" />
```

## File Organization

Group records by model where possible. For dependencies between action/menu/views, pragmatic ordering may override this rule.

```xml
<odoo>
    <!-- Views first -->
    <record id="model_view_list" model="ir.ui.view">...</record>
    <record id="model_view_form" model="ir.ui.view">...</record>
    <record id="model_view_search" model="ir.ui.view">...</record>

    <!-- Then actions -->
    <record id="model_action" model="ir.actions.act_window">...</record>

    <!-- Then menus -->
    <menuitem id="model_menu" action="model_action" .../>
</odoo>
```
