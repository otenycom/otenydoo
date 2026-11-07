from odoo import api, fields, models, tools

from .oteny_audit_fields import BigInteger
import markupsafe
import logging
from datetime import datetime

_logger = logging.getLogger(__name__)


class OtenyAuditLogAggregated(models.Model):
    """User-facing audit log view.

    Shape: one row per (audit_log_ref, field_change) pair. Inserts and deletes
    are recorded internally as one tombstone row in `oteny.audit.log` with all
    captured field values folded into the `snapshot` JSON column (Measure 3,
    storage win). The view UNNESTs that snapshot back into one virtual row per
    captured field, so list/group-by/search ergonomics match the legacy
    per-field shape — Group by Field, Filter on field_name, search by value
    all behave the same way regardless of underlying row shape. The
    `audit_log_id` column points back to the underlying log row so callers
    can recover the full snapshot when they need it (form view, JSON drill).
    """

    _name = "oteny.audit.log.aggregated"
    _description = "Aggregated Audit Log (including children)"
    _auto = False
    _order = "id DESC"

    audit_log_id = fields.Many2one("oteny.audit.log", string="Audit Log", readonly=True)
    parent_record_display_name = fields.Char(string="Parent Record", readonly=True)

    # --- Fields from oteny.audit.log ---
    # 64-bit like the underlying log column (see oteny_audit_fields).
    transaction_id = BigInteger(readonly=True, string="Transaction ID")
    model_name = fields.Char(readonly=True, string="Parent Model Name")
    model_display_name = fields.Char(
        string="Parent Model",
        readonly=True,
    )
    record_id = fields.Integer(readonly=True, string="Record ID")
    record_ref = fields.Reference(
        string="Record Link",
        selection="_selection_record_ref",
        compute="_compute_record_ref",
        readonly=True,
    )
    record_display_name = fields.Char(string="Record", readonly=True)
    field_name = fields.Char(readonly=True, string="Field Raw Name")
    field_display_name = fields.Char(string="Field", readonly=True)
    field_model_name = fields.Char(string="Model Name", readonly=True)
    field_model_display_name = fields.Char(
        string="Model",
        readonly=True,
    )
    old_value = fields.Text(string="Old Value Raw", readonly=True)
    new_value = fields.Text(string="New Value Raw", readonly=True)
    old_value_display_name = fields.Char(string="Old Value", readonly=True)
    new_value_display_name = fields.Char(string="New Value", readonly=True)
    change_type = fields.Selection(
        [("i", "Insert"), ("u", "Update"), ("d", "Delete"), ("r", "Remark")],
        readonly=True,
        string="Change",
    )
    create_date = fields.Datetime(string="Timestamp", readonly=True)
    create_uid = fields.Many2one("res.users", string="User", readonly=True)

    # --- Fields to distinguish parent/child logs ---
    is_child_log = fields.Boolean(string="Is Child Log?", readonly=True)
    child_model_name = fields.Char(string="Child Model", readonly=True)
    child_model_display_name = fields.Char(
        string="Child Model Display Name",
        readonly=True,
    )
    child_record_id = fields.Integer(string="Child Record ID", readonly=True)
    child_record_ref = fields.Reference(
        string="Child Record Link",
        selection="_selection_record_ref",
        compute="_compute_child_record_ref",
        readonly=True,
    )
    caption = fields.Html(string="Caption", readonly=True, compute="_compute_caption")

    # Fallback display formats when riverflow.service isn't installed (e.g.
    # in oteny_audit standalone tests). Match the riverflow.service formats so
    # behavior is identical when both modules are present.
    _DEFAULT_DATETIME_FORMAT = "%d-%b-%y %H:%M:%S"
    _DEFAULT_DATE_FORMAT = "%d-%b-%y"

    def _audit_datetime_format(self):
        try:
            rf = self.env["riverflow.service"]
            return getattr(rf, "DATETIME_FORMAT", self._DEFAULT_DATETIME_FORMAT)
        except KeyError:
            return self._DEFAULT_DATETIME_FORMAT

    def _audit_date_format(self):
        try:
            rf = self.env["riverflow.service"]
            return getattr(rf, "DATE_FORMAT", self._DEFAULT_DATE_FORMAT)
        except KeyError:
            return self._DEFAULT_DATE_FORMAT

    def _compute_caption(self):
        """Render one caption per virtual row; rows for the same record-event
        share a header and emit only their own field-change line, mirroring
        the legacy per-field UX."""
        log_list = list(self)
        change_type_verbs = {"i": "Insert", "u": "Update", "d": "Delete", "r": "Remark"}
        date_format = self._audit_datetime_format()
        for i, log in enumerate(log_list):
            caption_parts = []

            show_header = False
            if i == 0:
                show_header = True
            else:
                prev_log = log_list[i - 1]
                current_record_key = (
                    (log.field_model_name, log.child_record_id, log.change_type)
                    if log.is_child_log
                    else (log.model_name, log.record_id, log.change_type)
                )
                prev_record_key = (
                    (prev_log.field_model_name, prev_log.child_record_id, prev_log.change_type)
                    if prev_log.is_child_log
                    else (prev_log.model_name, prev_log.record_id, prev_log.change_type)
                )
                if current_record_key != prev_record_key or log.transaction_id != prev_log.transaction_id:
                    show_header = True

            if show_header:
                model_display = markupsafe.escape(log.field_model_display_name or "")
                record_display = markupsafe.escape(log.record_display_name or "")
                verb = change_type_verbs.get(log.change_type, "changed")
                user_display = markupsafe.escape(log.create_uid.name if log.create_uid else "")
                date_display = fields.Datetime.context_timestamp(self, log.create_date).strftime(date_format)
                styled_record_display = f'<span class="oteny-audit-record-header">{record_display}</span>'
                header_str = f"{verb} {model_display} {styled_record_display} by <b>{user_display}</b> on {date_display}"
                caption_parts.append(f"<div>{header_str}</div>")

            change_type = log.change_type
            field_name = markupsafe.escape(log.field_display_name or "")
            is_html_field = self._is_html_field(log)
            old_display, new_display = self._caption_values(log)

            if change_type == "i":
                new_val = self._format_field_value(new_display, is_html_field)
                field_change_str = f"Set <b>{field_name}</b> to <b>{new_val}</b>"
            elif change_type == "u":
                old_val = self._format_field_value(old_display, is_html_field)
                new_val = self._format_field_value(new_display, is_html_field)
                field_change_str = (
                    f"Updated <b>{field_name}</b> from '<b>{old_val}</b>' to '<b>{new_val}</b>'"
                )
            elif change_type == "d":
                old_val = self._format_field_value(old_display, is_html_field)
                field_change_str = f"<b>{field_name}</b> was <b>{old_val}</b>"
            elif change_type == "r":
                # A remark holds text, not a field value: always plain, even when its key
                # is the name of an HTML field.
                remark_text = markupsafe.escape(log.new_value_display_name or "")
                field_change_str = f"{field_name}: <b>{remark_text}</b>"
            else:
                field_change_str = ""

            if field_change_str:
                caption_parts.append(f"<div class='ms-4'>{field_change_str}</div>")

            log.caption = markupsafe.Markup("".join(caption_parts))

        for log in self:
            if log.caption is False:
                log.caption = ""

    def _caption_values(self, log):
        """(old, new) display values of a row. A selection field shows its label: rows written
        before 19.0.1.723 hold the technical value ("email") as their display value."""
        old_display = log.old_value_display_name or ""
        new_display = log.new_value_display_name or ""
        field = self._audited_field(log)
        if field is not None and field.type == "selection":
            labels = dict(field._description_selection(self.env))
            if old_display == (log.old_value or ""):
                old_display = labels.get(old_display, old_display)
            if new_display == (log.new_value or ""):
                new_display = labels.get(new_display, new_display)
        return old_display, new_display

    def _audited_field(self, log):
        """The field object of a row, or None (a removed model or field, a remark key)."""
        model_name, field_name = log.field_model_name, log.field_name
        if not model_name or not field_name or model_name not in self.env:
            return None
        return self.env[model_name]._fields.get(field_name)

    def _is_html_field(self, log):
        """Check if the field being audited is an HTML field."""
        try:
            field_name = log.field_name
            if field_name == "body_html":
                return True
            model_name = log.field_model_name
            if not model_name or not field_name:
                return False
            if model_name not in self.env:
                return False
            model_class = self.env[model_name]
            field = model_class._fields.get(field_name, False)
            if not field:
                return False
            return field.type == "html"
        except Exception as e:
            _logger.warning(
                "Could not determine if field %s.%s is an HTML field. Error: %s",
                log.field_model_name,
                log.field_name,
                e,
            )
            return False

    def _format_field_value(self, value, is_html_field):
        if not value:
            return ""
        converted_value = self._convert_date_value_to_local(value)
        if is_html_field:
            wrapped_value = f'<div class="oteny-audit-html-field">{converted_value}</div>'
            return markupsafe.Markup(wrapped_value)
        else:
            return markupsafe.escape(converted_value)

    def _convert_date_value_to_local(self, value):
        if not value or not isinstance(value, str):
            return value
        try:
            if " " in value and ("+" in value or "-" in value and value.count("-") >= 2):
                try:
                    dt = fields.Datetime.to_datetime(value)
                    local_dt = fields.Datetime.context_timestamp(self, dt)
                    return local_dt.strftime(self._audit_datetime_format())
                except (ValueError, TypeError):
                    pass
            if "-" in value and len(value.split("-")) == 3 and " " not in value:
                try:
                    date = fields.Date.to_date(value)
                    return date.strftime(self._audit_date_format())
                except (ValueError, TypeError):
                    pass
        except Exception:
            pass
        return value

    def _selection_record_ref(self):
        return self.env["oteny.audit.log"]._selection_record_ref()

    def _compute_record_ref(self):
        for log in self:
            log.record_ref = (
                f"{log.model_name},{log.record_id}" if log.model_name and log.record_id else False
            )

    def _compute_child_record_ref(self):
        for log in self:
            if log.is_child_log and log.child_model_name and log.child_record_id:
                log.child_record_ref = f"{log.child_model_name},{log.child_record_id}"
            else:
                log.child_record_ref = False

    def init(self):
        """Materialize the view: one row per (log row, field) for the log row's own record
        (direct rows, read from oteny_audit_log), and one per (parent reference, field) for
        each parent of that record (child rows). Snapshot rows are unnested into one row per
        captured field. The comment above the CREATE VIEW gives the branches and the row id.
        """
        tools.drop_view_if_exists(self.env.cr, self._table)

        # GIN index on the snapshot column for `?` and `@>` operators. Cheap
        # to maintain (~50-80 bytes/row) and makes targeted "find inserts that
        # captured field X" queries into index seeks. PARTIAL on
        # field_name = '__snapshot__' to match its btree sibling below: only
        # snapshot rows carry a non-null snapshot, and UPDATE rows (~7/8 of the
        # table) have snapshot IS NULL — yet a non-partial GIN still churns the
        # fastupdate pending list on every insert. Restricting the index to
        # snapshot rows removes that insert-time churn without losing coverage.
        #
        # CREATE INDEX IF NOT EXISTS will NOT replace a differently-defined
        # existing index, so we drop-then-recreate — but only when the live
        # index is the old non-partial definition (indexdef lacks "field_name").
        # This guard keeps a multi-GB rebuild from running on every -u.
        self.env.cr.execute(
            """
            SELECT indexdef FROM pg_indexes
            WHERE indexname = 'oteny_audit_log_snapshot_gin_idx'
            """
        )
        row = self.env.cr.fetchone()
        if row and "field_name" not in row[0]:
            _logger.info(
                "Dropping non-partial oteny_audit_log_snapshot_gin_idx to recreate it partial"
            )
            self.env.cr.execute("DROP INDEX oteny_audit_log_snapshot_gin_idx")
        self.env.cr.execute(
            """
            CREATE INDEX IF NOT EXISTS oteny_audit_log_snapshot_gin_idx
            ON oteny_audit_log USING GIN (snapshot jsonb_path_ops)
            WHERE field_name = '__snapshot__'
            """
        )

        # Partial btree index on (create_date DESC) for snapshot rows. Without
        # this, the snapshot half of the aggregated view's UNION falls back to
        # a parallel seq scan over the whole log table for every date-window
        # query — the planner cannot combine the existing global create_date
        # index with the field_name='__snapshot__' filter efficiently. A
        # partial index is tiny (one row per record-event, ~1/8 of total) and
        # turns the audit-log "From Yesterday onwards" load from a multi-
        # second cold scan into a sub-100ms range scan.
        self.env.cr.execute(
            """
            CREATE INDEX IF NOT EXISTS oteny_audit_log_snapshot_create_date_idx
            ON oteny_audit_log (create_date DESC)
            WHERE field_name = '__snapshot__'
            """
        )

        # UNION ALL of four disjoint branches: {direct, child} x {plain, snapshot}.
        #
        # DIRECT rows come from oteny_audit_log itself: a log row is shown under its own
        # record without a reference row (cut F of plans/audit-storage-reduction.md; until
        # 19.0.1.723 each log row had a "direct" reference that repeated its model, record,
        # name, date and transaction). CHILD rows come from the parent references
        # (`NOT ref.is_direct`); old direct references that the purge has not removed yet
        # are ignored, so a change never shows twice.
        #
        # PLAIN rows (updates, remarks, legacy per-field inserts/deletes, empty snapshots)
        # expose the log columns as they are, so a value search
        # (`unaccent(new_value_display_name) ILIKE ...`) pushes into the log scan. That split
        # took the value search from ~36 s to ~100 ms on production data; a CASE coupling log
        # and the snapshot LATERAL hid the predicate from the planner. SNAPSHOT rows unnest
        # the insert/delete tombstone into one row per captured field (`jsonb_each WITH
        # ORDINALITY`). No window function, so an outer `create_date` filter pushes into each
        # branch.
        #
        # Row id: (log.id * 1000 + ordinal) * 2 for direct rows, (ref.id * 1000 + ordinal)
        # * 2 + 1 for child rows (ordinal 0 for plain rows, 1..N for snapshot fields). Unique
        # over the four branches and far below the web client's 2^53.
        def branch(child, snapshot):
            if child:
                row_id = "((ref.id::bigint * 1000) + %s) * 2 + 1" % ("jek.idx" if snapshot else "0")
                model = "ref.target_model_name"
                record_id = "ref.target_record_id"
                record_name = "ref.target_display_name"
                parent_name = "COALESCE(ref.parent_display_name, log.record_display_name)"
                child_cols = """
                    TRUE AS is_child_log,
                    log.model_name AS child_model_name,
                    COALESCE(im_child.name->>'en_US', log.model_name) AS child_model_display_name,
                    log.record_id AS child_record_id"""
                source = """
                FROM oteny_audit_log_ref ref
                JOIN oteny_audit_log log ON ref.audit_log_id = log.id
                LEFT JOIN ir_model im_target ON ref.target_model_name = im_target.model
                LEFT JOIN ir_model im_child ON log.model_name = im_child.model"""
                child_filter = "NOT ref.is_direct AND "
            else:
                row_id = "((log.id::bigint * 1000) + %s) * 2" % ("jek.idx" if snapshot else "0")
                model = "log.model_name"
                record_id = "log.record_id"
                record_name = "log.record_display_name"
                parent_name = "log.record_display_name"
                child_cols = """
                    FALSE AS is_child_log,
                    NULL::varchar AS child_model_name,
                    NULL::varchar AS child_model_display_name,
                    NULL::integer AS child_record_id"""
                source = """
                FROM oteny_audit_log log
                LEFT JOIN ir_model im_target ON log.model_name = im_target.model"""
                child_filter = ""
            if snapshot:
                value_cols = """
                    jek.key AS field_name,
                    COALESCE(jek.value->>'label', log.field_display_name) AS field_display_name,
                    log.model_name AS field_model_name,
                    COALESCE(im_field.name->>'en_US', log.model_name) AS field_model_display_name,
                    CASE WHEN log.change_type = 'd' THEN jek.value->>'raw' ELSE '' END AS old_value,
                    CASE WHEN log.change_type = 'i' THEN jek.value->>'raw' ELSE '' END AS new_value,
                    CASE WHEN log.change_type = 'd' THEN jek.value->>'display' ELSE '' END AS old_value_display_name,
                    CASE WHEN log.change_type = 'i' THEN jek.value->>'display' ELSE '' END AS new_value_display_name,"""
                lateral = """
                CROSS JOIN LATERAL (
                    -- The fields of one insert or delete read in the order of their label:
                    -- the list sorts by id descending, so the label Z gets ordinal 1.
                    SELECT key, value,
                           row_number() OVER (ORDER BY COALESCE(value->>'label', key) DESC, key DESC) AS idx
                    FROM jsonb_each(log.snapshot) AS s(key, value)
                ) jek"""
                where = """log.field_name = '__snapshot__'
                    AND log.snapshot IS NOT NULL
                    AND log.snapshot <> '{}'::jsonb"""
            else:
                # Cast to text so the column types match the snapshot branches.
                value_cols = """
                    log.field_name::text AS field_name,
                    log.field_display_name::text AS field_display_name,
                    log.model_name AS field_model_name,
                    COALESCE(im_field.name->>'en_US', log.model_name) AS field_model_display_name,
                    log.old_value AS old_value,
                    log.new_value AS new_value,
                    log.old_value_display_name AS old_value_display_name,
                    log.new_value_display_name AS new_value_display_name,"""
                lateral = ""
                where = """(log.field_name <> '__snapshot__'
                    OR log.snapshot IS NULL
                    OR log.snapshot = '{}'::jsonb)"""
            return f"""
                SELECT
                    {row_id} AS id,
                    log.id AS audit_log_id,
                    log.create_date,
                    log.create_uid,
                    log.transaction_id,
                    {model} AS model_name,
                    COALESCE(im_target.name->>'en_US', {model}) AS model_display_name,
                    {record_id} AS record_id,
                    {record_name} AS record_display_name,
                    {parent_name} AS parent_record_display_name,{value_cols}
                    log.change_type,{child_cols}
                {source}
                LEFT JOIN ir_model im_field ON log.model_name = im_field.model{lateral}
                WHERE {child_filter}{where}"""

        branches = "\n                UNION ALL\n".join(
            branch(child, snapshot) for child in (False, True) for snapshot in (False, True)
        )
        self.env.cr.execute(f"CREATE OR REPLACE VIEW {self._table} AS ({branches}\n            )")
