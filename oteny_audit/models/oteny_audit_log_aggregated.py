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

            if change_type == "i":
                new_val = self._format_field_value(log.new_value_display_name or "", is_html_field)
                field_change_str = f"Set <b>{field_name}</b> to <b>{new_val}</b>"
            elif change_type == "u":
                old_val = self._format_field_value(log.old_value_display_name or "", is_html_field)
                new_val = self._format_field_value(log.new_value_display_name or "", is_html_field)
                field_change_str = (
                    f"Updated <b>{field_name}</b> from '<b>{old_val}</b>' to '<b>{new_val}</b>'"
                )
            elif change_type == "d":
                old_val = self._format_field_value(log.old_value_display_name or "", is_html_field)
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
        """Materialize the view as a UNION ALL of:

        * non-snapshot rows (legacy pre-M3 inserts/deletes + all updates) — passed
          through one row per (ref, log) pair, exactly the legacy shape.
        * snapshot rows (M3 tombstones) — UNNESTed via `jsonb_each` to produce
          one virtual row per (ref, log, snapshot_field) triple. Each virtual
          row carries field_name, field_display_name, and the appropriate
          old/new values derived from the snapshot entry.

        Row-id encoding: `ref.id::bigint * 1000 + field_index` (field_index=0
        for non-snapshot rows, 1..N for snapshot-derived rows). Avoids
        collision while staying within JavaScript's safe-integer range
        (2^53 ≈ 9×10^15) — `<< 32` would overflow that range for ref.id > 2M
        and the web client would silently lose precision. Field-count cap
        per record event is well under 1000 in practice.
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

        # UNION ALL of two disjoint branches, partitioned on whether the log
        # row is a non-empty snapshot tombstone. This is a performance rewrite
        # of what used to be a single SELECT with a LATERAL + CASE machinery
        # for every row.
        #
        # WHY: a value search (`unaccent(new_value_display_name) ILIKE '%x%'`)
        # against the old single-SELECT could not be pushed into the log scan,
        # because new_value_display_name was a CASE coupling `log` with the
        # snapshot LATERAL (`jek`). The planner estimated rows=1 over that
        # opaque expression, chose a nested loop, and seq-scanned the multi-
        # million-row oteny_audit_log_ref table once per matching log row — a
        # ~36s, 400M-row join explosion on production data.
        #
        # THE SPLIT: update rows (the ~7/8 majority) NEVER carry a snapshot, so
        # the non-snapshot branch (A) can expose PLAIN log columns
        # (new_value_display_name = log.new_value_display_name, etc.). With a
        # plain column the planner pushes the value filter straight into the
        # log scan (create_date index + filter), then index-joins ref via
        # oteny_audit_log_ref(audit_log_id). Measured 36s -> ~100ms.
        #
        # The two branch predicates are exact negations, so the union is total
        # and disjoint; UNION ALL (no dedup) is correct. The synthetic id
        # encoding is preserved: branch A always ends in 0
        # (ref.id*1000 + 0), branch B ends in 1..N (ref.id*1000 + ord), so ids
        # stay collision-free and JS-safe. No window function is used, so the
        # outer `create_date` filter still pushes into each Append child
        # (the historical UNION-pushdown hazard was ROW_NUMBER specifically).
        #
        # Branch A also catches the empty-snapshot tombstone rows (snapshot
        # IS NULL or '{}'): they surface as a single row with the sentinel
        # field_name='__snapshot__' and empty values, exactly as before.
        self.env.cr.execute(
            """
            CREATE OR REPLACE VIEW %s AS (
                -- Branch A: non-snapshot rows (updates, legacy per-field
                -- inserts/deletes, and empty-snapshot tombstones). Plain log
                -- columns only — no jsonb, no LATERAL — so value searches push
                -- into the log scan.
                SELECT
                    (ref.id::bigint * 1000) + 0 AS id,
                    ref.audit_log_id,
                    log.create_date,
                    log.create_uid,
                    log.transaction_id,

                    ref.target_model_name AS model_name,
                    COALESCE(im_target.name->>'en_US', ref.target_model_name) AS model_display_name,
                    ref.target_record_id AS record_id,
                    ref.target_display_name AS record_display_name,

                    COALESCE(ref.parent_display_name, log.record_display_name) AS parent_record_display_name,

                    -- Cast to text so the column types match the snapshot
                    -- branch (jek.key / COALESCE are text) and the pre-rewrite
                    -- view, keeping CREATE OR REPLACE VIEW type-compatible.
                    log.field_name::text AS field_name,
                    log.field_display_name::text AS field_display_name,
                    log.model_name AS field_model_name,
                    COALESCE(im_field.name->>'en_US', log.model_name) AS field_model_display_name,

                    log.old_value AS old_value,
                    log.new_value AS new_value,
                    log.old_value_display_name AS old_value_display_name,
                    log.new_value_display_name AS new_value_display_name,
                    log.change_type,

                    NOT ref.is_direct AS is_child_log,
                    CASE WHEN NOT ref.is_direct THEN log.model_name ELSE NULL END AS child_model_name,
                    CASE WHEN NOT ref.is_direct
                         THEN COALESCE(im_child.name->>'en_US', log.model_name)
                         ELSE NULL
                    END AS child_model_display_name,
                    CASE WHEN NOT ref.is_direct THEN log.record_id ELSE NULL END AS child_record_id

                FROM oteny_audit_log_ref ref
                JOIN oteny_audit_log log ON ref.audit_log_id = log.id
                LEFT JOIN ir_model im_target ON ref.target_model_name = im_target.model
                LEFT JOIN ir_model im_field ON log.model_name = im_field.model
                LEFT JOIN ir_model im_child ON (NOT ref.is_direct AND log.model_name = im_child.model)
                WHERE
                    log.field_name <> '__snapshot__'
                    OR log.snapshot IS NULL
                    OR log.snapshot = '{}'::jsonb

                UNION ALL

                -- Branch B: snapshot tombstone rows with a non-empty snapshot
                -- (inserts/deletes). UNNEST each captured field via LATERAL
                -- jsonb_each WITH ORDINALITY; map the value onto old/new by
                -- change_type (insert -> new_value, delete -> old_value).
                SELECT
                    (ref.id::bigint * 1000) + jek.idx AS id,
                    ref.audit_log_id,
                    log.create_date,
                    log.create_uid,
                    log.transaction_id,

                    ref.target_model_name AS model_name,
                    COALESCE(im_target.name->>'en_US', ref.target_model_name) AS model_display_name,
                    ref.target_record_id AS record_id,
                    ref.target_display_name AS record_display_name,

                    COALESCE(ref.parent_display_name, log.record_display_name) AS parent_record_display_name,

                    jek.key AS field_name,
                    COALESCE(jek.value->>'label', log.field_display_name) AS field_display_name,
                    log.model_name AS field_model_name,
                    COALESCE(im_field.name->>'en_US', log.model_name) AS field_model_display_name,

                    CASE WHEN log.change_type = 'd' THEN jek.value->>'raw' ELSE '' END AS old_value,
                    CASE WHEN log.change_type = 'i' THEN jek.value->>'raw' ELSE '' END AS new_value,
                    CASE WHEN log.change_type = 'd' THEN jek.value->>'display' ELSE '' END AS old_value_display_name,
                    CASE WHEN log.change_type = 'i' THEN jek.value->>'display' ELSE '' END AS new_value_display_name,
                    log.change_type,

                    NOT ref.is_direct AS is_child_log,
                    CASE WHEN NOT ref.is_direct THEN log.model_name ELSE NULL END AS child_model_name,
                    CASE WHEN NOT ref.is_direct
                         THEN COALESCE(im_child.name->>'en_US', log.model_name)
                         ELSE NULL
                    END AS child_model_display_name,
                    CASE WHEN NOT ref.is_direct THEN log.record_id ELSE NULL END AS child_record_id

                FROM oteny_audit_log_ref ref
                JOIN oteny_audit_log log ON ref.audit_log_id = log.id
                LEFT JOIN ir_model im_target ON ref.target_model_name = im_target.model
                LEFT JOIN ir_model im_field ON log.model_name = im_field.model
                LEFT JOIN ir_model im_child ON (NOT ref.is_direct AND log.model_name = im_child.model)
                CROSS JOIN LATERAL (
                    SELECT key, value, ord AS idx
                    FROM jsonb_each(log.snapshot) WITH ORDINALITY AS s(key, value, ord)
                ) jek
                WHERE
                    log.field_name = '__snapshot__'
                    AND log.snapshot IS NOT NULL
                    AND log.snapshot <> '{}'::jsonb
            )
            """
            % self._table
        )
