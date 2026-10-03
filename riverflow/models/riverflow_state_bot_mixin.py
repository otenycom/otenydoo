"""The generic bot-driven-workflow layer on riverflow.state.mixin (D173/D174, Layer 2).

The transition harness (hermeshost) drives ANY riverflow workflow with a bot: it reads
``bot_work_queue`` over /json/2/ and, per queued record, claims it (``bot_claim``) then fires an
isolated agent run. This layer lives on the STATE MIXIN — not ``riverflow.service`` — so any
workflow-bearing model (a service, or any model that carries ``riverflow.state.mixin``) is
bot-drivable. It resolves the harness work-item contract from the WORKFLOW SHAPE + the generic
state/transition roles (``state.bot_stage`` + ``transition.bot_role``) — never a hard-coded xml-id —
plus a ``_bot_task_spec()`` hook an app overrides for its domain (the skill / prompt / bot-safe DTO).
No ``oteny_bot`` dependency: the queue/claim/reaper are self-contained; the activity write-back is
the bot's job.

The mixin also owns the SAFETY BELT for a dead harness: it stamps ``bot_work_started_at`` when a
record enters a bot ``in_progress`` state, and ``_bot_reap_timeouts`` (an ir.cron) escalates any
record stuck past its state's ``bot_timeout_minutes`` SLA through the state's ``is_bot_timeout``
transition — so a crashed run that never reports back still hands the work back to a human.

TOKEN-FENCED CLAIM (the anti-double-file mechanism). A government filing is not idempotent, so
the state machine enforces AT MOST ONE live agent run per claim epoch with three primitives:

1. **CAS claim** — ``bot_claim`` locks the row (``FOR NO KEY UPDATE``), re-reads committed truth,
   and only then advances — two concurrent claimants can never both win.
2. **Dispatch token** — ``bot_claim_token`` is minted on EVERY entry into a bot ``in_progress``
   state and cleared on every exit, in the same vals/transaction as the state change. Only the
   claim winner holds the token; every exit from in-progress (work advance, escalate, reaper
   timeout) requires the current token, so a reaped-then-re-handed record's stale token is
   rejected everywhere — a zombie run cannot advance, escalate, or refile.
3. **Turn-start consume** — ``bot_run_claim`` stamps ``bot_run_started_at`` exactly once per
   token; the dispatcher (hh-discuss adapter / webhook harness) consumes it BEFORE any LLM
   activity, so a replayed dispatch message can never start a second agent run.

THE RUN/HUMAN MUTEX (multi-user concurrency). A bot's runs and its owner's people act on the same
records at random times, so this layer also owns two symmetric fences, both keyed on ONE predicate
— ``_bot_claim_is_live`` ("an agent run is happening RIGHT NOW"):

* ``_bot_dispatch_gate`` — a domain hook that DEFERS a dispatch (and a run's start) while the bot
  is unavailable to run, e.g. because a human is mid-way through an attended login for it.
  Deferred, never failed: the record keeps its place and the dispatch cron re-drives it.
* ``_bot_assert_human_transition_allowed`` — refuses a HUMAN transition out from under a live run,
  so a person cannot cancel a job in the seconds between the irreversible act and the record
  catching up with it. ONE transition is exempt: the state's ``is_bot_timeout`` hand-back
  (``riverflow.state.bot_timeout_transition``), which is the reaper's own exit. A person taking
  that door early makes the same judgement the clock makes on an abandoned run, and lands the
  record in the same state, so it is one hand-back with two triggers rather than a second meaning.
  Every other exit — *Cancel* above all — stays refused while a run is live.

Both are bounded by wall clock BY CONSTRUCTION: a claim stops being live the moment its state's
``bot_timeout_minutes`` SLA passes (the reaper then owns it), and a state with NO SLA is never
treated as live at all — an unbounded block would park a human forever, and liveness outranks the
fence. The hand-back exemption bounds it a second way, on demand: a person who can see the run is
dead does not have to sit out the SLA to say so.
"""

import logging
import secrets
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY

_logger = logging.getLogger(__name__)

# Fast re-dispatch window (the belt for a dispatch that never reached a live agent). A claim
# whose isolated run was NEVER consumed (``bot_run_started_at`` unset) past ``GRACE`` minutes is
# an orphan — the flagged dispatch was lost while the bot's gateway was down/reconnecting (its
# poll marker seeds PAST history on reconnect, so it never picks the old message up on its own).
# The dispatch cron re-posts it (same claim epoch) until ``CEILING`` minutes, after which the
# in-progress SLA reaper takes over. Re-fire is safe: ``bot_run_claim`` admits at most one run
# per epoch, so a re-post that races a live pickup is dropped (never a double side effect).
_BOT_REDISPATCH_GRACE_MINUTES = 3
_BOT_REDISPATCH_CEILING_MINUTES = 30
# A leftover SLA-less login park older than this stays human-owned.
# Drain resumes only a fresh park so a forgotten card cannot steal
# the slot (the 24836 class). The cron belt does not promote parks.
_BOT_LOGIN_PARK_FRESH_HOURS = 4


