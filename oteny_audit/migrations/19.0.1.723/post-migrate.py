"""Post-migration 19.0.1.723 — switch on the purge of the stored audit rows.

19.0.1.723 changes what the audit log writes (plan oteny-audit/plans/audit-storage-reduction.md,
cuts A to I and rules N1 to N4). The rows written before stay until the scheduled action
"Audit Log: Purge Rows Outside Coverage" removes what the new rules would not have written.
The purge takes minutes and ends with VACUUM FULL, which locks the two audit tables, so it
does not run here inside the upgrade: this script only switches the action on for the next
night at 01:00 UTC (02:00 or 03:00 in Duisburg). It switches itself off when it is done. On a
copy whose crons are off (a staging build, a laptop), run it by hand: Settings > Technical >
Scheduled Actions > Run Manually.
"""

import datetime

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    cron = env.ref("oteny_audit.ir_cron_audit_log_purge", raise_if_not_found=False)
    if not cron:
        return
    params = env["ir.config_parameter"].sudo()
    params.set_param("oteny_audit.purge_phase", "deletes")
    params.set_param("oteny_audit.purge_next_id", False)
    now = datetime.datetime.utcnow()
    tonight = now.replace(hour=1, minute=0, second=0, microsecond=0)
    if tonight <= now:
        tonight += datetime.timedelta(days=1)
    cron.write({"active": True, "nextcall": tonight})
