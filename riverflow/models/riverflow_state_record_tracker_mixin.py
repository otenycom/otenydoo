from odoo import _, fields, models, api
from odoo.addons.riverflow.models.riverflow_transition_mixin import RiverflowTransitionMixin  # type: ignore
import logging

_logger = logging.getLogger(__name__)

"""
This mixin maintains a global view of services and their parent entities through the RiverflowStateRecord model.

Key features:
- Automatic synchronization of the current model with a global state record 
  for efficient querying
"""


class RiverflowWorkflowStateRecordTrackerMixin(models.AbstractModel):
    _name = "riverflow.state.record.tracker.mixin"
    _description = "Syncs the model with the central Radar table (RiverflowStateRecord)."

    state_record_id = fields.Many2one(
        "riverflow.state.record",
        string="Primary State Record",
        compute="_compute_state_record_id",
        store=True,
        readonly=True,
        ondelete="set null",
        help="Primary state record for this master record (used for backwards compatibility)",
    )

    state_record_ids = fields.One2many(
        "riverflow.state.record",
        compute="_compute_state_record_ids",
        string="All State Records",
        help="All state records for this master record",
    )

    @api.depends("create_date")
    def _compute_state_record_id(self):
        """Compute the primary state record for backwards compatibility.

        For records with multiple state records (like log entries with start/end),
        this points to the first (start) record.

        Optimized for batch performance: queries all state records in one go,
        creates missing ones in batch, then assigns to each record.
        """
        if any(isinstance(record.id, api.NewId) for record in self):
            return

        # Read archived rows too: a row is archived with its subject, and a lookup that
        # misses it creates a second row and leaves the link empty (radar plan
        # crewradar-development/plans/one-radar-row-per-subject.md, cause C3).
        StateRecord = self.env["riverflow.state.record"].with_context(active_test=False)

        # Batch query: find all state records for all records in self
        all_state_records = StateRecord.search(
            [
                ("master_model", "=", self._name),
                ("master_res_id", "in", self.ids),
            ],
            order="master_res_id, record_type, id",
        )

        # Group state records by master_res_id for quick lookup
        state_records_by_master = {}
        for state_record in all_state_records:
            master_id = state_record.master_res_id
            if master_id not in state_records_by_master:
                state_records_by_master[master_id] = []
            state_records_by_master[master_id].append(state_record)

        # Identify records that need state record creation
        records_needing_creation = self.env[self._name]
        for record in self:
            if record.id not in state_records_by_master and self._add_state_record(record):
                records_needing_creation |= record

        # Batch create missing state records
        if records_needing_creation:
            self._create_state_records(records_needing_creation)

            # Re-query the newly created state records in batch
            new_state_records = StateRecord.search(
                [
                    ("master_model", "=", self._name),
                    ("master_res_id", "in", records_needing_creation.ids),
                ],
                order="master_res_id, record_type, id",
            )

            # Add newly created state records to lookup dict
            for state_record in new_state_records:
                master_id = state_record.master_res_id
                if master_id not in state_records_by_master:
                    state_records_by_master[master_id] = []
                state_records_by_master[master_id].append(state_record)

        # Assign primary state record to each record (first one in sorted order)
        for record in self:
            state_records = state_records_by_master.get(record.id, [])
            record.state_record_id = state_records[0] if state_records else False

    @api.depends("create_date")
    def _compute_state_record_ids(self):
        """Compute all state records for this master record.

        Optimized for batch performance: queries all state records in one go,
        then assigns to each record.

        Note: Depends on create_date to trigger on record creation. State records
        are created once and persist, so this mainly runs during initial setup.
        """
        StateRecord = self.env["riverflow.state.record"]

        # Filter out NewId records
        records_to_process = self.filtered(lambda r: not isinstance(r.id, api.NewId))
        newid_records = self - records_to_process

        # Set empty recordset for NewId records
        for record in newid_records:
            record.state_record_ids = StateRecord

        if not records_to_process:
            return

        # Batch query: find all state records for all real records
        # This search is only expensive on first access; afterwards, state records
        # exist and are just being retrieved
        all_state_records = StateRecord.search(
            [
                ("master_model", "=", self._name),
                ("master_res_id", "in", records_to_process.ids),
            ]
        )

        # Group state records by master_res_id for quick lookup
        state_records_by_master = {}
        for state_record in all_state_records:
            master_id = state_record.master_res_id
            if master_id not in state_records_by_master:
                state_records_by_master[master_id] = StateRecord
            state_records_by_master[master_id] |= state_record

        # Assign state records to each record
        for record in records_to_process:
            record.state_record_ids = state_records_by_master.get(record.id, StateRecord)

    def _create_state_records(self, records):
        """Create state records for the given master records.

        By default, creates one state record per master record with record_type='single'.
        Inheriting models can override this to create multiple state records per master record
        (e.g., separate records for start and end dates with different record_types).

        Args:
            records: Recordset of master records that need state records

        Returns:
            Recordset of created state records
        """
        state_record_vals = []
        for record in records:
            if self._add_state_record(record):
                # _logger.info(f"Creating riverflow_state_record for {record._name} with id {record.id}")
                # The rest of the values are copied from the record by compute methods in the state record model
                vals = {
                    "master_model": record._name,
                    "master_res_id": record.id,
                    "record_type": "single",
                }
                state_record_vals.append(vals)
        if len(state_record_vals) > 0:
            return self.env["riverflow.state.record"].create(state_record_vals)
        return self.env["riverflow.state.record"]

    @api.model
    def _add_state_record(self, record):
        return True

    def unlink(self):
        """Delete master records and their associated state records.

        We first unlink the master record, as during its unlink, it will flush writes to the state records
        for computed values. These flushes fail if we remove the state records from under the feet of the master record.

        Optimized for batch performance: queries all state records in one batch operation.
        """
        # Batch query: find all state records for all records being deleted
        StateRecord = self.env["riverflow.state.record"]
        state_records_to_delete = StateRecord.search(
            [
                ("master_model", "=", self._name),
                ("master_res_id", "in", self.ids),
            ]
        )

        result = super().unlink()
        state_records_to_delete.sudo().unlink()
        return result
