# Radar & State Records

> How the Radar view works, and how state records bridge workflow-enabled models to the global Radar query interface.

## Overview

The **Radar** is a global task/status overview screen accessible from the top-level "Radar" menu (`/odoo/radar`). It shows a unified list of all workflow-enabled records across different models (log entries, services, employees, ships) so users can see what needs attention without navigating into each module separately.

The Radar is powered by `riverflow.state.record` -- a denormalized model that mirrors key fields from the actual records. Each workflow-enabled record creates one or more state records, and the Radar view queries `riverflow.state.record` exclusively.

## Architecture

```text
                ┌──────────────────────────┐
                │    Radar View (UI)       │
                │  riverflow.state.record   │
                │  - list/gantt/calendar    │
                └──────────┬───────────────┘
                           │ queries
         ┌─────────────────┼──────────────────┐
         │                 │                  │
  ┌──────▼──────┐  ┌───────▼──────┐  ┌───────▼──────┐
  │ service_id  │  │ log_entry_id │  │ employee_id  │  ... etc
  │ (service)   │  │ (log entry)  │  │ site_id      │
  └──────┬──────┘  └───────┬──────┘  └───────┬──────┘
         │                 │                  │
  computed fields    computed fields    computed fields
  read from          read from          read from
  service record     log entry record   employee/site record
```

### Two roles for state records

1. **Subject records** (`is_subject=True`, `root_id=0`): Represent the main workflow-enabled record itself (a log entry, an employee, a ship). These appear as group headers in the Radar.
2. **Service records** (`is_subject=False`, `root_id>0`): Represent services/tasks attached to a subject. These appear indented under their subject in the Radar.

## The Mixin Layer

### `riverflow.state.mixin`

Adds `workflow_id`, `state_id`, and transition button infrastructure to any model. Provides the statusbar widget, transition JSON computation, and state change logic.

**Key fields provided**: `workflow_id`, `state_id`, `state_json`, `transition_buttons_json`, `is_end_state`, `deadline`

### `riverflow.state.record.tracker.mixin`

Maintains the link between a master record and its state record(s) in `riverflow.state.record`. A model that inherits this mixin automatically gets:

- `state_record_id` (Many2one, primary state record)
- `state_record_ids` (One2many, all state records)

State records are created lazily on first access via `_compute_state_record_id`. The mixin provides three extension points:

| Method | Default | Purpose |
|--------|---------|---------|
| `_add_state_record(record)` | `return True` | Return False to exclude a record from the Radar (e.g., generated/template records) |
| `_create_state_records(records)` | Creates one `record_type='single'` per record | Override to create multiple records (e.g., log entries create `start` + `end` pairs) |
| `unlink()` | Deletes state records when master is deleted | Ensures cleanup |

## The Computed Field Chain

`riverflow.state.record` has many stored computed fields (name, workflow_id, state_id, active, responsible_team_id, etc.). The base model computes these from `service_id` -- the default case for service task records.

For non-service master records (log entries, employees, ships), the inheriting module must add:

1. **A foreign key field** on the state record pointing to the master model (e.g., `log_entry_id`, `employee_id`, `site_id`)
2. **Overrides of `_compute_*` methods** to add the new FK as a dependency
3. **Overrides of `_compute_*_for_record` methods** to return the master record's value when the FK is set

### The `_for_record` pattern

Each computed field uses a two-method pattern:

```python
# In the base riverflow.state.record:
@api.depends("service_id.workflow_id")
def _compute_workflow_id(self):
    for record in self:
        record.workflow_id = self._compute_workflow_id_for_record(record)

@api.model
def _compute_workflow_id_for_record(self, record):
    if record.service_id:
        return record.service_id.workflow_id
    return False
```

The inheriting module overrides both:
- The `_compute_*` method: to add new `@api.depends` triggers
- The `_for_record` method: to return the value from the new FK before falling through to `super()`

```python
# In crewradar's extension (riverflow_state_record.py):
@api.depends("log_entry_id.workflow_id", "employee_id.workflow_id", "site_id.workflow_id")
def _compute_workflow_id(self):
    super()._compute_workflow_id()

@api.model
def _compute_workflow_id_for_record(self, record):
    if record.log_entry_id:
        return record.log_entry_id.workflow_id
    if record.employee_id:
        return record.employee_id.workflow_id
    if record.site_id:
        return record.site_id.workflow_id
    return super()._compute_workflow_id_for_record(record)
```

**Important**: When multiple FK overrides coexist in the same Python class, there can only be one definition of each method. The depends from all FKs must be combined into a single `@api.depends` decorator, and the `_for_record` method must check all FKs in order before calling `super()`.

### Fields that need overriding

When adding a new model to the Radar, override these `_for_record` methods at minimum:

| Field | Source | Notes |
|-------|--------|-------|
| `name` | `record.name` | Display name in Radar list |
| `sortable_name` | `record.name` | For ordering |
| `display_name` | `record.name` | Calendar/gantt display |
| `workflow_id` | `record.workflow_id` | Workflow filter |
| `state_id` | `record.state_id` | State badge |
| `is_end_state` | `record.is_end_state` | Completed filter |
| `active` | `record.active` | Archive sync |
| `responsible_team_id` | `record.responsible_team_id` | Team filter |
| `company_id` | `record.company_id` | Multi-company (if applicable) |
| `internal_notes_summary` | `record.internal_notes_summary` | Notes preview |
| `external_messages_summary` | `record.external_messages_summary` | Messages preview |
| `unreviewed_message_count` | `record.unreviewed_message_count` | Review badge |

Optional overrides depending on the model:
- `deadline`, `end_date` -- for Radar calendar/gantt views
- `user_write_date` -- for highlight row (only if model inherits `riverflow.highlight.row.mixin`, which `riverflow.mail.thread.review.mixin` brings along); radar routes the log entry, the employee and the ship (crewradar 19.0.11.21: before, the employee's and the ship's own rows never highlighted)
- `tag_ids` -- for tag display
- `res_date` -- for date-based sorting

## Current Radar participants

| Master Model | FK on state record | Record Types | `_add_state_record` filter | Override location |
|---|---|---|---|---|
| `riverflow.service` | `service_id` | `single` | Excludes templates | Base `riverflow.state.record` |
| `crewradar.log.entry` | `log_entry_id` | `start` + `end` | Excludes generated entries | `crewradar/models/riverflow_state_record.py` |
| `hr.employee` | `employee_id` | `single` | Only if `workflow_id` set | `crewradar/models/riverflow_state_record.py` |
| `crewradar.site` | `site_id` | `single` | Only if `workflow_id` set | `crewradar/models/riverflow_state_record.py` |

## Key Files

```text
riverflow/
├── models/
│   ├── riverflow_state_record.py                # Base state record with service_id computes
│   └── riverflow_state_record_tracker_mixin.py  # Mixin for master records

crewradar/
├── models/
│   └── riverflow_state_record.py  # Overrides for log_entry_id, employee_id, site_id
├── views/
│   └── crewradar_state_record_views.xml  # Radar list/gantt/calendar view customizations
```
