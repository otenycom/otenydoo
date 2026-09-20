---
name: oteny-shortcut
description: Generic view enhancements module. Provides (1) a shortcut banner of saved Favorite buttons above every multi-record view (list, kanban, calendar, pivot, graph, gantt and custom views; no js_class needed), the same buttons above lists inside a form (x2many fields in list mode) where they narrow the rows to the filter's domain, and a Show When expression on the filter (subject, view, field) that says where a button shows — all extending ir.filters with shortcut fields; and (2) a per-form Form Wide Toggle that flips form views between the default 1400px-capped layout and a full-width layout with chatter below the sheet, with per-model localStorage persistence and a system-parameter on/off switch. Use when configuring filter shortcuts, adding filter buttons above an in-form list, storing view layouts into shortcuts, defaulting specific forms to wide layout, or extending these systems.
---

# View Enhancements (oteny_shortcut)

This module bundles two independent generic UI enhancements that improve daily use of Odoo's stock views:

1. **View Shortcuts** — quick-access buttons for saved Favorites: a banner above every multi-record view, and a button row above lists inside a form.
2. **Form Wide Toggle** — per-form switch between narrow (default 1400px-capped) and wide (full-width sheet, chatter below) layouts, with persistence per model.

## When to Use This Skill

- **Key Users**: Understanding the shortcut buttons above a view or above a list in a form, or the wide/narrow toggle on form views
- **Developers**: Adding filter buttons above an in-form list, letting a custom view store a layout in a shortcut, defaulting a specific form to wide layout, extending either system
- **AI Agents**: Setting up shortcut configurations (sequence, Show When, default), or applying the `o_form_default_wide` marker to forms with wide tables

---

# Feature 1: View Shortcuts

## Why This Exists

Users of views like the Radar screen often work with a small set of preferred filters, where each filter is designed for a particular view (list or calendar). Without shortcuts, switching between these requires opening the search dropdown, selecting a filter, and then clicking the view toggle — every time. The shortcut banner eliminates this friction by placing the most-used filter+view combinations as one-click buttons directly above the view content.

The same need exists inside a form: a person looking at the services of an employee, the credentials of a ship or the timesheets of an employee wants one-click filters above that list. Crewradar first built that once, in Python, for the Services tab (three fixed states in a mixin, a widget, a stylesheet and a per-user settings field). Thijs judged that too rigid, because an admin could not add or change a state (2026-09-14). Now every in-form list is configurable the same way the banner is: a saved filter with a sequence is a button above every list of its model inside any form, and its **Show When** expression says in which forms (the services filters under an employee differ from those under a log entry).

## How It Works for Users

Shortcuts are built on top of Odoo's existing **Favorites** system. Any saved Favorite (filter) can be promoted to a shortcut button by setting a sequence number on it. The button then shows everywhere: in the banner above the model's views and above every list of the model inside a form. Its **Show When** expression narrows that.

### The banner above a view

The banner shows on **every multi-record view** of a model that has shortcut favorites: list, kanban, calendar, pivot, graph, gantt and custom views such as the Credential Planning timeline. No view has to be prepared for it. Form views and pop-up dialogs (Search More...) never show it.

When you click a shortcut button:
- The corresponding filter is activated (just like selecting it from the Favorites dropdown)
- Any previously active filters are cleared
- If the shortcut has a preferred view type, the view switches automatically — but only when the current screen's action actually offers that view; otherwise the filter is applied without switching (so a shortcut whose view is unavailable on the current screen never errors)
- The clicked button is highlighted to show it's active; clicking it again deactivates the filter

### The button row above a list inside a form

A shortcut shows as a button above **every list of its model inside a form** where its Show When expression is true (or empty), for example the services list on the employee, ship, log entry and service forms. A list in kanban mode gets no row.

- **One button is active at a time.** A click narrows the list to the rows the filter matches; the other rows are hidden. Clicking the active button again shows every row. There is no separate "All" button.
- **The Default Filter is active when the form opens**, on every open. The last click is not remembered anywhere (decision, 2026-09-14: a filter click is a view preference of the person looking, not a change to the record; a memory cost two builds to get right in the earlier Python version and was dropped).
- **A click never changes the record.** The form does not show the save / discard buttons after a filter click, and a row added while a filter is active stays visible until the list reloads.
- **While a filter is active the list holds every row**, so the result is complete and there is no pager; switching the filter off restores the page size of the view.

### Setting up a shortcut yourself: the Shortcut group on the filter

The settings live on the favorite's own form, in a **Shortcut** group, in Odoo's own words and with a short explanation on the form. There is no button or gear for it in the views (Thijs, 2026-09-15: "it should just be when editing the filter"). A user reaches the form the way Odoo offers: **Edit** in the Favorites panel right after saving a search opens the new favorite's form, and **Settings > Technical > User-defined Filters** opens any favorite the user may change.

