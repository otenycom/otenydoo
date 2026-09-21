---
name: oteny-audit-talent
description: "Who changed which field, and when, from the audit trail."
version: 0.1.1
---

# Audit trail

`oteny_audit` writes one row per changed field: which record, which field,
the old value, the new value, who did it, and when. A bot answers "who
changed this" by reading those rows. It never writes them and never guesses.

This Talent is the copy the bot reads. A client Talent loads it. It does
not copy this recipe.

## When to use

Load this skill when someone asks who changed a record or a field, when a
value changed, or what a record looked like before. Reach the Odoo the way
`oteny-odoo-access-talent` says: `odoo_client` when the project bound the
connection, its script when the owner set it up on this bot. Name the
connection on every call. Read the trail as your own login may; a row you
cannot read is not yours to tell.

## The model

`oteny.audit.log`, newest first. The fields you read:

| Field | Meaning |
| --- | --- |
| `model_name`, `record_id` | The changed record. `record_display_name` is its name at the time. |
| `field_display_name` | The changed field as a person sees it. `field_name` is the raw name. |
| `old_value_display_name`, `new_value_display_name` | The values as a person sees them. The raw values are `old_value` and `new_value`. |
| `change_type` | `i` insert, `u` update, `d` delete. |
| `create_uid`, `create_date` | Who made the change, and when (UTC). |

## Checklist

1. Resolve the record. When the person names it, search its model by name
   first (`search_read` with `limit: 5`) and confirm the one they mean.
2. Read the rows: `search_read` on `oteny.audit.log` with
   `[["model_name", "=", "<model>"], ["record_id", "=", <id>]]`, fields
   `["field_display_name", "old_value_display_name",
   "new_value_display_name", "change_type", "create_uid", "create_date"]`,
   `limit: 50`. Add `["field_name", "=", "<field>"]` when one field is asked.
3. Answer with the rows: who, when, field, from what to what. Quote the
   display values. Convert `create_date` to the person's time zone when
   they gave one; say UTC otherwise.
4. No rows: say the trail holds no change for that record and field. Do not
   infer a change from the record's current value.

## Never

- Never write to `oteny.audit.log`, and never call a method on it beyond
  `search_read`, `read` and `search_count`.
- Never tell a person a change you did not read from a row.
- Never widen a search to every record because one search returned nothing;
  ask which record they mean.

## Worked example

"Who changed the email of Acme Ltd?" Search `res.partner` by name, confirm
the id, read the trail for that id with `field_name = email`, and answer:
"Jane Doe changed the email of Acme Ltd from a@example.com to b@example.com
on 2026-09-17 09:12 UTC."
