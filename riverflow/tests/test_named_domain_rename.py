import importlib.util
from pathlib import Path

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "riverflow", "test_named_domain")
class TestNamedDomainForm(TransactionCase):
    """The domain widget accepts a non-literal such as datetime.date.today()."""

    def test_named_domain_form_allows_expressions(self):
        """Without allow_expressions the form shows "Invalid domain" and does
        not count records. A saved filter uses the same option."""
        view = self.env.ref("riverflow.view_named_domain_form")
        self.assertIn(
            "options=\"{'model': 'applies_to_model', 'allow_expressions': True}\"",
            view.arch,
        )


@tagged("post_install", "-at_install", "riverflow", "test_named_domain")
class TestNamedDomainRename(TransactionCase):
    """The 19.0.1.1274 pre-migrate renames riverflow.auto.add.domain to
    riverflow.named.domain in place.

    The test puts the database back into the old shape with plain SQL, inside
    the test transaction (PostgreSQL DDL is transactional, so the rollback at
    the end of the test restores everything), runs the migration and checks
    what a real upgrade depends on: the rows and their ids survive, the
    registry finds the model under its new name, other modules' XML ids keep
    pointing at their records, and no riverflow field XML id keeps the old
    name — an old-named one would make Odoo drop the column at the end of the
    upgrade.
    """

    def _load_migration(self):
        migration_file = (
            Path(__file__).resolve().parent.parent
            / "migrations" / "19.0.1.1274" / "pre-migrate.py"
        )
        spec = importlib.util.spec_from_file_location("pre_migrate_1_1274", migration_file)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _back_to_old_shape(self, mod):
        """The inverse of the migration, as the database looked before it."""
        cr = self.env.cr
        cr.execute(f'ALTER TABLE "{mod.NEW_TABLE}" RENAME TO "{mod.OLD_TABLE}"')
        cr.execute(f'ALTER SEQUENCE "{mod.NEW_TABLE}_id_seq" RENAME TO "{mod.OLD_TABLE}_id_seq"')
        cr.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass AND conname LIKE %s",
            (mod.OLD_TABLE, f"{mod.NEW_TABLE}%"),
        )
        for (name,) in cr.fetchall():
            cr.execute(
                f'ALTER TABLE "{mod.OLD_TABLE}" RENAME CONSTRAINT "{name}" '
                f'TO "{mod.OLD_TABLE}{name[len(mod.NEW_TABLE):]}"'
            )
        cr.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = %s AND indexname LIKE %s",
            (mod.OLD_TABLE, f"{mod.NEW_TABLE}%"),
        )
        for (name,) in cr.fetchall():
            cr.execute(f'ALTER INDEX "{name}" RENAME TO "{mod.OLD_TABLE}{name[len(mod.NEW_TABLE):]}"')
        for table, column in mod.MODEL_NAME_COLUMNS:
            cr.execute(
                f'UPDATE "{table}" SET "{column}" = %s WHERE "{column}" = %s',
                (mod.OLD_MODEL, mod.NEW_MODEL),
            )
        for old, new in mod.RENAMED_XMLIDS.items():
            cr.execute(
                "UPDATE ir_model_data SET name = %s WHERE module = 'riverflow' AND name = %s",
                (old, new),
            )
        cr.execute(
            "UPDATE ir_model_data SET name = %s || substr(name, %s) "
            "WHERE module = 'riverflow' AND name LIKE %s",
            (mod.OLD_FIELD_PREFIX, len(mod.NEW_FIELD_PREFIX) + 1, mod.NEW_FIELD_PREFIX.replace("_", r"\_") + "%"),
        )

    def test_rename_keeps_rows_ids_and_xmlids(self):
        mod = self._load_migration()
        cr = self.env.cr
        self._back_to_old_shape(mod)

        # A named domain as another module's data record would have it: a row
        # in the old table with an XML id whose stored model is the old name.
        cr.execute(
            "SELECT id FROM ir_model WHERE model = 'res.partner'"
        )
        partner_model_id = cr.fetchone()[0]
        cr.execute(
            f'INSERT INTO "{mod.OLD_TABLE}" (name, applies_to_model_id, applies_to_model, domain) '
            "VALUES ('Partner: companies', %s, 'res.partner', %s) RETURNING id",
            (partner_model_id, "[('is_company', '=', True)]"),
        )
        row_id = cr.fetchone()[0]
        cr.execute(
            "INSERT INTO ir_model_data (module, name, model, res_id, noupdate) "
            "VALUES ('test_named_domain', 'partner_companies', %s, %s, true)",
            (mod.OLD_MODEL, row_id),
        )

        self.assertTrue(mod.rename_auto_add_domain_to_named_domain(cr))

        cr.execute("SELECT to_regclass(%s), to_regclass(%s)", (mod.OLD_TABLE, mod.NEW_TABLE))
        old_table, new_table = cr.fetchone()
        self.assertIsNone(old_table, "the old table must be gone")
        self.assertIsNotNone(new_table, "the table must carry the new name")
        cr.execute("SELECT to_regclass(%s)", (f"{mod.NEW_TABLE}_id_seq",))
        self.assertIsNotNone(cr.fetchone()[0], "the id sequence follows the table")
        cr.execute(
            "SELECT count(*) FROM pg_constraint WHERE conrelid = %s::regclass AND conname LIKE %s",
            (mod.NEW_TABLE, f"{mod.OLD_TABLE}%"),
        )
        self.assertEqual(cr.fetchone()[0], 0, "no constraint keeps the old table name")

        cr.execute(f'SELECT name, domain FROM "{mod.NEW_TABLE}" WHERE id = %s', (row_id,))
        self.assertEqual(
            cr.fetchone(), ("Partner: companies", "[('is_company', '=', True)]"),
            "the row keeps its id and values",
        )
        cr.execute("SELECT count(*) FROM ir_model WHERE model = %s", (mod.NEW_MODEL,))
        self.assertEqual(cr.fetchone()[0], 1)
        cr.execute(
            "SELECT count(*) FROM ir_model_fields WHERE model = %s OR relation = %s",
            (mod.OLD_MODEL, mod.OLD_MODEL),
        )
        self.assertEqual(cr.fetchone()[0], 0, "no field row keeps the old model name")
        cr.execute(
            "SELECT model FROM ir_model_data WHERE module = 'test_named_domain' AND name = 'partner_companies'"
        )
        self.assertEqual(
            cr.fetchone()[0], mod.NEW_MODEL,
            "another module's XML id must follow, or its data file no longer loads",
        )
        cr.execute(
            "SELECT count(*) FROM ir_model_data WHERE module = 'riverflow' AND name LIKE %s",
            ("%auto\\_add\\_domain%",),
        )
        self.assertEqual(
            cr.fetchone()[0], 0,
            "an old-named riverflow XML id would make _process_end drop the column",
        )
        cr.execute(
            "SELECT count(*) FROM ir_model_data WHERE module = 'riverflow' AND name LIKE %s",
            (mod.NEW_FIELD_PREFIX.replace("_", r"\_") + "%",),
        )
        self.assertGreaterEqual(cr.fetchone()[0], 6, "the field XML ids carry the new prefix")

        # A second run finds the new table and changes nothing.
        self.assertFalse(mod.rename_auto_add_domain_to_named_domain(cr))