class RiverflowStateBotMixin(models.AbstractModel):
    _inherit = "riverflow.state.mixin"

    bot_work_started_at = fields.Datetime(
        "Bot Work Started",
        copy=False,
        help="When this record entered a bot `in_progress` state (stamped by the state mixin on the "
        "state change; cleared on leaving). The timeout reaper escalates a record whose dwell here "
        "exceeds the state's bot_timeout_minutes.",
    )
    bot_claim_token = fields.Char(
        "Bot Claim Token",
        copy=False,
        help="The epoch of ONE claim: minted on every entry into a bot `in_progress` state (same "
        "vals/transaction as the state change), cleared on every exit. Only the claim winner holds "
        "it; every bot_claim exit from in-progress requires it, so a stale (reaped/re-handed) run "
        "is rejected server-side. Rides the dispatch message so the isolated run can prove its "
        "epoch (bot_run_claim / bot_token_check).",
    )
    bot_released_token = fields.Char(
        "Released Work Token", copy=False, readonly=True,
        help="The last claim token that left a bot in-progress state through its own "
        "bot_claim (the turn's advance or hand-back). The bridge's work_probe tells an "
        "expected end from a lost claim by it.")
    bot_run_started_at = fields.Datetime(
        "Bot Run Started",
        copy=False,
        help="Stamped by bot_run_claim when the dispatcher consumes this claim's ONE agent run "
        "(before any LLM activity). Unset = the run for the current token has not started; a "
        "replayed dispatch message finds it set and is dropped. Cleared with the token on every "
        "state change.",
    )

    def _sync_workflow_with_state(self, vals):
        """Extend the mixin's state-change hook to stamp/clear the bot in-progress clock AND the
        claim-token epoch: entering a bot `in_progress` state stamps ``bot_work_started_at`` (for
        the timeout reaper) and mints a fresh ``bot_claim_token`` (the epoch of this one claim);
        any other state change clears both. ``bot_run_started_at`` resets on every state change
        (a fresh epoch's run has not been consumed). Minting here — inside write()/create(), same
        vals as the state change — makes claim + token (+ the app's flagged dispatch message)
        commit or roll back atomically."""
        super()._sync_workflow_with_state(vals)
        if "state_id" in vals:
            new_state = (
                self.env["riverflow.state"].browse(vals["state_id"]) if vals["state_id"] else False
            )
            in_progress = bool(new_state and new_state.bot_stage == "in_progress")
            queue_sla = bool(
                new_state
                and new_state.bot_stage == "queue"
                and new_state.bot_timeout_minutes > 0
            )
            stamp_clock = in_progress or queue_sla
            vals["bot_work_started_at"] = fields.Datetime.now() if stamp_clock else False
            vals["bot_claim_token"] = secrets.token_urlsafe(9) if in_progress else False
            vals["bot_run_started_at"] = False

    def write(self, vals):
        """Extend write to dispatch INLINE when a record enters a bot ``queue`` state: the
        hand-off (e.g. Kirsten's *Hand to Barney* wizard) triggers the bot in the same
        transaction — real time, not on the next cron tick (on odoo.sh cron workers run out of
        band and slowly). The 3-min dispatch cron stays as the catch-up belt for records whose
        inline dispatch failed (bot unbound, post error) or that were queued before activation.

        When a record LEAVES a slot-holding state (``in_progress`` or ``bot_login_hold``),
        drain the oldest queued peer of the same workflow. If no queue peer waits,
        resume one fresh SLA-less login park (``_bot_resume_login_park``). The 3-min
        cron is still the correctness belt for queue; it does not promote parks.
        Inline drain is latency sugar. Opt out with ``bot_no_inline_dispatch``
        (same flag as inline dispatch)."""
        leaving_slot = self.browse()
        if "state_id" in vals and not self.env.context.get("bot_no_inline_dispatch"):
            leaving_slot = self.filtered(
                lambda r: r.active and (
                    r.state_id.bot_stage == "in_progress" or r.state_id.bot_login_hold
                )
            )
        res = super().write(vals)
        if "state_id" in vals:
            self._bot_dispatch_inline()
            if leaving_slot:
                leaving_slot._bot_drain_peers()
        return res

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._bot_dispatch_inline()
        return records

    def _bot_dispatch_inline(self):
        """Dispatch every record of ``self`` that now sits in a bot ``queue`` state, inline in
        the caller's transaction — claim + flagged message + activity session commit (or roll
        back) WITH the hand-off write. Each record dispatches under its own savepoint: a failed
        dispatch rolls back cleanly and NEVER breaks the user's hand-off (the record stays
        queued; the cron belt retries). Runs sudo — the dispatch is system machinery (message
        post to the bot's channel + the activity session), not something the handing user needs
        rights for; parity with the cron, which runs as superuser. Opt out with context
        ``bot_no_inline_dispatch`` (tests exercising the raw queue/claim primitives)."""
        if self.env.context.get("bot_no_inline_dispatch"):
            return
        for record in self.filtered(lambda r: r.state_id.bot_stage == "queue"):
            try:
                with self.env.cr.savepoint():
                    record.sudo()._bot_dispatch_one()
            except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
                # A serialization failure, a deadlock or a lock timeout is Odoo's own retryable
                # class: the HTTP layer rolls the whole request back and replays it, up to five
                # times. Swallowing it here turned a millisecond race into the belt's three-minute
                # delay (test1, 2026-09-06: the drain's post into the filing channel collided with
                # the bot's own narration post, which bumps the same channel row).
                raise
            except Exception:  # noqa: BLE001 — never break the hand-off; the cron belt retries
                _logger.exception(
                    "inline bot dispatch failed for %s(%s) — record stays queued for the "
                    "dispatch cron belt", record._name, record.id)

    @api.model
    def bot_work_queue(self):
        """The bot-owned work waiting for an isolated agent run (the harness contract, generic).

        One dict per record in a ``queue`` bot-stage state, resolved to {res_model, res_id, state,
        claim_transition_id, in_progress_state, escalate_transition_id, expect_state_in,
        max_tool_turns, verbose} from the workflow shape + {skill, prompt, dto} from the domain
        ``_bot_task_spec()`` hook. A record whose workflow isn't bot-configured (no ``claim``
        transition) is skipped. No sudo — the caller is the bot user; kwargs are never named
        ``ids`` (the /json/2/ recordset selector)."""
        queue_states = self.env["riverflow.state"].search([("bot_stage", "=", "queue")])
        if not queue_states:
            return []
        items = []
        for record in self.search([("state_id", "in", queue_states.ids)]):
            item = record._bot_work_item()
            if item:
                items.append(item)
        return items

    def _bot_work_item(self):
        """Resolve ONE queued record to the harness work item from the workflow shape + the generic
        roles + the ``_bot_task_spec`` hook. ``None`` when the workflow isn't bot-configured (no
        ``claim`` transition out of the queue state). The skill + prompt + max tool turns come
        DECLARATIVELY from the claim transition (``bot_skill``/``bot_prompt``/``bot_max_tool_turns``
        — the workflow states what the run does, beside the ``bot_role`` that states when), falling
        back to the ``_bot_task_spec`` hook; the bot-safe DTO always comes from the hook (it is
        computed record data). ``res_model``/``res_id`` name the WORKFLOW record itself — distinct
        from ``riverflow.service``'s own ``res_model``/``res_id`` fields, which point at the
        service's *subject* (e.g. a log entry)."""
        self.ensure_one()
        state = self.state_id
        claim = state.from_transition_ids.filtered(lambda t: t.bot_role == "claim")[:1]
        if not claim:
            return None
        in_progress = claim.to_state_id
        outgoing = in_progress.from_transition_ids
        escalate = outgoing.filtered(lambda t: t.bot_role == "escalate")[:1]
        # the agent's expected success targets = the `work` transitions out of the in-progress state
        expect = outgoing.filtered(lambda t: t.bot_role == "work").mapped("to_state_id")
        spec = self._bot_task_spec()
        return {
            "res_model": self._name,
            "res_id": self.id,
            "state": state.name,
            "claim_transition_id": claim.id,
            "in_progress_state": in_progress.name,
            "escalate_transition_id": escalate.id if escalate else False,
            "expect_state_in": expect.mapped("name"),
            "skill": claim.bot_skill or spec.get("skill"),
            "prompt": claim.bot_prompt or spec.get("prompt", ""),
            "max_tool_turns": claim.bot_max_tool_turns or spec.get("max_tool_turns") or 0,
            "verbose": claim.bot_verbose,
            "dto": spec.get("dto") or {},
        }

    def _bot_task_spec(self):
        """Domain hook: the {skill, prompt, dto} for one record's isolated agent run. Base is
        generic/empty; an app module (e.g. crewradar_cuneus_sign for MFNL) overrides per record
        type to supply the bot-safe DTO (and, when the workflow doesn't declare them on the
        claim transition via ``bot_skill``/``bot_prompt``, the skill + anchored prompt)."""
        self.ensure_one()
        return {"skill": False, "prompt": "", "dto": {}}

    # --- the run/human mutex: is an agent run happening RIGHT NOW? ------------------------ #

    def _bot_claim_is_live(self):
        """True when an agent run on THIS record is LIVE — the single predicate the dance
        admission (may a human start an attended login?) and the human-transition guard (may a
        human move this record?) both key on. All three conditions must hold:

        * the record sits in a bot ``in_progress`` state (it is claimed at all);
        * its ONE run has been CONSUMED (``bot_run_started_at`` stamped by ``bot_run_claim``) — a
          claimed-but-never-started dispatch is not a run, it is a message waiting to be read, and
          blocking humans on one would mean blocking them on a possibly-dead gateway;
        * the run has not outlived its state's SLA — past that the timeout reaper owns the record,
          and treating a reaper-eligible zombie as live would block a human until the reaper got
          round to it.

        LIVENESS IS STRUCTURAL, not a promise: a state with no SLA (``bot_timeout_minutes`` <= 0)
        has no reaper and therefore no wall-clock bound, so its claim is NEVER live — a fence that
        cannot expire is worse than no fence, and the answer here decides whether a HUMAN is
        refused."""
        self.ensure_one()
        state = self.state_id
        if state.bot_stage != "in_progress":
            return False
        if not self.bot_run_started_at or not self.bot_work_started_at:
            return False
        if state.bot_timeout_minutes <= 0:
            return False
        deadline = self.bot_work_started_at + timedelta(minutes=state.bot_timeout_minutes)
        return fields.Datetime.now() < deadline

    def _bot_one_live_slot_defers(self):
        """True when another record of THIS workflow holds the bot's one live slot.

        Derived from service states. No occupancy row, no TTL, no release bookkeeping.

        Exclude-self: a record in a ``bot_login_hold`` state is never blocked by its own
        hold. That is the whole priority system — the parked record's login resume is
        always admitted; fresh work is not.

        Strict at dispatch for a fresh-work queue (``bot_stage == queue`` and not a
        login-hold): any other claimed in-progress peer defers, and a login-hold
        that occupies the slot (SLA or in_progress) defers too. A SLA-less park
        such as *Needs Login* does not occupy. *Register Login* is a queue state
        AND a login-hold — it uses the relaxed fence so a stray claimed peer
        cannot deadlock the occupant's resume.

        Relaxed at consume / re-post / login-hold resume: only a consumed, in-SLA peer
        defers (``_bot_claim_is_live``). Two claimed-unconsumed rows from a REPEATABLE
        READ race may both post; the consume fence plus the box serializer admit one
        browser Open. A second Open burns SMS; that happens at consume, never at post.

        A serializing write on the bot row would close even the extra Discuss post.
        That write is skipped until an impact dump shows a real double-post.
        """
        self.ensure_one()
        if not self.active:
            return False
        workflow = self.state_id.workflow_id
        if not workflow or not workflow.active:
            return False
        peer_states = self.env["riverflow.state"].search([
            ("workflow_id", "=", workflow.id),
            ("active", "=", True),
            "|",
            ("bot_stage", "=", "in_progress"),
            ("bot_login_hold", "=", True),
        ])
        if not peer_states:
            return False
        others = self.search([
            ("state_id", "in", peer_states.ids),
            ("id", "!=", self.id),
            ("active", "=", True),
        ])
        if not others:
            return False
        # A SLA-less login park (Needs Login) must not freeze the fleet.
        # The dance latch covers a live sign-in. Register Login / Relogin
        # carry a clock, so they still occupy the slot.
        if (not self.state_id.bot_login_hold
                and any(o._bot_login_hold_occupies_slot() for o in others)):
            return True
        claimed = others.filtered(
            lambda o: o.active and o.state_id.bot_stage == "in_progress"
        )
        if self.state_id.bot_stage == "queue" and not self.state_id.bot_login_hold:
            return bool(claimed)
        return any(o._bot_claim_is_live() for o in claimed)

    def _bot_login_hold_occupies_slot(self):
        """True when THIS login-hold should block a sibling's fresh work.

        Needs Login is human-owned and has no SLA. Treating it as an occupant
        parks every new Hand until someone clears that card — an unbounded
        block. Register Login (queue + SLA) and Relogin (in_progress) occupy.
        """
        self.ensure_one()
        state = self.state_id
        if not state.bot_login_hold:
            return False
        if state.bot_stage == "in_progress":
            return True
        return state.bot_timeout_minutes > 0

    def _bot_one_live_slot_occupant(self):
        """The sibling that occupies the one live slot, or empty."""
        self.ensure_one()
        if not self.active:
            return self.browse()
        workflow = self.state_id.workflow_id
        if not workflow or not workflow.active:
            return self.browse()
        peer_states = self.env["riverflow.state"].search([
            ("workflow_id", "=", workflow.id),
            ("active", "=", True),
            "|",
            ("bot_stage", "=", "in_progress"),
            ("bot_login_hold", "=", True),
        ])
        if not peer_states:
            return self.browse()
        others = self.search([
            ("state_id", "in", peer_states.ids),
            ("id", "!=", self.id),
            ("active", "=", True),
        ])
        for other in others:
            if other._bot_login_hold_occupies_slot():
                return other
        claimed = others.filtered(
            lambda o: o.active and o.state_id.bot_stage == "in_progress"
        )
        if self.state_id.bot_stage == "queue" and not self.state_id.bot_login_hold:
            return claimed[:1]
        live = claimed.filtered(lambda o: o._bot_claim_is_live())
        return live[:1]

    @api.model
    def _bot_one_live_slot_occupant_of_workflow(self, workflow):
        """The record that occupies the one live slot for ``workflow``, or empty.

        Same occupy rules as ``_bot_one_live_slot_occupant``, without
        exclude-self. A SLA-less login park is not an occupant. *Register
        Login* and *Relogin* occupy. A claimed ``in_progress`` record
        occupies. Used by the operator UI so it does not start from one
        service.
        """
        if not workflow or not workflow.active:
            return self.browse()
        peer_states = self.env["riverflow.state"].search([
            ("workflow_id", "=", workflow.id),
            ("active", "=", True),
            "|",
            ("bot_stage", "=", "in_progress"),
            ("bot_login_hold", "=", True),
        ])
        if not peer_states:
            return self.browse()
        records = self.search([
            ("state_id", "in", peer_states.ids),
            ("active", "=", True),
        ])
        for rec in records:
            if rec._bot_login_hold_occupies_slot():
                return rec
        claimed = records.filtered(
            lambda r: r.active and r.state_id.bot_stage == "in_progress"
        )
        return claimed[:1]

    @api.model
    def _bot_one_live_slot_queued_of_workflow(self, workflow, occupant=None):
        """Queued records of ``workflow`` that wait, occupant excluded.

        Same occupy rules: a *Register Login* occupant is not counted as
        waiting. A SLA-less leftover park is not ``queue``, so it is not
        in this set.
        """
        if not workflow or not workflow.active:
            return self.browse()
        queue_states = self.env["riverflow.state"].search([
            ("workflow_id", "=", workflow.id),
            ("bot_stage", "=", "queue"),
            ("active", "=", True),
        ])
        if not queue_states:
            return self.browse()
        domain = [
            ("state_id", "in", queue_states.ids),
            ("active", "=", True),
        ]
        if occupant:
            domain.append(("id", "!=", occupant.id))
        return self.search(domain)

    def _bot_drain_peers(self):
        """Dispatch the oldest queued peer of the same workflow.

        Called after a record leaves a slot-holding state. Each peer goes through
        ``_bot_dispatch_inline`` (savepoint per record, swallow-and-log). A failed
        drain never rolls back the exit that freed the slot. The cron belt retries
        queue. One attempt is enough: the claimed peer drains the next one when
        it exits. If no queue peer waits, resume one fresh SLA-less login park.
        """
        if self.env.context.get("bot_no_inline_dispatch"):
            return
        for record in self.filtered(lambda r: r.active):
            record._bot_drain_one_peer()

    def _bot_drain_one_peer(self):
        self.ensure_one()
        workflow = self.state_id.workflow_id
        if not workflow or not workflow.active:
            return
        queue_states = self.env["riverflow.state"].search([
            ("workflow_id", "=", workflow.id),
            ("bot_stage", "=", "queue"),
            ("active", "=", True),
        ])
        if queue_states:
            peer = self.search([
                ("state_id", "in", queue_states.ids),
                ("id", "!=", self.id),
                ("active", "=", True),
            ], order="id asc", limit=1)
            if peer:
                peer._bot_dispatch_inline()
                return
        self._bot_drain_login_park()

    def _bot_sla_less_login_hold_states(self, workflow):
        """States that park a human without occupying the one live slot."""
        holds = self.env["riverflow.state"].search([
            ("workflow_id", "=", workflow.id),
            ("bot_login_hold", "=", True),
            ("active", "=", True),
        ])
        return holds.filtered(
            lambda s: s.bot_stage != "in_progress" and s.bot_timeout_minutes <= 0
        )

    def _bot_login_park_since(self):
        """Cutoff for a leftover SLA-less login park. Tests patch this."""
        return fields.Datetime.now() - timedelta(hours=_BOT_LOGIN_PARK_FRESH_HOURS)

    def _bot_drain_login_park(self):
        """Resume one fresh SLA-less login park after a sibling freed the slot.

        Queue peers win. A leftover park older than
        ``_BOT_LOGIN_PARK_FRESH_HOURS`` stays human-owned so a forgotten
        card cannot steal the slot (24836 class). The cron belt does not
        promote parks. HR Continue after login still works.
        """
        self.ensure_one()
        if (self.state_id.bot_stage == "in_progress"
                or self._bot_login_hold_occupies_slot()):
            return
        if self._bot_one_live_slot_occupant():
            return
        workflow = self.state_id.workflow_id
        if not workflow or not workflow.active:
            return
        park_states = self._bot_sla_less_login_hold_states(workflow)
        if not park_states:
            return
        since = self._bot_login_park_since()
        park = self.search([
            ("state_id", "in", park_states.ids),
            ("id", "!=", self.id),
            ("active", "=", True),
            ("write_date", ">=", since),
        ], order="write_date desc, id desc", limit=1)
        if not park:
            return
        try:
            with self.env.cr.savepoint():
                park.sudo()._bot_resume_login_park()
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            raise  # Odoo's retryable class: let the HTTP layer replay the request
        except Exception:  # noqa: BLE001
            _logger.exception(
                "login-park resume failed for %s(%s) after %s(%s) freed the slot",
                park._name, park.id, self._name, self.id,
            )

    def _bot_resume_login_park(self):
        """Domain hook: advance this SLA-less login park into its resume queue.

        Base is a no-op. An app writes the park into the login-resume queue
        state so ``_bot_dispatch_inline`` claims it. Do not mint a claim here.
        """
        return False

    def _bot_dispatch_gate(self):
        """Domain hook: may a NEW isolated run be dispatched — or a dispatched one START — for this
        record right now? Return True to DEFER.

        Deferred is NEVER failed: the record keeps its state and its claim epoch, and the 3-min
        ``bot_dispatch_queue`` cron re-drives it (a queued record on the normal dispatch leg; an
        already-claimed one on the ``_bot_redispatch_stalled`` belt). So a caller must return True
        only for a condition that CLEARS ON ITS OWN — never one that needs a human.

        Base: defer when another record of this workflow holds the bot's one live slot
        (a claimed run, or a ``bot_login_hold`` park — see ``_bot_one_live_slot_defers``).
        An app that wires an attended-login latch overrides this and MUST call ``super()``
        after its cheap latch check. The override is also where the per-bot dance mutex is
        taken, and it MUST be taken without waiting (see ``oteny.bot.login_dance_blocks_run``):
        a dispatch that blocks on a lock could sit in a cycle, whereas a dispatch that defers
        simply comes back in three minutes."""
        self.ensure_one()
        return self._bot_one_live_slot_defers()

    def _bot_assert_human_transition_allowed(self, transition):
        """Refuse a HUMAN transition that would move this record out from under a LIVE agent run —
        raise ``UserError``, or return quietly.

        The window this closes is small and real: an isolated run's irreversible act (a portal
        submit) and the record catching up with it (write the proof, advance the state) are seconds
        apart, and a person clicking *Cancel* in between leaves a real, filed side effect behind a
        record that says cancelled. There is no way to un-submit, so the only correct answer for
        that transition is to make the human wait for the run.

        ONE transition is exempt: the state's ``is_bot_timeout`` exit (``bot_timeout_transition``).
        That is the hand-back door, and the reaper already takes it on this very record once the
        SLA passes. A person taking the same door early is making the same judgement the clock
        makes — "this run is not coming back" — so it carries the same meaning and the same
        landing state, never a second one. It is exempt for three reasons.

        First, the record needs a human abort at all: a run whose gateway died keeps the record
        in-progress, and the SLA is the only exit, which parks a person for up to
        ``bot_timeout_minutes`` in front of a bot that will never answer.

        Second, the hand-back is honest about an in-flight side effect in a way *Cancel* is not.
        It lands in the workflow's own owner-return state ("this is not done, look at it"), where
        *Cancel* asserts the work is over. So a submit that did land leaves a record that still
        reads as unfinished work, which is the truthful record either way.

        Third, the submit race is fenced one step closer to the act than this method can reach.
        The agent's own skill re-checks its epoch (``bot_token_check``) immediately before the
        irreversible call and stops on a lost claim, and the hand-back clears
        ``bot_claim_token`` in the same transaction as the state change — so an abort that
        commits before that check turns the submit into a no-op. What stays possible is a submit
        already in flight, and that residual is identical for the reaper's hand-back and the
        agent's own escalate, which this fence has never guarded.

        Called from the transition-execution choke points (the button and the wizard
        OK). A person is refused. ``bot_claim`` and a flagged bot open/OK pass
        ``riverflow_bot_caller`` so the claiming bot's own wizard still runs. The
        bound is the run finishing, the person taking the hand-back, or the SLA reaper
        handing the record back; the message names all three, because a refusal whose
        end the user cannot see is indistinguishable from a hang."""
        self.ensure_one()
        if self.env.context.get("riverflow_bot_caller"):
            return
        if not self._bot_claim_is_live():
            return
        abort = self.state_id.bot_timeout_transition()
        if abort and transition == abort:
            return
        waited = int((fields.Datetime.now() - self.bot_work_started_at).total_seconds() // 60)
        if abort:
            raise UserError(_(
                "%(bot_state)s is working on this right now — it started %(minutes)s minute(s) "
                "ago and usually takes a few minutes. Wait for it to finish and try again. If it "
                "never finishes, it is handed back automatically within %(sla)s minutes — you do "
                "not need to do anything. To take it back straight away, use "
                "%(abort)s instead.",
                bot_state=self.state_id.name,
                minutes=waited,
                sla=self.state_id.bot_timeout_minutes,
                abort=abort.name,
            ))
        raise UserError(_(
            "%(bot_state)s is working on this right now — it started %(minutes)s minute(s) ago and "
            "usually takes a few minutes. Wait for it to finish and try again. If it never "
            "finishes, it is handed back automatically within %(sla)s minutes — you do not need to "
            "do anything.",
            bot_state=self.state_id.name,
            minutes=waited,
            sla=self.state_id.bot_timeout_minutes,
        ))

    def _bot_lock_row(self):
        """Serialize concurrent claimants on THIS row: flush pending ORM writes, take the row lock
        (``FOR NO KEY UPDATE`` — the strength Odoo's own UPDATE takes, blocking, so the loser waits
        and then re-reads the winner's committed truth; unlike FOR UPDATE it doesn't block FK
        inserts referencing the row), then invalidate the cache so every re-check below reads the
        committed values, not a stale snapshot. The ORDER is load-bearing: flush → lock →
        invalidate."""
        self.ensure_one()
        self.env.cr.flush()  # flush ALL pending ORM writes so the lock sees current data
        self.env.cr.execute(
            f'SELECT id FROM "{self._table}" WHERE id = %s FOR NO KEY UPDATE', [self.id]
        )
        self.invalidate_recordset()

    @api.model
    def bot_claim(self, res_id, transition_id, work_token=None):
        """Advance ONE record through ``transition_id`` under the row lock — the single choke
        point for the harness claim (→ in-progress before running), the agent's work advance,
        the escalate (→ a human state on failure) and the reaper's timeout exit.

        CAS + token fence (checked against COMMITTED truth, in this order):

        * record/transition gone → ``{ok: False}``;
        * already in the target state → ``{ok: True, already: True}`` — NO token: a re-poll /
          replay must never be handed a live epoch (the running claim owns the record);
        * state ≠ the transition's source state → ``{ok: False}`` (CAS loss — someone advanced
          it under us);
        * exiting a bot ``in_progress`` state whose stored ``bot_claim_token`` doesn't match
          ``work_token`` → ``{ok: False, reason: 'stale work token'}`` — the zombie-run fence;
        * else open-and-save the transition wizard with no pause (same
          ``action_save`` pipeline a person uses) → ``{ok: True, state}`` +
          ``token`` when the target is a bot ``in_progress`` state (the
          freshly minted epoch — only the claim WINNER sees it).

        Kwargs are never named ``ids`` (the /json/2/ recordset selector); ``res_id`` names the
        record on THIS model (the model is implied by the /json/2/<model>/bot_claim endpoint)."""
        record = self.browse(res_id).exists()
        transition = self.env["riverflow.transition"].browse(transition_id).exists()
        if not record or not transition:
            return {"ok": False, "reason": "unknown record or transition"}
        record._bot_lock_row()
        if not record.exists():
            return {"ok": False, "reason": "record deleted"}
        if record.state_id == transition.to_state_id:
            return {"ok": True, "already": True, "state": record.state_id.name}
        if transition.from_state_id and record.state_id != transition.from_state_id:
            return {"ok": False, "state": record.state_id.name,
                    "reason": f"record is in {record.state_id.name!r}, not "
                    f"{transition.from_state_id.name!r}"}
        if (record.state_id.bot_stage == "in_progress" and record.bot_claim_token
                and work_token != record.bot_claim_token):
            return {"ok": False, "state": record.state_id.name, "reason": "stale work token"}
        # Domain precondition: a real-world invariant the CAS/token fence can't express (e.g.
        # MFNL: no *Filed* without a captured filing proof). Runs under the row lock, so it is
        # model-independent — a weak agent that TRIES the advance is refused here, not trusted.
        guard = record._bot_claim_guard(transition)
        if guard:
            return {"ok": False, "state": record.state_id.name, "reason": guard}
        exiting_epoch = (record.state_id.bot_stage == "in_progress" and record.bot_claim_token
                         and work_token == record.bot_claim_token)
        try:
            record._bot_claim_run_wizard(transition)
        except UserError as exc:
            return {"ok": False, "state": record.state_id.name, "reason": str(exc)}
        if exiting_epoch:
            record.bot_released_token = work_token
        result = {"ok": True, "state": record.state_id.name}
        if record.bot_claim_token:
            # the target is a bot in_progress state — hand the WINNER its fresh epoch
            result["token"] = record.bot_claim_token
        return result

    def _bot_claim_run_wizard(self, transition):
        """Open-and-save the transition wizard. No pause. No deadline-key copy."""
        self.ensure_one()
        record = self.with_context(
            transition_id=transition.id,
            riverflow_bot_caller=True,
            active_model=self._name,
            active_id=self.id,
            active_ids=self.ids,
        )
        action = record._prepare_transition_action()
        wizard_ctx = dict(action.get("context") or {})
        wizard_ctx["active_model"] = self._name
        wizard_ctx["active_id"] = self.id
        wizard_ctx["active_ids"] = self.ids
        wizard_ctx["riverflow_bot_caller"] = True
        Wizard = self.env[action["res_model"]].with_context(**wizard_ctx)
        defaults = Wizard.default_get(list(Wizard._fields))
        wizard = Wizard.create(defaults)
        result = wizard.action_save()
        # A save check that holds (an error, or a warning nobody confirmed)
        # leaves the record where it is. bot_claim has no one to tick the
        # confirmation, so the hold is a refusal, as a UserError is.
        if wizard._save_check_is_held(result):
            raise UserError("\n".join(f["message"] for f in result["params"]["findings"]))

    def _bot_claim_guard(self, transition):
        """Server-side precondition for a bot advance — the ONE place a domain layer can REFUSE
        a ``bot_claim`` transition even when the CAS/token fence would allow it. Base: no guard
        (falsy). An app overrides it to enforce an invariant the workflow STATE alone can't — MFNL
        makes a *File* → *Filed* advance require a captured filing proof, so a fabricated/skipped
        "Filed" is structurally impossible regardless of which model drives the run. Return a short
        reason string to REFUSE (``bot_claim`` then returns ``{ok: False, reason}`` and does not
        advance); return a falsy value to allow."""
        return None

    @api.model
    def bot_run_claim(self, res_id, work_token):
        """Consume the ONE agent run of the current claim epoch — called by deterministic
        dispatcher code (the hh-discuss adapter / the webhook harness) BEFORE any LLM/session
        activity. Under the row lock: require a bot ``in_progress`` state + a matching
        ``work_token`` + ``bot_run_started_at`` unset, then stamp it. A second consume, a
        wrong/stale token, or a post-reap replay all return ``{ok: False}`` — at most one agent
        run per dispatch, across message replays and multiple gateway processes."""
        record = self.browse(res_id).exists()
        if not record:
            return {"ok": False, "reason": "unknown record"}
        # The dispatch gate BEFORE the row lock — every path takes the per-bot mutex first and a
        # row lock second, so the two orders can never invert into a cycle. This is the fence that
        # makes "a run and an attended login never overlap" true BY CONSTRUCTION rather than by
        # timing: the latch stops FUTURE dispatches, but a message already sitting in the channel
        # (posted moments before the latch, or re-posted by the belt while the gateway was down)
        # would otherwise be picked up mid-dance and open a browser the human's finalize then
        # sweeps. Refusing the consume is free — the dispatcher is fail-closed and drops the
        # message without starting the agent, and the re-dispatch belt re-posts it once the dance
        # ends (same epoch, same token).
        if record._bot_dispatch_gate():
            return {"ok": False, "state": record.state_id.name,
                    "reason": "bot unavailable — deferred"}
        record._bot_lock_row()
        if not record.exists():
            return {"ok": False, "reason": "record deleted"}
        if record.state_id.bot_stage != "in_progress":
            return {"ok": False, "state": record.state_id.name, "reason": "not in progress"}
        if not work_token or work_token != record.bot_claim_token:
            return {"ok": False, "reason": "stale work token"}
        if record.bot_run_started_at:
            return {"ok": False, "reason": "run already consumed"}
        record.bot_run_started_at = fields.Datetime.now()
        return {"ok": True, "state": record.state_id.name}

    @api.model
    def bot_token_check(self, res_id=None, work_token=None, **_unused):
        """Read-only epoch probe for the RUNNING agent: ``{ok: True}`` iff the record is still in
        a bot ``in_progress`` state and ``work_token`` is its current ``bot_claim_token``. The
        skill runs this immediately before any irreversible action (portal submit) — not ok means
        the run was timed out/reaped and the work re-assigned, so it must STOP.

        ``res_id`` plus ``work_token`` is the documented call. A call that only
        sends ``work_token`` still resolves: the token names one claim epoch.
        Extra kwargs are ignored so a leftover ``number`` from
        ``bot_set_mfnl_concept_number`` cannot 422.
        """
        if not work_token:
            return {"ok": False, "reason": "missing work token"}
        if res_id:
            record = self.browse(res_id).exists()
        else:
            record = self.search([("bot_claim_token", "=", work_token)], limit=1)
        if not record:
            return {"ok": False, "reason": "unknown record"}
        if record.state_id.bot_stage != "in_progress":
            return {"ok": False, "state": record.state_id.name, "reason": "not in progress"}
        if work_token != record.bot_claim_token:
            return {"ok": False, "reason": "stale work token"}
        return {"ok": True, "state": record.state_id.name}

    @api.model
    def _bot_reap_timeouts(self):
        """Escalate bot records stuck past their state's SLA — the timeout reaper, run by
        an ir.cron on each concrete workflow-bearing model. For every ``in_progress`` or
        timeout-enabled ``queue`` state with a positive ``bot_timeout_minutes``, any record
        whose ``bot_work_started_at`` is older than the SLA is advanced through that state's
        ``is_bot_timeout`` transition (``bot_timeout_transition`` — the reaper's exit, distinct
        from the agent's own escalate, and the SAME door
        ``_bot_assert_human_transition_allowed`` lets a person take early on an abandoned run).
        The reaper passes the token it READS as its ``work_token``: ``bot_claim``
        re-checks it under the row lock, so a record whose run completed (or whose token
        rotated) between the read and the lock is a clean no-op — the reaper can never
        revert a just-completed record. A queue state has no claim token, so the fence
        does not apply. Returns the count reaped. This is the backstop for a harness that
        died mid-run and never reported back, and for a login-hold queue that froze."""
        now = fields.Datetime.now()
        states = self.env["riverflow.state"].search([
            ("bot_stage", "in", ("in_progress", "queue")),
            ("bot_timeout_minutes", ">", 0),
        ])
        reaped = 0
        for state in states:
            timeout_transition = state.bot_timeout_transition()
            if not timeout_transition:
                continue
            deadline = now - timedelta(minutes=state.bot_timeout_minutes)
            stuck = self.search([
                ("state_id", "=", state.id),
                ("bot_work_started_at", "!=", False),
                ("bot_work_started_at", "<", deadline),
            ])
            for record in stuck:
                claim = self.with_context(bot_reap=True).bot_claim(
                    record.id, timeout_transition.id, work_token=record.bot_claim_token)
                if claim.get("ok") and not claim.get("already"):
                    reaped += 1
        return reaped

    @api.model
    def bot_dispatch_queue(self):
        """Push each queued bot-owned record to its bot as an isolated turn (the Odoo-driven trigger
        that replaces the Oteny-side harness poll: the owner's Odoo asks the bot to act, and the bot's
        own channel poll picks it up — no external sweep, no webhook). The PRIMARY dispatch is the
        inline one on the hand-off write (``_bot_dispatch_inline`` — real time); this cron is the
        catch-up belt for records whose inline dispatch failed and for work queued before
        activation. Idempotent: the dispatch claims the record (→ its in-progress state), so it
        leaves the queue and is never re-dispatched. Returns the number dispatched. Run by an
        ir.cron on each concrete workflow-bearing model."""
        queue_states = self.env["riverflow.state"].search([("bot_stage", "=", "queue")])
        dispatched = 0
        if queue_states:
            for record in self.search([("state_id", "in", queue_states.ids)]):
                if record._bot_dispatch_one():
                    dispatched += 1
        # The belt for a lost dispatch: re-post an already-claimed record whose isolated run was
        # never consumed (the orphan a gateway-down window leaves) — same tick, safe by the fence.
        dispatched += self._bot_redispatch_stalled()
        return dispatched

    @api.model
    def _bot_redispatch_stalled(self):
        """Re-post the flagged dispatch for a claimed record whose isolated run was NEVER consumed
        (``bot_run_started_at`` unset) between the grace and the ceiling — the fast recovery for a
        dispatch lost while the bot's gateway was down (its poll marker seeds past the flagged
        message on reconnect, so it never self-recovers). Re-uses the STANDING claim epoch (no
        re-claim — the token holds). A run that DID start then died (``bot_run_started_at`` set) is
        NOT re-fired here — the consume fence would drop it — so the in-progress SLA reaper escalates
        it instead. Returns the count re-dispatched."""
        inprog = self.env["riverflow.state"].search([("bot_stage", "=", "in_progress")])
        if not inprog:
            return 0
        now = fields.Datetime.now()
        floor = now - timedelta(minutes=_BOT_REDISPATCH_CEILING_MINUTES)
        deadline = now - timedelta(minutes=_BOT_REDISPATCH_GRACE_MINUTES)
        stalled = self.search([
            ("state_id", "in", inprog.ids),
            ("bot_run_started_at", "=", False),          # the run was never consumed …
            ("bot_work_started_at", "!=", False),
            ("bot_work_started_at", "<", deadline),      # … and it has sat past the grace …
            ("bot_work_started_at", ">", floor),         # … but not so long the SLA reaper owns it
        ])
        count = 0
        for record in stalled:
            if record._bot_redispatch_one():
                count += 1
        return count

    def _bot_redispatch_one(self):
        """Re-post the flagged dispatch for THIS already-claimed, never-consumed in-progress record.
        Resolves the work item from the claim transition INTO the current state (reusing the standing
        ``bot_claim_token``) and hands it to the domain ``_bot_redispatch`` hook. True when re-fired."""
        self.ensure_one()
        if self._bot_dispatch_gate():
            return False  # deferred — a later queue pass re-fires it (still inside the ceiling)
        item = self._bot_inprogress_work_item()
        if not item:
            return False
        return bool(self._bot_redispatch(item, self._bot_dispatch_prompt(item)))

    def _bot_inprogress_work_item(self):
        """The harness work item for a record ALREADY in its bot ``in_progress`` state (for a
        re-dispatch): the same shape as ``_bot_work_item`` but resolved from the claim transition
        INTO the current state, and carrying the STANDING ``bot_claim_token`` (no new claim). None
        when the current state isn't a bot in-progress state reachable by a claim transition, or the
        token is missing (nothing to re-fire)."""
        self.ensure_one()
        state = self.state_id
        if state.bot_stage != "in_progress" or not self.bot_claim_token:
            return None
        claim = self.env["riverflow.transition"].search(
            [("to_state_id", "=", state.id), ("bot_role", "=", "claim")], limit=1)
        if not claim:
            return None
        outgoing = state.from_transition_ids
        escalate = outgoing.filtered(lambda t: t.bot_role == "escalate")[:1]
        expect = outgoing.filtered(lambda t: t.bot_role == "work").mapped("to_state_id")
        spec = self._bot_task_spec()
        return {
            "res_model": self._name,
            "res_id": self.id,
            "state": state.name,
            "claim_transition_id": claim.id,
            "in_progress_state": state.name,
            "escalate_transition_id": escalate.id if escalate else False,
            "expect_state_in": expect.mapped("name"),
            "skill": claim.bot_skill or spec.get("skill"),
            "prompt": claim.bot_prompt or spec.get("prompt", ""),
            "max_tool_turns": claim.bot_max_tool_turns or spec.get("max_tool_turns") or 0,
            "verbose": claim.bot_verbose,
            "token": self.bot_claim_token,
            "dto": spec.get("dto") or {},
        }

    def _bot_redispatch(self, item, prompt):
        """Domain hook: RE-post the flagged dispatch for an already-claimed record using the
        standing token in ``item['token']`` — NO re-claim (the record is already in-progress; only
        the isolated run was lost). Base is a no-op (returns False); an app that wires a bot
        overrides it to re-post the flagged message to the bot's channel with that token. Kept off
        ``oteny_bot`` here so riverflow stays a pure engine."""
        return False

    def _bot_dispatch_one(self):
        """Resolve THIS queued record's work item and hand it to the domain ``_bot_dispatch``
        hook (the shared leg of the inline dispatch and the cron belt). True when dispatched.

        A gated bot (``_bot_dispatch_gate``) DEFERS: the record stays queued and the 3-min cron
        re-drives it, so nothing is lost and nobody sees an error."""
        self.ensure_one()
        if self._bot_dispatch_gate():
            return False
        item = self._bot_work_item()
        return bool(item and self._bot_dispatch(item, self._bot_dispatch_prompt(item)))

    def _bot_dispatch_prompt(self, item):
        """The THIN isolated-turn instruction for one queued record — names the skill + the record
        + the transition's declared task instruction (``bot_prompt``, a static instruction — no
        record data), NEVER the DTO (the bot fetches that itself over its uplink, so no PII rides
        the channel). Without the declared prompt the workflow's per-transition instruction (e.g.
        the MFNL unattended contract) would reach only the webhook escape hatch, not the primary
        Discuss dispatch."""
        self.ensure_one()
        # B-PROMPT1: name the ONE first uplink read by id — the channel never carries the DTO.
        # A bot whose Talent declares `record_pin` already has the record in its system
        # prompt, read by the platform at dispatch (the SOURCE RECORD SNAPSHOT frame); that
        # snapshot is what it files, so it must not fetch a second copy. Every other bot
        # still makes the one read.
        thin = (
            f"Run the '{item.get('skill') or ''}' task for {item.get('state')} record "
            f"#{self.id} (riverflow.service). Load the skill. If your system prompt carries "
            f"a SOURCE RECORD SNAPSHOT of this record, that snapshot is this record's data: "
            f"do not fetch it again. Otherwise your first uplink call is ONE search_read "
            f"with domain [['id', '=', {self.id}]] for this record's fields — there is no "
            f"record payload in this message. Complete the work, and advance the record. "
            f"Act only on this one record."
        )
        declared = (item.get("prompt") or "").strip()
        return f"{thin}\n{declared}" if declared else thin

    def _bot_dispatch(self, item, prompt):
        """Domain hook: dispatch ONE isolated turn for this record (its work ``item`` + a thin
        ``prompt``) to its bot. Base is a no-op (returns False) — an app that wires a bot (e.g.
        crewradar → the oteny_bot Discuss seam) overrides it to CLAIM the record (via
        ``item['claim_transition_id']``) and post the flagged message to the bot's channel; a truthy
        return counts it dispatched. The claim's fresh ``token`` MUST ride the flagged message (the
        oteny_bot work header) — no token, no dispatch. Kept off ``oteny_bot`` here so riverflow
        stays a pure engine."""
        return False
