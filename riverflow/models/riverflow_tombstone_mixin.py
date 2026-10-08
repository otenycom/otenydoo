from odoo import api, fields, models, tools
from odoo.tools import SQL


class TombstoneMixin(models.AbstractModel):
    """Retire a record instead of deleting it inside a compute.

    Odoo's unlink() is not a plain delete: it first runs every pending compute
    of the transaction (flush_all) and it ends by clearing the whole ORM cache
    (invalidate_all). Inside a compute that a field read triggered, the first
    computes other fields too early (a field being computed drops the
    recompute that a record created in that nested flush asks for), and the
    second makes the read in flight lose its value ("Record does not exist or
    has been deleted" for a record that exists). Both reached production
    (radar plan tombstones-instead-of-deletes-in-computes).

    So a compute calls ``_retire()`` on the records it no longer needs. One
    write sets ``to_be_deleted`` (why the row goes) and ``active = False`` (so
    Odoo hides it by itself: searches, x2many reads and path domains), clears
    the links that a delete would clear at once, and registers one pre-commit
    step that deletes the flagged rows when no field read is in flight.

    Code that runs with ``active_test=False`` sees retired rows as if they
    were archived; it filters ``("to_be_deleted", "=", False)``. The domain
    ``("x_ids", "!=", False)`` reads the comodel with active_test=False: use
    ``("x_ids", "any", [])``. SQL readers add ``AND NOT to_be_deleted``.
    """

    _name = "riverflow.tombstone.mixin"
    _description = "Tombstone: retire instead of delete inside a compute"

    to_be_deleted = fields.Boolean(
        default=False,
        index=True,
        copy=False,
        readonly=True,
        help="Set by a compute that no longer needs this record. The record is archived at once; the commit deletes it.",
    )
    # Without a user archive, only _retire() sets active, so it is read-only in
    # the UI: Odoo offers Archive in a list's action menu for a writable active.
    # A model with a user archive (services, log entries) redefines active
    # writable; Odoo merges the two definitions.
    active = fields.Boolean(default=True, index=True, readonly=True)

    _TOMBSTONE_PURGE_KEY = "riverflow.tombstone.purge"
    _TOMBSTONE_MODELS_KEY = "riverflow.tombstone.models"

    def init(self):
        """Database defaults, so a row inserted with SQL (test fixtures do) is
        live. Odoo leaves a new Boolean column NULL on existing rows when its
        default is False; the update makes NOT to_be_deleted exact in SQL."""
        super().init()
        if self._abstract or not self._auto:
            return
        table = SQL.identifier(self._table)
        self.env.cr.execute(SQL("ALTER TABLE %s ALTER COLUMN active SET DEFAULT true", table))
        self.env.cr.execute(SQL("ALTER TABLE %s ALTER COLUMN to_be_deleted SET DEFAULT false", table))
        self.env.cr.execute(SQL("UPDATE %s SET to_be_deleted = false WHERE to_be_deleted IS NULL", table))

    @api.model
    @tools.ormcache()
    def _tombstone_links(self):
        """The links of this model, found once per registry from the field
        definitions, so a model that joins the mixin needs no list of its own.

        - set-null links: stored, non-computed Many2one fields of any model
          that point at this model with ondelete="set null". A computed
          Many2one is left to its compute, which must leave out retired
          records and depend on <link>.active.
        - cascade children: One2many fields of this model whose inverse is
          ondelete="cascade" and whose comodel also uses the mixin.
        """
        Mixin = self.pool["riverflow.tombstone.mixin"]
        set_null = []
        for model_name, Model in self.pool.items():
            if Model._abstract or not Model._auto:
                continue
            for field in Model._fields.values():
                if (
                    field.type == "many2one"
                    and field.comodel_name == self._name
                    and field.store
                    and not field.compute
                    and field.ondelete == "set null"
                ):
                    set_null.append((model_name, field.name))
        children = []
        for field in self._fields.values():
            if field.type != "one2many" or not field.inverse_name:
                continue
            Comodel = self.pool[field.comodel_name]
            inverse = Comodel._fields.get(field.inverse_name)
            if inverse is not None and inverse.ondelete == "cascade" and issubclass(Comodel, Mixin):
                children.append(field.name)
        return tuple(set_null), tuple(children)

    def _retire(self):
        """Retire these records instead of deleting them now (see the class).

        One write for the whole set: a write runs no flush and clears no
        cache. Do at once what the delete would do to links, retire the
        cascade children that use the mixin too, and register the purge."""
        records = self.filtered(lambda r: not r.to_be_deleted)
        if not records:
            return
        records.write({"to_be_deleted": True, "active": False})
        set_null, children = self._tombstone_links()
        for model_name, field_name in set_null:
            linked = self.env[model_name].with_context(active_test=False).search([(field_name, "in", records.ids)])
            if linked:
                linked.write({field_name: False})
        for field_name in children:
            # active_test=False: a user-archived child goes too, as the database
            # cascade would delete it with the parent.
            records.with_context(active_test=False)[field_name]._retire()
        data = self.env.cr.precommit.data
        data.setdefault(self._TOMBSTONE_MODELS_KEY, set()).add(self._name)
        if not data.get(self._TOMBSTONE_PURGE_KEY):
            data[self._TOMBSTONE_PURGE_KEY] = True
            self.env.cr.precommit.add(self.env["riverflow.tombstone.mixin"]._tombstone_purge)

    @api.model
    def _tombstone_purge(self):
        """Delete the flagged rows of every model that retired some.

        Runs at the commit (a pre-commit step), when no field read is in
        flight, so the cache clear of unlink() harms nothing. The delete marks
        dependents for recompute, so this flushes them; a compute that retires
        again in that flush registers the next step, which Odoo runs in the
        same commit. Only flagged rows go: a user-archived record stays."""
        data = self.env.cr.precommit.data
        data.pop(self._TOMBSTONE_PURGE_KEY, None)
        model_names = data.pop(self._TOMBSTONE_MODELS_KEY, set())
        for model_name in sorted(model_names):
            rows = self.env[model_name].sudo().with_context(active_test=False).search([("to_be_deleted", "=", True)])
            if rows:
                rows.unlink()
        self.env.flush_all()

    @api.autovacuum
    def _gc_tombstones(self):
        """Fallback: delete flagged rows that a commit step missed. Odoo's
        vacuum calls this on every model that inherits the mixin."""
        if self._abstract:
            return
        rows = self.sudo().with_context(active_test=False).search([("to_be_deleted", "=", True)])
        if rows:
            rows.unlink()