| Field | Meaning |
|---|---|
| **Show as a button** | Off: the favorite stays in the Favorites menu only. On: a new button goes last, after the model's other buttons |
| **Show in** | **Everywhere**: in the views and inside the forms. **Views only**: the list, kanban and calendar views you open from a menu. **Forms only**: the lists inside forms, for example the Services tab of an employee. **One form only**: pick the form below. **Custom rule**: an expression of your own, for administrators |
| **Form** | For One form only: the form whose list shows these records, e.g. Employee. The picker offers the models whose form view lists the records, preferring those a menu opens |
| **Icon** | The icon on the button, chosen from the searchable icon list (see The Shortcut Icon chooser) |
| **Shortcut View** | Banner only: the view to switch to on click |
| **Default Filter** | Odoo's own flag, on the same form: switched on when the view or the form opens, where the button shows |

The choice writes the filter's Show When expression; the expression, the position and the stored layout stay on the form for administrators in debug mode. A change shows after the page is reloaded. Who may change a favorite follows Odoo's own rule: your own favorites and the favorites shared with all users; a Settings administrator, every filter.

### Where the row sits inside a form

When a toolbar of buttons stands directly above the list (the crewradar tabs: **Add Service**, **Full Screen**, ...), the row moves into that toolbar, at its right end, so the filters share the line with the actions (Thijs, 2026-09-15). Without such a toolbar the row stays on its own line above the list.

### Show When: where a button shows

**Show When** is a Python expression on the filter, evaluated in the browser at each place a button could appear. Empty means everywhere. It governs the **buttons only**: the favorite itself stays in the Favorites menu of the model's views. One rule follows from that: **a shortcut's Default Filter applies only where its expression is true**, so a filter meant for the lists inside a form does not narrow the model's top-level views at open, while it is still the default inside those forms.

| Name | Inside a form | On a top-level view |
|---|---|---|
| `subject` | model of the record the list belongs to, e.g. `'hr.employee'` | `False` |
| `subject_id` | id of that record, `False` on a new record | `False` |
| `field` | the list's field name, e.g. `'service_ids'` | `False` |
| `view` | `'form'` | the view type: `'list'`, `'kanban'`, `'calendar'`, ... |
| `uid`, `context` | the user | the user |

Examples: `subject == 'crewradar.log.entry'`; `subject in ('hr.employee', 'crewradar.site')`; `view == 'calendar'`; `not subject` for a banner-only button. The **Show in** choice on the filter form writes these for the usual cases (Everywhere = empty, Views only = `not subject`, Forms only = `view == 'form'`, One form only = `subject == '<model>'`); anything else reads as **Custom rule**, and the expression is edited in debug mode. On the filter form the expression sits in its own **Show When** group next to **Shortcut Banner**: the list of names and the examples above, then a code box (Python) for the expression (Thijs, 2026-09-15). The server refuses an expression that does not parse. A name the browser does not know hides the button and logs a warning in the console (hidden is the safe direction, because the default follows the answer).

### Setting Up Shortcuts (technical form)

The same form, opened from the technical menu. In debug mode it also shows the expression, the position and the stored layout:

1. First, create or locate a saved Favorite in the search bar of the target view — or create the filter directly in step 2 (a form-only filter needs no view)
2. Go to **Settings > Technical > User-defined Filters**
3. Find the filter and set these fields:
   - **Shortcut Seq.** — any number greater than 0 (controls display order; lower numbers appear first)
   - **Show When** — optional, its own group with the explanation and a code box: the expression that says where the button shows (see above); empty = everywhere
   - **Default Filter** — Odoo's own flag; for a shortcut it applies where its button shows: applied at view open on a top-level view, active when the form opens inside a form
   - **Shortcut View** — optional, banner only: the view to auto-switch to on click. The dropdown lists only the view types that exist for the filter's model (e.g. List + Calendar for credentials; List + Credential Planning timeline for credential planning). Leave empty to stay on the current view
   - **Shortcut Icon** — optional: the icon of the button, chosen from a searchable list of every Font Awesome icon this backend has loaded. Type a word (`clock`, `ship`, `gear`) and pick the icon; there is no class to remember
4. Reload the page (F5) — the button appears. The banner refreshes with the view; the in-form row is loaded once per model per page load.

### Who Sees Which Shortcuts

Shortcut visibility follows the same rules as Odoo Favorites:
- A Favorite shared with **all users** (empty "Shared with" field) appears as a shortcut for everyone
- A Favorite shared with **specific users** appears only for those users

