"""Delete the duplicate Radar rows before the unique index of 19.0.1.1357 is made
(radar plan crewradar-development/plans/one-radar-row-per-subject.md, F4).

The index riverflow_state_record_master_record_type_uniq allows one row per
master_model, master_res_id and record_type. A row that has an older row with the
same three values is deleted; the oldest (lowest id) stays. On the production copy
of 23-Oct-2026 this deletes 533 rows: the second rows that a migration made on
1-Apr-2026 for 515 employees, and 18 archived rows of 8 archived log entries. No
record, check result or tag points to any of them. The links of the subjects are
filled by the crewradar migration that follows.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute(
        """
        DELETE FROM riverflow_state_record r
         USING riverflow_state_record k
         WHERE k.master_model = r.master_model
           AND k.master_res_id = r.master_res_id
           AND k.record_type = r.record_type
           AND k.id < r.id
        RETURNING r.master_model
        """
    )
    deleted = {}
    for (master_model,) in cr.fetchall():
        deleted[master_model] = deleted.get(master_model, 0) + 1
    _logger.info("19.0.1.1357: duplicate Radar rows deleted: %s", deleted)
