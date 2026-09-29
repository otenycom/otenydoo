"""Rename riverflow.auto.add.domain to riverflow.named.domain, in place.

The model was already a generic "named domain": auto-add rules point to it,
and so do the crewradar salary auto-add components; rivercreds now uses it for
the applicability of credential type groups (Thijs, 2026-09-28). Only the name
said "auto add". The rows, their ids and every XML id stay; only names change.

Why pre-migrate, and why plain SQL: this runs before riverflow's new code
loads, so the registry finds the renamed table and ir.model row instead of
creating an empty new model. Odoo 19 has no model-rename helper (the
upgrade-util library is not installed), so each reference is rewritten here.

What must follow the model name, and why:
- the table, its id sequence, its constraints and indexes;
- ir_model / ir_model_fields (model, relation): the registry looks rows up by
  model name;
- ir_model_data.model for EVERY module: data records of crewradar,
  crewradar_creds and crewradar_cuneus_sign keep their XML ids, and the XML
  loader refuses an XML id whose stored model differs from the record's model;
- riverflow's own XML ids (model_, field_, access_, view_, action_, menu_):
  an old-named field XML id would not be loaded in this upgrade, so
  ir.model.data._process_end would unlink the ir.model.fields row it points
  to at the end of the upgrade and DROP THE COLUMN;
- the string references (action, views, saved filters, attachments, mail).

Idempotent: it only acts while the old table exists and the new one does not.
"""

import logging

from odoo.tools import SQL
from odoo.tools.sql import table_exists

_logger = logging.getLogger(__name__)

OLD_MODEL = "riverflow.auto.add.domain"
NEW_MODEL = "riverflow.named.domain"
OLD_TABLE = "riverflow_auto_add_domain"
NEW_TABLE = "riverflow_named_domain"

# riverflow's own XML ids that carry the model name (the field_ ids are
# renamed by prefix below).
RENAMED_XMLIDS = {
    "model_riverflow_auto_add_domain": "model_riverflow_named_domain",
    "access_riverflow_auto_add_domain_reader": "access_riverflow_named_domain_reader",
    "access_riverflow_auto_add_domain_manager": "access_riverflow_named_domain_manager",
    "view_auto_add_domain_form": "view_named_domain_form",
    "view_auto_add_domain_list": "view_named_domain_list",
    "view_auto_add_domain_search": "view_named_domain_search",
    "action_auto_add_domain": "action_named_domain",
    "menu_auto_add_domain": "menu_named_domain",
}
OLD_FIELD_PREFIX = "field_riverflow_auto_add_domain__"
NEW_FIELD_PREFIX = "field_riverflow_named_domain__"

# (table, column) pairs that store a model name as text.
MODEL_NAME_COLUMNS = [
    ("ir_model", "model"),
    ("ir_model_fields", "model"),
    ("ir_model_fields", "relation"),
    ("ir_model_data", "model"),
    ("ir_act_window", "res_model"),
    ("ir_ui_view", "model"),
    ("ir_filters", "model_id"),
    ("ir_attachment", "res_model"),
    ("mail_message", "model"),
    ("mail_followers", "res_model"),
    ("mail_activity", "res_model"),
]


def _rename_table(cr):
    cr.execute(SQL(
        "ALTER TABLE %s RENAME TO %s", SQL.identifier(OLD_TABLE), SQL.identifier(NEW_TABLE)
    ))
    cr.execute(SQL(
        "ALTER SEQUENCE IF EXISTS %s RENAME TO %s",
        SQL.identifier(f"{OLD_TABLE}_id_seq"),
        SQL.identifier(f"{NEW_TABLE}_id_seq"),
    ))
    # Constraints first: renaming a primary-key or unique constraint renames
    # its index too. Then the remaining indexes.
    cr.execute(
        "SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass AND conname LIKE %s",
        (NEW_TABLE, f"{OLD_TABLE}%"),
    )
    for (name,) in cr.fetchall():
        cr.execute(SQL(
            "ALTER TABLE %s RENAME CONSTRAINT %s TO %s",
            SQL.identifier(NEW_TABLE),
            SQL.identifier(name),
            SQL.identifier(NEW_TABLE + name[len(OLD_TABLE):]),
        ))
    cr.execute(
        "SELECT indexname FROM pg_indexes WHERE tablename = %s AND indexname LIKE %s",
        (NEW_TABLE, f"{OLD_TABLE}%"),
    )
    for (name,) in cr.fetchall():
        cr.execute(SQL(
            "ALTER INDEX %s RENAME TO %s",
            SQL.identifier(name),
            SQL.identifier(NEW_TABLE + name[len(OLD_TABLE):]),
        ))


def _rename_model_references(cr):
    for table, column in MODEL_NAME_COLUMNS:
        if not table_exists(cr, table):
            continue
        cr.execute(SQL(
            "UPDATE %s SET %s = %s WHERE %s = %s",
            SQL.identifier(table),
            SQL.identifier(column),
            NEW_MODEL,
            SQL.identifier(column),
            OLD_MODEL,
        ))
        if cr.rowcount:
            _logger.info("%s.%s: %d rows renamed to %s", table, column, cr.rowcount, NEW_MODEL)


def _rename_riverflow_xmlids(cr):
    for old, new in RENAMED_XMLIDS.items():
        cr.execute(
            "UPDATE ir_model_data SET name = %s WHERE module = 'riverflow' AND name = %s",
            (new, old),
        )
    cr.execute(
        """
        UPDATE ir_model_data
           SET name = %s || substr(name, %s)
         WHERE module = 'riverflow' AND name LIKE %s
        """,
        (NEW_FIELD_PREFIX, len(OLD_FIELD_PREFIX) + 1, OLD_FIELD_PREFIX.replace("_", r"\_") + "%"),
    )
    _logger.info("Renamed %d riverflow field XML ids to %s*", cr.rowcount, NEW_FIELD_PREFIX)


def rename_auto_add_domain_to_named_domain(cr):
    if not table_exists(cr, OLD_TABLE) or table_exists(cr, NEW_TABLE):
        return False
    _rename_table(cr)
    _rename_model_references(cr)
    _rename_riverflow_xmlids(cr)
    _logger.info("Renamed model %s to %s", OLD_MODEL, NEW_MODEL)
    return True


def migrate(cr, version):
    rename_auto_add_domain_to_named_domain(cr)