This means teams can have shared shortcuts (e.g., "Pending" for everyone) while individual users can also promote their personal favorites to shortcuts.

For the banner, a favorite saved from a specific action shows in that action only, like the Favorites menu. Inside a form there is no action, so a shortcut applies at **model level**, whatever action it was saved from; only its Show When expression narrows it.

### Active State

A banner button is highlighted only when its filter is actually active. If you change filters manually via the search bar, or toggle a shortcut off by clicking it again, the highlighting updates immediately. In a form, the highlighted button is the one whose filter narrows the list.

### Shortcut Fields Reference

| Field | Purpose |
|-------|---------|
| **Shortcut Seq.** | 0 = not a shortcut; >0 = show as button, ordered by value |
| **Show When** | Python expression: where the button shows (`subject`, `subject_id`, `field`, `view`, `uid`, `context`). Empty = everywhere. Must parse |
| **Default Filter** | Odoo's own flag. For a shortcut it applies only where Show When is true: applied at view open on a top-level view, active when the form opens inside a form |
| **Shortcut View** | Banner only. Preferred view to switch to on click; the options are scoped per model to the view types that model actually has (List, Calendar, and custom views such as the Credential Planning timeline). Empty = stay on current view |
| **Shortcut Icon** | Font Awesome icon of the button, picked from a searchable list (widget `fa_icon`). Stored as the bare class, e.g. `fa-clock-o` |
| **Shortcut Layout** | Banner only. JSON blob storing view layout state (column selection/widths for list, scale/weekends for calendar, period for the timeline); populated via the "Store Layout" button |

**Button icons**: A shortcut button shows the optional **Shortcut Icon** (the Font Awesome class you set) at full strength, plus, in the banner, a faded icon for its target **Shortcut View** type. The view-type icon is resolved automatically from Odoo's per-view-type icon map (the same icons the view switcher uses), so custom view types show their registered icon with no extra configuration.

### Store Layout

Each banner shortcut can store a **view layout** so that clicking the shortcut not only activates its filter but also restores the view to a specific column arrangement (list), calendar display (calendar) or period (timeline). This is useful when different shortcuts are designed for different tasks that benefit from different visible columns or calendar scales.

#### Saving a Layout

1. Click the shortcut you want to configure — it highlights as active
2. Arrange the view the way you want:
   - **List view**: show/hide optional columns via the column menu (the kebab icon at the top-right of the list), and optionally resize columns by dragging column borders
   - **Calendar view**: select the desired scale (Day, Week, Month, Year) and toggle "Show weekends" on or off
3. Click the **Store Layout** button (save icon) that appears at the right end of the shortcut banner — it is only visible when exactly one shortcut is active and the view has a layout to store (list, calendar, the credential timeline)
4. A confirmation dialog shows a summary of the captured settings — click **Confirm** to save, or **Cancel** to discard

The layout is stored as a JSON blob in the **Shortcut Layout** field on the filter record (visible under Settings > Technical > User-defined Filters).

#### What Gets Saved and Restored

| View Type | Captured State | Restore Behavior |
|-----------|---------------|-----------------|
| **List** | Which optional columns are visible; relative width of each column (as ratios that scale across screen sizes) | Column selection is written to `localStorage` (same mechanism Odoo uses natively); width ratios are applied to column headers after render |
| **Calendar** | Current scale (day/week/month/year); whether weekends are shown | Scale and weekend visibility are set on the calendar controller |

#### When the Layout Is Applied

- **On shortcut click**: the stored layout is applied immediately after the filter is activated
- **On page load**: if a shortcut with stored layout is already active (e.g., from a bookmarked URL or default filter), the layout is applied automatically during initial render — no click needed
- **On deactivation**: when the shortcut is toggled off or a different shortcut is selected, the stored layout state is cleared so it does not interfere with manual column adjustments

#### Period: fixed dates or relative to today (timeline views)

When the captured layout carries a custom period (`range_id: custom` with `start_date`/`stop_date` — the Credential Planning timeline), the wizard shows a **Period** choice:

- **Fixed dates** (default) — the shortcut always lands on the captured dates.
- **Relative to today** — the shortcut lands on a window that moves with the calendar: **Start month offset** (0 = this month, -1 = last month, 3 = three months ahead) and **Length (months)**, counted in whole months from the start of today's month. Both are prefilled from the captured window (a window that is not month-aligned snaps to whole months) and stay editable; the preview shows the dates the choice resolves to as of today. Confirm stores `relative_period` instead of the dates.

A Monthly or Weekly scale has no Period choice: it already means "the default window around today". The preview also lists any other captured key as-is (for example the timeline `grouping_mode`), so it never reports "No layout data captured" for a layout that carries data.

