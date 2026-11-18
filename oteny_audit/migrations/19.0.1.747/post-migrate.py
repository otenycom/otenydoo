"""Post-migration 19.0.1.747 — switch on the purge phase that strips the stored insert snapshots.

Since 19.0.1.747 an insert writes one audit row with the id of the new record, and no snapshot of
its fields (plan oteny-audit/plans/audit-insert-id-only.md). The insert snapshots written before
stay until the phase `inserts` of the scheduled action "Audit Log: Purge Rows Outside Coverage"
turns them into id rows; the phase `vacuum` then gives the space back with VACUUM FULL, which
locks the two audit tables. So this script only sets the phase and switches the action on for
the next night at 01:00 UTC. It switches itself off when it is done. On a copy whose crons are
off (a staging build, a laptop), run it by hand: Settings > Technical > Scheduled Actions >
Run Manually, repeated until the action is off.
"""

import datetime

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {"oteny_audit_ignore": True})
    cron = env.ref("oteny_audit.ir_cron_audit_log_purge", raise_if_not_found=False)
    if not cron:
        return
    params = env["ir.config_parameter"].sudo()
    params.set_param("oteny_audit.purge_phase", "inserts")
    params.set_param("oteny_audit.purge_next_id", False)
    now = datetime.datetime.utcnow()
    tonight = now.replace(hour=1, minute=0, second=0, microsecond=0)
    if tonight <= now:
        tonight += datetime.timedelta(days=1)
    cron.write({"active": True, "nextcall": tonight})
