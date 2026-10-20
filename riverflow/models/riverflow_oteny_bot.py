"""riverflow answers the bridge's work contract for its own carriers.

The platform calls ``oteny.bot`` (``work_consume``, ``work_probe``, ``work_release``)
by work token and never names this engine. This extension maps the three verbs to
the bot mixin: the run-claim consume, the token check, and the hand-back through
the state's ``is_bot_timeout`` exit.

It also arms this engine's bot crons when a bot is bound on a neutralized copy
(``bind_discuss_channel``).
"""
from odoo import models

# The engine's two bot crons (data/riverflow_bot_cron.xml): the dispatch belt that re-posts
# a lost hand-off, and the reaper that hands back a run whose harness died.
BOT_CRON_XMLIDS = (
    "riverflow.ir_cron_bot_dispatch_queue",
    "riverflow.ir_cron_bot_reap_timeouts",
)


class OtenyBotRiverflow(models.Model):
    _inherit = "oteny.bot"

    def bind_discuss_channel(self, uplink_ref, channel_id=None):
        """Bind as the platform does, then arm the bot crons on a neutralized copy.

        Neutralize switches off every cron, and both bot crons sit in a ``noupdate`` block,
        so no deploy switches them on again. A copy with a bot but without them looks
        healthy, because a hand-off still dispatches inline, until the first lost dispatch,
        which then never recovers, and a dead run is never handed back. Binding is the
        deliberate act that connects a bot to this copy, so it arms what the bot needs, and
        no provisioner keeps a runbook check of its own. This is not the side effect that
        ``ir.cron.toggle`` refuses on a neutralized database: that guard stops a record
        change from re-enabling a cron by accident. Production is never neutralized, so a
        bind there never changes a cron. The answer names what was armed
        (``bot_crons_armed``), so the provisioner can say so.
        """
        result = super().bind_discuss_channel(uplink_ref, channel_id=channel_id)
        if result.get("ok"):
            result["bot_crons_armed"] = self._arm_bot_crons_on_neutralized_copy()
        return result

    def _arm_bot_crons_on_neutralized_copy(self):
        """Switch on the inactive bot crons when ``database.is_neutralized`` is set; return
        the xml ids that were switched on. ``sudo``, because a bot binds itself as its own
        seam user, which may not read ``ir.cron``."""
        if not self.env["ir.config_parameter"].sudo().get_param("database.is_neutralized"):
            return []
        armed = []
        for xmlid in BOT_CRON_XMLIDS:
            cron = self.sudo().env.ref(xmlid, raise_if_not_found=False)
            if cron and not cron.active:
                cron.active = True
                armed.append(xmlid)
        return armed

    def _riverflow_carrier(self, origin_model):
        Model = self.env.get(origin_model) if origin_model else None
        if Model is None or not hasattr(Model, "bot_run_claim"):
            return None
        return Model

    def _work_consume(self, origin_model, res_id, work_token):
        Model = self._riverflow_carrier(origin_model)
        if Model is None:
            return super()._work_consume(origin_model, res_id, work_token)
        return Model.bot_run_claim(res_id, work_token)

    def _work_probe(self, origin_model, res_id, work_token):
        Model = self._riverflow_carrier(origin_model)
        if Model is None:
            return super()._work_probe(origin_model, res_id, work_token)
        check = Model.bot_token_check(res_id=res_id, work_token=work_token) or {}
        if check.get("ok"):
            return {"ok": True, "mine": True, "released": False, "state": check.get("state")}
        record = Model.browse(res_id).exists()
        released = bool(record) and record.bot_released_token == work_token
        out = {"ok": True, "mine": False, "released": released,
               "reason": "released by the turn" if released else str(check.get("reason") or ""),
               "state": record.state_id.name if record else check.get("state")}
        if released and record.state_id.bot_stage == "in_progress" and record.bot_claim_token:
            out["next_token"] = record.bot_claim_token
        return out

    def _work_release(self, origin_model, res_id, work_token, reason):
        Model = self._riverflow_carrier(origin_model)
        if Model is None:
            return super()._work_release(origin_model, res_id, work_token, reason)
        record = Model.browse(res_id).exists()
        if not record:
            return {"ok": False, "released": False, "reason": "unknown record"}
        if record.state_id.bot_stage != "in_progress" or record.bot_claim_token != work_token:
            return {"ok": True, "released": False, "state": record.state_id.name,
                    "reason": "not this token's live claim"}
        exit_transition = record.state_id.bot_timeout_transition()
        if not exit_transition:
            return {"ok": True, "released": False, "state": record.state_id.name,
                    "reason": "the state has no timeout exit; the reaper stays the backstop"}
        # No ``bot_reap`` context: this turn did not wait for the state's SLA.
        claim = Model.bot_claim(res_id, exit_transition.id, work_token=work_token) or {}
        return {"ok": bool(claim.get("ok")),
                "released": bool(claim.get("ok") and not claim.get("already")),
                "state": claim.get("state"),
                "reason": str(claim.get("reason") or reason or "released")}