#### Overwriting a Layout

To update a shortcut's layout, simply arrange the view as desired and click **Store Layout** again. The new layout replaces the previous one. To remove a stored layout entirely, clear the **Shortcut Layout** field on the filter record.

#### Layout JSON Schema

- List: `{"optional_columns": ["field_a", "field_b"], "column_widths": {"field_a": 0.30, "field_b": 0.70}}`
- Calendar: `{"scale": "week", "show_weekends": true}`
- Credential Planning timeline: `{"range_id": "monthly", "grouping_mode": "byEmployee"}` (a scale = its default window around today), `{"range_id": "custom", "start_date": "2026-10-01", "stop_date": "2026-12-31", "grouping_mode": "byShip"}` (fixed period), or `{"range_id": "custom", "relative_period": {"start_month_offset": -1, "months": 6}, "grouping_mode": "byShip"}` (relative period: whole months from the start of today's month; wins over fixed dates stored beside it). The conversions live in riverflow `components/timeline/layout_range.js`; a consuming business documents its own use of these keys in its own skill bundle.

## Decisions (2026-09-14, Thijs, built in oteny_shortcut 19.0.1.220)

1. **The banner shows on every multi-record view without opt-in.** The former `shortcut_list` / `shortcut_calendar` `js_class` views were removed; riverflow's `StateRecordCalendarController` extends Odoo's `CalendarController` again and the rivercreds timeline controller no longer renders the banner itself.
2. **The banner reads its shortcuts from the favorites the view already loads**, so a view open costs no extra request.
3. ~~A saved filter gets a placement: Views (default), Forms, Both.~~ Superseded by decision 9 the same day.
4. **A shortcut applies at model level** — above every list of that model inside any form; the Show When expression narrows it.
5. **Only list-mode x2many fields get the in-form row.**
6. **Filtering is exact and server-checked**: every row is loaded while a filter is active and the rows the filter does not match are hidden. Considered and rejected: paging over the matched ids only (needs private `StaticList` internals and bookkeeping for rows added or removed while filtered).
7. **No memory of the last click**; the Default Filter is active at every open.
8. **No explicit "All" button**; clicking the active button again shows all rows.
9. **Follow-up the same day: no placement field, a "Show When" expression instead.** Thijs: the services filters under an employee differ from those under a log entry, so a shortcut needs a rule for where it shows, not a coarse Views / Forms choice. `shortcut_show_when` is a Python expression evaluated in the browser with `subject` (model of the record the list belongs to, `False` on a top-level view), `subject_id`, `field`, `view` (`'form'` inside a form, else the view type), `uid` and `context`. Empty means show everywhere. The expression governs the **buttons only** (banner and in-form row): the favorite stays in the Favorites menu. Second rule, needed by that choice: a shortcut's **Default Filter applies only where its expression is true**, so a form-only default does not narrow the model's top-level views at open. The service filters ship with `subject in ('hr.employee', 'crewradar.site', 'crewradar.log.entry', 'riverflow.service')`.
10. **Self-service setup (2026-09-15, Thijs).** The expression is too technical for most users, and a favorite has no edit screen in Odoo: only a Settings admin in debug mode could make a favorite a shortcut. Decisions: (a) a **Shortcuts wizard in the style of Store Layout, one favorite at a time**: pick the favorite, then Button on/off, Position, **Show on** as a preset (Everywhere / Screens only / Tabs only / The tab of one record type / Custom rule), Icon, Default; the preset writes the expression, Custom keeps it and shows the code box in debug mode only. Wording (Thijs, 2026-09-15, after "Above the views only" / "Inside forms only", then "Screens only" / "Tabs only", then "record type" all failed): abstract words for the two places are not known to users, so the labels are built from the names of the things themselves — "Only on the Services list", "Only inside an Employee, Logbook Entry, Service or Ship" — from the favorite's model and the models whose form shows a list of it (`_show_preset_selection`, `_subject_models`: relation fields kept only when a form view of their model places the field, and among those the models a menu opens are preferred (`_prefer_models_with_a_menu`); up to five names, then "..."). Known limit: on a crewradar database the services choice also names Credential Plan Item, because its form lists services and it has a planning menu. The order number is hidden; a new button goes last (`_next_sequence`). (b) **The door is a gear that appears when a filter is selected**, nothing else (Thijs, second answer): on a multi-record view the banner shows a gear when exactly one favorite is active in the search, shortcut or not, so a plain favorite can be promoted from there; inside a form the row shows a gear when a button is active. No menu item, no cog-menu entry, no dependency on crewradar; the module stays self-contained. (c) The models offered for "Only in the form of ..." are the models that have a One2many or Many2many field pointing at the filter's model (relation fields; cheap and generic). (d) ~~The Save current search dialog gets a checkbox.~~ Dropped in the second answer: the gear is the only door. (e) The wizard is Python (a transient model and a form view, like Store Layout); the client side is only the two gears and, separately, the row moving into the toolbar line above the list, right-aligned, when such a toolbar sits directly above it. Access follows Odoo's rule: own favorites and favorites shared with all; a Settings admin edits every filter.
11. **No wizard: the settings are fields on the filter form (2026-09-15, Thijs, third answer).** The wizard was a copy of what the filter form can hold, one dialog too many. The gear now opens the favorite's own form (ir.filters) in a dialog, where a **Shortcut** group holds Show as a button, Show in, Form, Icon and the banner fields; the expression and the order number stay for administrators in debug mode. Wording uses Odoo's own terms with a short explanation: **Everywhere**, **Views only** (the list, kanban and calendar views opened from a menu), **Forms only** (the lists inside forms, e.g. the Services tab of an employee), **One form only** (pick the form), **Custom rule**. How things work does not change: the choice writes the Show When expression, a new button goes last, the default applies where the button shows.
12. **No gear either (2026-09-15, Thijs, fourth answer).** The gears in the banner and the in-form row were removed: the settings are changed when editing the filter, through the doors Odoo already has (Edit after saving a search, which opens `base.ir_filters_view_edit_form`; Settings > Technical > User-defined Filters, which opens `base.ir_filters_view_form`). The edit form is a primary inherit of the technical form, so the Shortcut group (an extension of the technical form) appears in both; a test guards it. The banner renders only when the model has shortcut buttons, as before.

---

# Feature 2: Form Wide Toggle

A small square icon button in the top-right corner of every form sheet flips the form between narrow (default 1400px-capped layout, chatter aside on XXL screens) and wide (full-width sheet, chatter below). The user's choice is remembered per model in `localStorage`. Specific forms can default to wide via the `o_form_default_wide` marker class. A System Parameter (`oteny_shortcut.form_wide_toggle`, default `"True"`) globally enables or disables the feature.

The crewradar Employee form ships defaulted to wide because its Logbook, Vacation, Airfare, and Timesheets tabs contain wide tables that need full-width display. Other forms keep the standard narrow layout so the chatter and attachment preview sit aside as usual.

For details — user behavior and configuration, system parameter, defaulting forms via view inheritance, and the technical patches that implement the feature (FormController, FormRenderer, FormCompiler) — see [Form Wide Toggle](references/form-wide-toggle.md).

---

## Technical Reference (View Shortcuts)

### How the banner gets on every view

- `views/layout_patch.xml` extends Odoo's `web.Layout` template and inserts `<ViewShortcutsBanner/>` between the control panel and `<main class="o_content">`, as a row of the action's flex column (so a search panel inside the content is not affected). `views/layout_patch.js` adds the component to `Layout.components`. Every view with a control panel renders `Layout`, hence the banner is everywhere.
- The banner (`components/view_shortcuts_banner/view_shortcuts_banner.js`) decides whether it has anything to show: `isEnabled` needs `env.searchModel`, no `env.inDialog`, and `env.config.viewType !== "form"` (a form has a search model too, for its record pager). It renders nothing when the model has no shortcut favorites.
- **Buttons come from the search model's favorites.** `ir.filters.get_filters` (`models/ir_filters.py`) is overridden: it adds the shortcut fields (`SHORTCUT_FIELDS`) to every favorite it returns and drops none. `views/search_model_patch.js` patches `SearchModel._irFilterToFavorite` to keep those keys as `shortcutSequence`, `shortcutViewType`, `shortcutIcon`, `shortcutLayout`, `shortcutShowWhen`, and to set `shortcutVisible` = sequence > 0 and the Show When expression true for this view (`views/show_when.js`, context `{subject: false, view: env.config.viewType, ...}`). It also patches `_createGroupOfFavorites` to pass a shortcut whose expression is false here with `is_default: false`, so the default does not apply on this view while the favorite stays in the menu. The banner then reads `searchModel.getSearchItems(favorite with shortcutVisible)`, sorted by sequence; each item carries `id` (search item id), `serverSideId` (the `ir.filters` id) and `isActive`. A click is `searchModel.toggleSearchItem(id)`; the banner re-renders on the search model's `update` event.
- **Layout callbacks travel through the environment.** A controller that owns a storable layout calls `useSubEnv({ shortcutLayout: { getViewLayout, applyViewLayout } })`; the banner reads `env.shortcutLayout`. Without it the Store Layout button stays hidden and stored layouts are ignored. `views/list_controller_patch.js` (optional columns + column width ratios, the `useEffect` + `requestAnimationFrame` width restore) and `views/calendar_controller_patch.js` (scale + weekends) patch Odoo's controllers; rivercreds' `RivercredsPlanTimelineController` does the same for the timeline period (crewradar_creds adds the grouping).
- **A custom view needs nothing** to get the banner. It only adds the `useSubEnv` seam above when it has a layout worth storing.

### How the in-form row works

- `views/x2many_field_patch.js` patches `X2ManyField` (so every `one2many` / `many2many` widget and their subclasses, e.g. riverflow's `riverflow_one2many`, get it) and `views/x2many_field_patch.xml` inserts `<X2ManyShortcutsRow/>` before the `ListRenderer` of the `web.X2ManyField` template. In `setup`, a list-mode field loads `ir.filters.get_shortcuts(resModel)` — every shortcut of the model, cached per model for the page load in a module-level `Map` (`loadFormShortcuts`, `clearFormShortcutsCache` for tests). The getter `x2manyShortcuts` keeps the ones whose Show When is true with `{subject: record.resModel, subject_id: record.resId, field: props.name, view: "form"}`, and `onWillStart` activates the `is_default` one among those.
- **Activation** (`_activateShortcut`): if the list has more rows than its page (`count > limit`) or is not on the first page, `list.load({ limit: count, offset: 0 })` loads every row (the x2many pager hides itself once `count <= limit`). Then `_refreshShortcutMatches` evaluates the filter's domain in the browser like a favorite — `Domain.and([[["id","in",ids]], new Domain(shortcut.domain)]).toList(user.context)`, so `context_today()` and `relativedelta` resolve from Odoo's py builtins — and calls `orm.search` on the list's model with it. The server does the matching (dotted paths, record rules). The result becomes `shortcutState.matchedIds` (a `Set`). One request per (filter, row set): a pending request is reused when the effect after a load and the explicit call ask for the same pair; another filter always asks. A result that arrives after the user switched filter is dropped.
- **Hiding** (`views/list_renderer_patch.js`): `ListRenderer.getRowClass` adds `o_x2many_shortcut_hidden d-none` to a row whose `resId` is not in `matchedIds`; an unsaved row (`record.isNew`) always shows. The renderer subscribes to the field's state with `useState(env.x2manyShortcuts)` (the field publishes it with `useSubEnv`). The row loop template is deliberately not touched: project's own list renderers derive from `web.ListRenderer.Rows` with an xpath on that loop, and the field's value must stay untouched so the record never becomes dirty.
- **Reloads**: a `useEffect` on `[list, list.records]` recomputes the matches after a save, an onchange or a record switch. **Deactivation** clears `matchedIds` and restores the view's page size with `list.load({ limit: original, offset: 0 })`.
- **Toolbar placement**: `views/x2many_field_patch.xml` adds `t-ref="x2manyShortcutRoot"` to the field's root; `onMounted`, `findShortcutToolbar(el)` takes the element right before the field's `.o_field_widget` wrapper when it is a `div.d-flex` holding a `button`, gives it an id, and the row is rendered into it with an Owl `t-portal` (Owl resolves the target by selector, so the id) with the classes `order-last ms-auto`; without a toolbar the row renders in place above the list. Owl inserts portal content at the start of the target, hence `order-last`.
- Known limits: footer aggregates still count hidden rows; keyboard row navigation can land on a hidden row; kanban-mode x2many fields get no row; an admin's filter change needs a page reload.

### The Shortcut Icon chooser

The **Shortcut Icon** field is a Char holding a bare Font Awesome class
(`fa-clock-o`), which the button templates render as `fa #{shortcut_icon}`. It
was free text, so setting it meant knowing the Font Awesome names by heart, and
a typo shows no icon and explains nothing. `widget="fa_icon"`
(`static/src/fields/fa_icon/`) replaces the text box with a searchable list of
every icon the page can render.

- **Where the list comes from.** There is no list of icon names to import: in
  the browser the names exist only as CSS rules, `.fa-name::before { content:
  "\fxxx" }`, from the Font Awesome 4.7 stylesheet in `web.assets_backend`. So
  `fa_icons.js` reads `document.styleSheets` once and keeps the result. Odoo
  does the same twice in its own code — the pictogram tab of the media dialog
  (`html_editor/utils/fonts.js`) and the Studio icon picker
  (`web_studio/utils.js`, enterprise only) — but reusing either would make this
  module depend on more than `web`, so the idea is repeated here in a few lines.
- **What counts as an icon.** A rule whose only declared style is `content`, and
  whose selector names the icon class plus at most the base class `fa`
  (`.fa-name::before`, `.fa.fa-name::before`, `.fa-name.fa::before`). The style
  test drops the helper rules (`.fa-lg`, `.fa-spin`, `.fa-stack-1x`); the
  selector test keeps the icons Odoo adds in `fontawesome_overridden.scss` and
  drops the ones it only re-points. One rule can name several classes: Font
  Awesome groups an icon with the older aliases it keeps
  (`.fa-gears:before, .fa-cogs:before`), the aliases first and the current name
  last. The list therefore stores the **last** name and searches on all of them,
  so typing `gears` finds `fa-cogs`.
- **The widget** is Odoo's `SelectMenu` with a `choice` slot, the same shape
  Studio uses: a toggler showing the chosen icon and its class, and a panel with
  a search box over a wrapping grid (the grid is `fa_icon_field.scss`; the panel
  stays 300px tall so it does not resize while you type). `autoSort` is off
  because the list is already ordered by class name, which puts an icon beside
  its variants.
- **The stored value stays the bare class.** A value that carries the base class
  as well (`fa fa-check`, possible from the free-text days) is read as its `fa-`
  part. A class no stylesheet defines is put in front of the list as a choice of
  its own, shown as its text because there is no glyph to show, so the value of
  the record is never hidden and can be cleared.
- The widget is generic (`supportedTypes: ["char"]`), so any Char field that
  holds a Font Awesome class can use it.

### Key Files

```text
oteny_shortcut/
├── __manifest__.py
├── models/
│   └── ir_filters.py                    # ir.filters extension: shortcut fields incl. shortcut_show_when (Text, + parse constraint); the user-facing settings is_shortcut (inverse: _next_shortcut_sequence / 0), shortcut_show_preset + shortcut_subject_model_id (computed from the expression via preset_for_expression; a shared inverse writes it via expression_for_preset), shortcut_subject_model_ids (_shortcut_subject_models: forms that list the records, _prefer_models_with_a_menu); get_filters() override (shortcut fields on every favorite), get_shortcuts(res_model), dynamic _shortcut_view_type_selection() + per-model shortcut_view_type_whitelist compute
├── security/
│   └── ir.model.access.csv             # Access rights for the store layout wizard
├── wizard/
│   ├── store_layout_wizard.py           # Transient model: captures layout, writes to ir.filters on confirm; Period choice (fixed / relative_period in whole months) for a captured custom period; summary lists unknown keys generically
│   └── store_layout_wizard_views.xml    # Wizard form view (target='new' dialog)
├── views/
│   └── ir_filters_views.xml             # Form/list/search view extensions: group Shortcut (explanation, Show as a button, Show in radio + explanation, Form picker, Icon with widget="fa_icon", Shortcut View with widget="filterable_selection" + whitelist_fname, Layout and position in debug mode) and group Show When (debug only: the names, then the expression in widget="code" mode python)
├── tests/
│   ├── test_store_layout.py             # Wizard confirm, summary, get_shortcuts layout
│   ├── test_shortcut_show_when.py       # Show When parse constraint, get_filters carries the shortcut fields on every favorite, get_shortcuts returns every shortcut of the model (action ignored)
│   └── test_shortcut_settings.py        # Preset round trip, settings read the stored values, the switch gives the last position, the choice writes the expression, forms offered, own/shared/other's-private access
└── static/
    ├── src/
    │   ├── components/
    │   │   ├── view_shortcuts_banner/           # Banner: buttons from searchModel favorites, toggle + guarded switchView, Store Layout via env.shortcutLayout; scss shared with the in-form row
    │   │   ├── x2many_shortcuts_row/            # In-form button row (presentation only); inToolbar prop for the toolbar placement
    │   │   └── form_wide_toggle/                # Feature 2
    │   ├── fields/
    │   │   └── fa_icon/                         # The Shortcut Icon chooser: fa_icons.js reads the icon classes from the stylesheets, fa_icon_field.js is the widget="fa_icon" on a Char field
    │   └── views/
    │       ├── layout_patch.js / .xml           # Banner inserted into web.Layout on every view
    │       ├── show_when.js                     # showWhenContext() + isShortcutShown(): the expression's names and its evaluation
    │       ├── search_model_patch.js            # Favorites keep the shortcut fields, shortcutVisible per view, default cancelled where hidden
    │       ├── list_controller_patch.js         # List layout (optional columns, widths) + env.shortcutLayout
    │       ├── calendar_controller_patch.js     # Calendar layout (scale, weekends) + env.shortcutLayout
    │       ├── x2many_field_patch.js / .xml     # In-form row: load Forms shortcuts, activate default, evaluate domain, orm.search, load all rows
    │       └── list_renderer_patch.js           # Hide non-matching rows via getRowClass
    └── tests/                                    # Hoot tests (web.assets_unit_tests)
        ├── x2many_shortcuts.test.js             # In-form row: default at open, subject/field/view expression, hidden and broken expressions, switch, switch off, page size, no shortcuts
        ├── view_shortcuts_banner.test.js        # Banner: order, view/subject expressions, a hidden shortcut's default not applied but still a favorite, toggle, one active, no banner on forms / without shortcuts
        ├── x2many_toolbar.test.js               # The in-form row inside the toolbar above the list (order-last ms-auto), still filtering
        └── fa_icon_field.test.js                # Icon chooser: reading the icon classes from the stylesheets (aliases, duplicates, helper rules), search and pick, an unknown class stays visible and can be cleared
```

### Server API

- `ir.filters.get_filters(model, action_id, embedded_action_id, embedded_parent_res_id)` — Odoo's favorites loader, extended: every dict also carries `shortcut_sequence`, `shortcut_view_type`, `shortcut_icon`, `shortcut_layout`, `shortcut_show_when`. Nothing is dropped; the browser decides per view.
- `ir.filters.get_shortcuts(res_model)` — every shortcut of the model (`shortcut_sequence > 0`, the favorite visibility rule `user_ids` empty or containing the user, embedded filters excluded, no action condition). Returns `name`, `domain`, `is_default` and the shortcut fields, ordered by sequence then name. Used by the in-form row only.
- `_check_shortcut_show_when` — a `ValidationError` when the expression does not parse (`odoo.tools.safe_eval.test_python_expr`).

### Shortcut View Selection (per-model)

`shortcut_view_type` on `ir.filters` is a **dynamic selection** — `_shortcut_view_type_selection()` reads `ir.ui.view`'s own `type` selection (via `fields_get`) and returns every switchable view type, excluding `form`/`search`/`qweb`. Custom view types (e.g. `rivercreds_plan_timeline`) appear automatically once they are registered on `ir.ui.view.type`; **no per-module `selection_add` on this field is needed**.

The field renders with `widget="filterable_selection"` and `options="{'whitelist_fname': 'shortcut_view_type_whitelist'}"`. `shortcut_view_type_whitelist` is a `Json` compute (`@api.depends('model_id')`) returning the switchable view types that have at least one `ir.ui.view` for the filter's model — so the dropdown only offers views the model actually has. The widget always keeps the currently-stored value as an option even if it falls outside the whitelist. The whitelist field must be present (invisible) in the form so it loads into `record.data`.

This whitelist is per-**model**; actual view availability is per-**action**. So `onShortcutClick` also guards `switchView`: it switches only when the target type is in the current action's `env.config.viewSwitcherEntries`, otherwise it applies the filter in place. Together these prevent `ViewNotFoundError` from a favorite whose preferred view isn't exposed by the screen it's used on.

### Layout Auto-Apply Mechanism

`_applyActiveShortcutLayout()` runs in the banner's `onWillStart` (initial page load) and on every SearchModel `update` event. It tracks the last-applied `serverSideId` (`_lastAppliedLayoutId`) to avoid redundant re-application on unrelated updates (sort, paginate); a layout is applied only when the single active shortcut with a stored layout *changes*, and `applyViewLayout(null)` is called when it goes away. The callbacks are `env.shortcutLayout.getViewLayout` / `applyViewLayout` (see above).

### Running the tests

- Python: `--test-tags test_store_layout` (both test classes carry that tag) with `-u oteny_shortcut`.
- Hoot: `--test-tags "/web:WebSuite.test_unit_desktop[@oteny_shortcut]"` on cr-test (Odoo drives its own headless Chrome; `@oteny_shortcut` is the module suite, `@oteny_shortcut/x2many_shortcuts` a file). A test of a door mocks the action service (`mockService("action", { doAction })`) and checks the context of the act_window. Use a free port when a previous run has not released its port yet. A test that mounts a form must answer `ir.config_parameter.get_param` with `onRpc`, because the Form Wide Toggle asks for its system parameter on every form open and the mock server has no such model. A test that mounts a list must define `webModels` (`ResCompany`, `ResPartner`, `ResUsers`) next to its own models, because the list controller asks `res.users` for the export group.

### Dependencies

- Depends only on `web` (no Odoo app dependencies)
- Consumer modules (riverflow, rivercreds, crewradar) add `oteny_shortcut` to their `depends`; they need no view changes for the banner. A business module can ship Forms shortcuts the same way; a consuming business documents its own worked example in its own skill bundle.

## References

- [Form Wide Toggle](references/form-wide-toggle.md) — User behavior, system parameter, default-wide marker class, and the FormController/FormRenderer/FormCompiler patches that implement the feature
