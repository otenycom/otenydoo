"""The generic Oteny business-bot models (Layer 1 — no workflow / domain coupling).

An Oteny bot is external (its own machine), so it writes its activity back into this Odoo over
the /json/2/ uplink; the owner reviews it here. Everything is domain-agnostic: a session's origin
is a soft ``(model, res_id)`` reference so a workflow engine or an app module can attach a session
to its own record without this addon depending on it.
"""

import secrets
from datetime import timedelta

import mistune
from psycopg2 import IntegrityError

from odoo import SUPERUSER_ID, _, Command, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import html_sanitize

# Renders a session's markdown ``response``/``request`` for display, the same way the
# platform's Discuss adapter renders the model's final reply before message_post
# (hermeshost catalog/plugins/hh-discuss/discuss_wire.py::markdown_to_discuss_html).
# hard_wrap=True turns a single "\n" into "<br>" (that adapter's python-markdown
# "nl2br" extension) — without it a plain-text multi-line reply collapses onto one
# line in HTML, since a bare "\n" inside a <p> is just whitespace to a browser.
_MARKDOWN = mistune.create_markdown(hard_wrap=True)

# The leading marker that tells the bot's gateway to run a Discuss message as a FRESH, isolated
# agent turn (its own session) instead of a turn in the accumulating channel chat — the Discuss
# trigger that replaces the Oteny-side harness poll. WIRE CONTRACT: this string MUST match the
# hh-discuss adapter's ISOLATED_SENTINEL (hermeshost catalog/plugins/hh-discuss/discuss_wire.py).
ISOLATED_TURN_SENTINEL = "[oteny:isolated]"

# The machine-readable work header a WORK-carrying dispatch places right after the sentinel:
# names the workflow record + the claim epoch's dispatch token. WIRE CONTRACT: the format MUST
# stay parseable by the hh-discuss adapter's WORK_HEADER_RE (hermeshost discuss_wire.py) —
# `\[oteny:work:([\w.]+):(\d+):([A-Za-z0-9_-]+)\]`. The adapter consumes the token
# (bot_run_claim) BEFORE starting the agent run, so a replayed message can never run twice;
# an old adapter sees the header as prose (harmless), and a headerless isolated message keeps
# working (plain conversation / scenario driver).
WORK_HEADER_FMT = "[oteny:work:{res_model}:{res_id}:{token}]"

# An OPTIONAL flag a dispatch may carry (right after the isolated sentinel) to ask the run to
# STREAM per-tool narration into the channel live — a noisy debug trace only wanted while a Talent
# author is diagnosing a run. Off by default (the end-of-run summary always lands regardless).
# WIRE CONTRACT: must match the hh-discuss adapter's VERBOSE_SENTINEL (hermeshost discuss_wire.py),
# parity-pinned in that repo's tests/test_discuss_adapter.py.
VERBOSE_SENTINEL = "[oteny:verbose]"

# The human/agent-readable token instructions appended to a work-carrying dispatch. The token is
# the run's proof of its claim epoch: it must ride every bot_claim advance/escalate, and the
# skill must probe bot_token_check before anything irreversible.
WORK_TOKEN_TRAILER = (
    "\nYour work token is {token}. Pass work_token={token} on every bot_claim "
    "advance/escalate. Run bot_token_check immediately before any irreversible action "
    "(portal submit) — if not ok, STOP: you were timed out and the work re-assigned."
)

# How long a started attended-login DANCE latches the bot's dispatch path before it expires by
# itself. The TTL is what makes the latch LIVENESS-SAFE: no OK, no cancel, a closed laptop, a
# crashed browser — the bot resumes dispatching on wall clock alone, with no cron and no
# operator.
#
# This is a FLOOR, not a mirror. It used to say that 15 minutes mirrored the broker's own
# handoff window (`browser_handoff_minutes`), and that a dance outliving the window it minted
# is over whatever the human does. Neither half survives. The login-handoff mint stopped using
# `browser_handoff_minutes` and uses an INACTIVITY clock instead
# (`LOGIN_HANDOFF_INACTIVITY_S`, 600 s at the time of writing), which is a different number and
# a different KIND of number: an idle window dies inside 10 minutes, while a window a human
# keeps working in is refreshed and can outlive any fixed latch. So the two clocks drifted
# apart in both directions at once, and neither was wrong about its own job.
#
# The relationship that actually has to hold is one-way: the latch must cover the window, never
# the reverse. A latch that ends first is the dangerous end — the bot resumes dispatching while
# a human is still typing, and the run's browser is swept out from under them. So the latch
# takes the greater of this floor and what the broker itself reports for the window it just
# minted (`login_dance_extend`, called from the attach), and it never shrinks. A latch that
# outlives a dead window costs nothing: re-open finds nothing and says so.
LOGIN_DANCE_MINUTES = 15


class OtenyBot(models.Model):
    _name = "oteny.bot"
    _description = "Oteny Business Bot"
    _order = "name"
    # one bot per tenant reference — the write-back's ensure_bot relies on this to be a true upsert
    # (a check-then-create alone races under overlapping sweeps). NULL uplink_ref may repeat (a
    # manually-created bot without an uplink), per Postgres NULL semantics. Odoo 19 constraint style.
    _uplink_ref_uniq = models.Constraint(
        "unique(uplink_ref)",
        "An Oteny bot's uplink reference must be unique.",
    )

    name = fields.Char(required=True)
    bot_user_id = fields.Many2one(
        "res.users", string="Bot User",
        help="The Odoo login the bot authenticates as over /json/2/ (its scoped seam user).")
    discuss_channel_id = fields.Many2one(
        "discuss.channel", string="Home Channel",
        help="The Discuss channel the bot converses in with its owner/operators, and the one "
        "it posts proactive news into. Always served; extra rooms come from channel_ids "
        "(a declared role) or from an operator simply adding the bot to a channel.")
    channel_ids = fields.One2many(
        "oteny.bot.channel", "bot_id", string="Role Channels",
        help="Which room plays which of the bot's declared ROLES. A role is named by the "
        "Talent the bot runs (its routing.channels); binding it here is what gives that room "
        "the role's own persona and preloaded skills.")
    uplink_ref = fields.Char(
        "Uplink Ref", index=True, copy=False,
        help="The Oteny-side tenant reference (e.g. hh00140) the bot writes its activity under. "
        "Opaque to this Odoo; the bot supplies it when it calls record_activity.")
    active = fields.Boolean(default=True)
    session_ids = fields.One2many("oteny.bot.session", "bot_id", string="Activity")
    session_count = fields.Integer(compute="_compute_session_count")
    login_dance_until = fields.Datetime(
        "Login Dance Until", copy=False,
        help="Set while a human is completing an attended sign-in for this bot (the 'open a "
        "login browser → sign in → save' dance). While it is in the FUTURE the bot's dispatch "
        "path is latched: no new isolated run is dispatched and no dispatched run may start, so "
        "the human's browser session and the bot's never overlap. Cleared on save/cancel and "
        "EXPIRES BY ITSELF — an abandoned dance unlatches the bot on wall clock alone, with no "
        "cron and no operator.")
    login_dance_user_id = fields.Many2one(
        "res.users", string="Login Dance By", copy=False,
        help="Who started the login dance currently holding the latch — so a second person is "
        "told WHO is signing in rather than just 'busy'.")
    jar_clear_available = fields.Boolean(
        compute="_compute_jar_clear_available",
        help="Whether this Oteny account has a stop-and-reset credential wired. "
             "Gates the button so it is hidden rather than broken.")

    login_dance_token = fields.Char(
        "Login Dance Token", copy=False,
        help="The epoch of ONE dance: minted on every login_dance_start and required by "
        "login_dance_stop, which is a COMPARE-and-clear. The latch is per-BOT but the screens "
        "that release it are per-user and per-record, so 'stop the dance' must mean 'stop MY "
        "dance' — without the epoch, anyone's Cancel would release whoever's dance was live and "
        "re-open the run/login overlap the latch exists to close. Same fence as bot_claim_token.")
    login_dance_session_id = fields.Char(
        "Login Dance Session", copy=False,
        help="The cloud-browser session THIS dance minted. The screen that minted it holds the "
        "same id, but a screen is per-tab and per-record, so a closed tab or a reopened wizard "
        "loses it. Keeping the id on the BOT is what lets the person who started the sign-in "
        "re-open that same browser window instead of minting a second one — a second mint "
        "supersedes the first, which throws away the half-finished sign-in and spends one of the "
        "portal's few daily sign-ins.")
    login_dance_is_active = fields.Boolean(
        compute="_compute_login_dance_is_active",
        help="True while a login dance latch is in the future. The form uses this "
        "to show Clear login dance. Prefer this over the lock so a list read "
        "never waits.",
    )

    @api.depends("session_ids")
    def _compute_session_count(self):
        for bot in self:
            bot.session_count = len(bot.session_ids)

    # --- the dance/run mutex (multi-user concurrency) ------------------------------------ #
    # A bot's browser does two kinds of work that must never overlap: its OWN isolated runs
    # (already serialized one-at-a-time on its machine) and a HUMAN's attended login. They
    # share one cookie profile, so an overlap means the login's flush clobbers the run's
    # session — or the login's cleanup kills the run's live browser mid-filing. The two take
    # turns via ONE per-bot Postgres advisory lock + this TTL'd latch:
    #
    #   dance side (login_dance_hold)  → EXCLUSIVE, blocking. It is the FIRST lock its caller
    #                                    takes, so it can never be part of a cycle.
    #   run side  (login_dance_blocks_run) → try-SHARED, NEVER waits. Contention means DEFER,
    #                                    which is free: the bot_dispatch_queue cron re-drives
    #                                    a deferred dispatch. Shared, so concurrent dispatches
    #                                    for one bot don't exclude each OTHER — only the dance.
    #
    # No deadlock is constructible: only the dance ever waits, and it waits holding nothing.

    def _login_dance_lock_key(self):
        """The deterministic per-bot key both sides of the dance/run mutex hash. ONE key per
        bot and one only — a single global lock ordering, so there is no second key to invert
        it against."""
        self.ensure_one()
        return f"oteny.bot:{self.id}:dispatch"

    def login_dance_active(self):
        """True while a login dance holds the latch — set AND still in the future. Expiry needs
        no cron and no sweep: the comparison against ``now`` IS the expiry."""
        self.ensure_one()
        return bool(self.login_dance_until and self.login_dance_until > fields.Datetime.now())

    def login_dance_hold(self):
        """DANCE side: take this bot's dance/run mutex EXCLUSIVELY for the caller's transaction,
        then re-read the latch from COMMITTED truth. flush → lock → invalidate, the same
        load-bearing order as riverflow's ``_bot_lock_row``: a read taken before the lock could
        be a pre-lock snapshot.

        Postgres releases the lock when the transaction ends, so it stands across whatever the
        caller does next (for the login gate: the broker mint). That is deliberate — a dispatch
        must not slip in between the admission check and the latch — and it costs nothing,
        because nothing that matters waits on it (the run side tries and defers). The only
        possible waiter is a SECOND dance start, bounded by the first's own HTTP timeout."""
        self.ensure_one()
        self.env.cr.flush()
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))", [self._login_dance_lock_key()])
        self.invalidate_recordset(["login_dance_until", "login_dance_user_id"])

    def login_dance_blocks_run(self):
        """RUN side: True when NO isolated run may be dispatched or started for this bot right
        now — either a dance holds the latch, or one is being taken THIS INSTANT (the mutex is
        held exclusively and the latch is about to appear).

        NEVER waits. It TRIES the shared lock and reads contention as a defer, so a dispatch is
        only ever delayed (the 3-min ``bot_dispatch_queue`` cron re-drives it), never failed —
        and a dispatch can never sit in a lock cycle. Callers must treat True as 'try again
        later', never as an error."""
        self.ensure_one()
        self.env.cr.flush()
        self.env.cr.execute(
            "SELECT pg_try_advisory_xact_lock_shared(hashtext(%s))",
            [self._login_dance_lock_key()])
        if not self.env.cr.fetchone()[0]:
            return True  # a dance start owns the mutex right now — defer, don't wait
        self.invalidate_recordset(["login_dance_until", "login_dance_user_id"])
        return self.login_dance_active()

    def login_dance_start(self, minutes=None):
        """Latch the bot for an attended login dance (TTL'd — see LOGIN_DANCE_MINUTES) and return
        the dance's EPOCH TOKEN. The caller MUST hold the mutex (``login_dance_hold``) so the latch
        and any concurrent dispatch's latch-read are ordered.

        The token is the caller's proof of ownership: keep it and hand it back to
        ``login_dance_stop``. A second dance start (a takeover — the newest click wins, matching the
        broker's handoff supersede) mints a FRESH token, which is exactly what makes the superseded
        screen's later Cancel/OK a no-op instead of a release of somebody else's live dance.

        sudo: writing the bot is manager-only, but starting a dance is an HR act on a record the
        HR user may only read."""
        self.ensure_one()
        token = secrets.token_urlsafe(9)
        self.sudo().write({
            "login_dance_until": fields.Datetime.now() + timedelta(minutes=minutes or LOGIN_DANCE_MINUTES),
            "login_dance_user_id": self.env.uid,
            "login_dance_token": token,
        })
        return token

    def login_dance_attach_session(self, token, session_id):
        """Record the browser session THIS dance minted — a COMPARE-and-set on the epoch token.

        Same fence as ``login_dance_stop`` and for the same reason: a screen whose dance was
        superseded still holds the old token, and it must not overwrite the newcomer's session
        id. Returns True when it wrote."""
        self.ensure_one()
        if not token or not session_id:
            return False
        self.login_dance_hold()
        if token != self.sudo().login_dance_token:
            return False
        self.sudo().login_dance_session_id = session_id
        return True

    def login_dance_stance(self):
        """Who holds the attended sign-in for this bot right now, read from committed truth.

        Returns ``{"state", "holder", "until", "session_id"}``. ``state`` is ``free`` when no
        dance holds the latch — an EXPIRED dance reads free, because the TTL comparison IS the
        expiry — ``mine`` when the current user started it, and ``theirs`` when somebody else did.

        Ownership is per ODOO USER, deliberately, and not per screen. A popup blocker that eats
        the login tab, a tab closed by accident and a wizard opened again all destroy per-screen
        state, and those are exactly the cases a person has to recover from. The user written on
        the bot record survives all three. The cost is that two people sharing one login are one
        person here, so a team that shares one login gets the recovery answer for both of them.

        CALL THIS WITHOUT ``sudo``. It sudoes internally for the field reads, because an HR user
        may not read the bot record, but the ``mine`` test compares against ``self.env.uid`` — and
        a sudoed caller carries the superuser id, which would read every dance as somebody else's.

        No lock is taken. This is paint, so a stale read costs at worst a refused click that says
        why, never a wrong write. Every write path still re-checks under ``login_dance_hold``."""
        self.ensure_one()
        bot = self.sudo()
        if not bot.login_dance_active():
            return {"state": "free", "holder": False, "until": False, "session_id": False}
        return {
            "state": "mine" if bot.login_dance_user_id.id == self.env.uid else "theirs",
            "holder": bot.login_dance_user_id.name or _("another user"),
            "until": bot.login_dance_until,
            "session_id": bot.login_dance_session_id or False,
        }

    def login_dance_extend(self, token, minutes=None):
        """Push THIS dance's expiry out — a COMPARE-and-extend on the epoch token.

        Never shortens. The latch's only job is to cover the sign-in that is actually
        happening, so moving it in is a way to hand a human's browser to the bot mid-2FA,
        and there is no caller that wants that. A stale token from a superseded screen
        extends nothing, the same fence ``login_dance_stop`` carries.

        Call it with the window's own remaining life when the broker reports one, and on
        a human's real interaction otherwise. Returns True when it moved the latch."""
        self.ensure_one()
        if not token:
            return False
        self.login_dance_hold()
        bot = self.sudo()
        if token != bot.login_dance_token:
            return False
        until = fields.Datetime.now() + timedelta(
            minutes=minutes or LOGIN_DANCE_MINUTES)
        if bot.login_dance_until and bot.login_dance_until >= until:
            return False
        bot.login_dance_until = until
        return True

    @api.depends("login_dance_until")
    def _compute_login_dance_is_active(self):
        now = fields.Datetime.now()
        for bot in self:
            bot.login_dance_is_active = bool(
                bot.login_dance_until and bot.login_dance_until > now
            )

    def login_dance_force_clear(self):
        """Manager override: empty the dance latch without the epoch token.

        ``login_dance_stop`` is compare-and-clear. A stuck latch whose token
        is gone would no-op and look like a hang. Take the mutex, then write
        the three fields empty. Manager group only.
        """
        self.ensure_one()
        if not self.env.user.has_group("oteny_bot.group_oteny_bot_manager"):
            raise UserError(_(
                "Only an Oteny Bot Manager can clear a login dance."
            ))
        self.login_dance_hold()
        self.sudo().write({
            "login_dance_until": False,
            "login_dance_user_id": False,
            "login_dance_token": False,
            "login_dance_session_id": False,
        })

    def action_login_dance_force_clear(self):
        self.login_dance_force_clear()
        return True

    @api.depends_context("uid")
    def _compute_jar_clear_available(self):
        """Hide the button rather than break it. `_broker_token` refuses to lend one
        purpose's credential to another, so an unconfigured stop-and-reset would answer
        401 while the button still looked available."""
        ok = self.env["oteny.broker.client"]._broker_purpose_configured("jar-clear")
        for rec in self:
            rec.jar_clear_available = ok

    def action_stop_and_reset_browser(self):
        """Close this bot's live cloud browser and forget any saved sign-in.

        The button an operator presses when a run is stuck inside the portal. It stops
        the window FIRST, because that is what ends the stuck run, and forgets the saved
        sign-in SECOND, so the next run cannot restore whatever wedged this one.

        It stops a window even when a turn is using it. That is the point: a run stuck in
        a portal dialog is exactly what this is for. The record that turn was working
        parks itself back to not-started the way it does for any lost browser, so nothing
        is left half-filed.

        Anyone who can open this form may press it. The blast radius is one bot's browser,
        and waiting for the right person is worse than an interrupted run.
        """
        self.ensure_one()
        res = self.env["oteny.broker.client"]._broker_post(
            "/v1/browser/reset", {}, purpose="jar-clear") or {}
        closed = int(res.get("sessions_closed") or 0)
        forgot = bool(res.get("jar_forgotten"))
        if closed and forgot:
            msg = _("Browser stopped (%s window(s)) and the saved sign-in was cleared.", closed)
        elif closed:
            msg = _("Browser stopped (%s window(s)). There was no saved sign-in to clear.", closed)
        elif forgot:
            msg = _("The saved sign-in was cleared. No browser window was open.")
        else:
            msg = _("Nothing to stop: no browser window was open and no sign-in was saved.")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": _("Stop and Reset browser"), "message": msg,
                       "type": "success", "sticky": False},
        }

    def login_dance_stop(self, token):
        """COMPARE-and-clear: release the latch ONLY if ``token`` is the CURRENT dance's epoch.
        Returns True when it released, False when it was somebody else's dance (or none).

        The compare is the whole point. The latch is per-BOT, but the screens that release it are
        per-user and per-record and there are several people working at once — so an unconditional
        clear would mean *any* Cancel releases *whoever* is mid-sign-in, dropping a run straight
        into their login and re-opening the exact overlap this latch exists to close. Owning the
        dance, not merely having a screen open, is what earns the release.

        Takes the mutex for the same reason ``login_dance_start`` needs it: without it, a stop that
        read a matching token could still land its write AFTER a concurrent takeover committed, and
        destroy the new dance's latch. flush → lock → invalidate, then compare against committed
        truth. Never required for correctness (the TTL is the backstop) — it just returns the bot to
        work now instead of in 15 minutes."""
        self.ensure_one()
        if not token:
            return False
        self.login_dance_hold()
        if token != self.sudo().login_dance_token:
            return False
        self.sudo().write({
            "login_dance_until": False, "login_dance_user_id": False, "login_dance_token": False,
            "login_dance_session_id": False,
        })
        return True

    def login_dance_stop_own(self):
        """Release the latch when the CURRENT dance is the caller's own: its user is
        ``env.user``. A dance is owned by the person signing in, not by the screen that
        started it. On 2026-09-17 a person started the dance from one Ask dialog,
        reloaded the form, and saved from a fresh dialog that held no token; the save
        released nothing and every dispatch deferred until the TTL ran out. The token
        still guards a stop from anyone else's screen (``login_dance_stop``): a
        colleague's save never releases this person's dance. Same mutex, same compare
        against committed truth. Returns True when it released."""
        self.ensure_one()
        self.login_dance_hold()
        bot = self.sudo()
        if not bot.login_dance_active() or bot.login_dance_user_id != self.env.user:
            return False
        bot.write({
            "login_dance_until": False, "login_dance_user_id": False, "login_dance_token": False,
            "login_dance_session_id": False,
        })
        return True

    @api.model
    def record_activity(self, uplink_ref, session, turns=None):
        """The seam the bot calls over /json/2/ to log ONE exchange (owner-visibility, generic).

        Creates an ``oteny.bot.session`` (+ optional child ``turns``) for the bot identified by
        ``uplink_ref``. ``sudo`` internally — the bot's least-privilege key need not carry write
        on the log models. The security boundary is ``bot_id``: a caller can only ever log under the
        bot its OWN ``uplink_ref`` resolves to (an uplink reaches only its own owner's Odoo), never
        another bot's. The ``origin`` (``origin_model`` + ``origin_res_id``) is an ADVISORY soft
        reference the bot self-reports — it is not validated here (this addon is domain-agnostic and
        the record may since be gone), and a bot that can log is one that already has write access to
        those records, so a mis-stated origin is a self-report artefact, not a privilege escalation.
        ``session`` is the session vals ({name, kind, request, response, outcome, outcome_detail,
        origin_model, origin_res_id, started_at, duration_s}); ``turns`` an optional list of turn
        vals. Kwargs are never named ``ids`` (the /json/2/ recordset selector). Returns
        ``{ok, session_id}``."""
        bot = self.sudo().search([("uplink_ref", "=", uplink_ref)], limit=1)
        if not bot:
            return {"ok": False, "reason": f"no oteny.bot with uplink_ref {uplink_ref!r}"}
        allowed = set(self.env["oteny.bot.session"]._fields)
        vals = {k: v for k, v in (session or {}).items() if k in allowed}
        vals["bot_id"] = bot.id
        if turns:
            tallowed = set(self.env["oteny.bot.turn"]._fields)
            vals["turn_ids"] = [
                (0, 0, {k: v for k, v in (t or {}).items() if k in tallowed}) for t in turns]
        rec = self.env["oteny.bot.session"].sudo().create(vals)
        return {"ok": True, "session_id": rec.id}

    @api.model
    def record_run(self, work_token, run=None, turns=None):
        """The seam the bot calls at the END of an isolated dispatch run to fill the open,
        token-matched session with its verdict + forensics (D174 hybrid — "a run must never be
        silent"). It ADOPTS the ``dispatched`` session opened at dispatch (correlated by the claim
        epoch's ``work_token``) instead of forking a new one, so the customer sees ONE row that
        goes from *Dispatched* → its real outcome + reason seconds after the run — no 120-min
        timeout wait, no SSH.

        Field policy: forensic fields (``response``, ``duration_s``) always attach; the run's
        SELF-ASSESSED ``outcome``/``outcome_detail`` are written only while the session is still
        ``dispatched`` — i.e. the workflow state machine has not already closed it. So a hard
        failure (403 spiral, empty reply → ``error`` + the tool-failure breakdown) lands its
        reason, but a state-machine ``ok``/``escalated`` (the record advanced) is never downgraded
        by a softer bot guess. Possessing ``work_token`` proves the claim epoch (same trust model
        as ``bot_claim``); ``sudo`` internally (the bot's least-privilege key need not carry write
        on the log models). Kwargs are never named ``ids``. Returns ``{ok, session_id}``."""
        if not work_token:
            return {"ok": False, "reason": "record_run needs a work_token"}
        Session = self.env["oteny.bot.session"].sudo()
        session = Session.search([("work_token", "=", work_token)], order="id desc", limit=1)
        if not session:
            return {"ok": False, "reason": f"no session for work_token {work_token!r}"}
        allowed = set(Session._fields)
        vals = {k: v for k, v in (run or {}).items() if k in allowed}
        # Never let a soft self-report clobber a terminal outcome the state machine already proved.
        if session.outcome not in ("dispatched", False):
            vals.pop("outcome", None)
            vals.pop("outcome_detail", None)
        if turns:
            tallowed = set(self.env["oteny.bot.turn"]._fields)
            # Replace any prior turns (a re-report is idempotent, not additive).
            vals["turn_ids"] = [Command.clear()] + [
                Command.create({k: v for k, v in (t or {}).items() if k in tallowed})
                for t in turns]
        session.write(vals)
        return {"ok": True, "session_id": session.id}

    # ------------------------------------------------------------------ #
    # The work contract: the three verbs the platform calls on a dispatch.  #
    # Named without any engine. The bridge finds the record by the session   #
    # the token names and asks the engine of that record's model through the #
    # ``_work_*`` hooks below; an engine module extends ``oteny.bot`` and     #
    # answers for its own models. No engine → fail-closed, as the platform   #
    # treats every refusal.                                                   #
    # ------------------------------------------------------------------ #

    def _work_session(self, work_token):
        if not work_token:
            return self.env["oteny.bot.session"].sudo()
        return self.env["oteny.bot.session"].sudo().search(
            [("work_token", "=", work_token)], order="id desc", limit=1)

    @api.model
    def work_consume(self, work_token):
        """Consume the one agent run of the dispatch that carries ``work_token``.

        Called by the platform before any model activity. ``{ok: True, state}`` once
        per dispatch; ``{ok: False, reason}`` for a second consume, a stale token, an
        unknown token, or a record without an engine. The platform drops the dispatch
        on ``ok: False``; the engine's own belts redispatch it."""
        session = self._work_session(work_token)
        if not session:
            return {"ok": False, "reason": f"unknown work token {work_token!r}"}
        out = self._work_consume(session.origin_model, session.origin_res_id, work_token)
        if out is None:
            return {"ok": False, "reason": f"no engine for {session.origin_model!r}"}
        return out

    @api.model
    def work_probe(self, work_token):
        """Whether the turn that holds ``work_token`` still owns its record.

        ``{ok: True, mine: True}`` while the claim is live. ``{ok: True, mine: False,
        released: True}`` when the record left the bot's hands through the turn's own
        advance (an expected end, not a loss; ``next_token`` names the new epoch when
        the advance minted one). ``{ok: True, mine: False, released: False, reason}``
        when the claim was lost (timed out, reaped, re-assigned). ``{ok: False}`` only
        for an unknown token or a record without an engine, which the platform reads
        as "cannot tell", never as a loss."""
        session = self._work_session(work_token)
        if not session:
            return {"ok": False, "mine": False, "released": False,
                    "reason": f"unknown work token {work_token!r}"}
        out = self._work_probe(session.origin_model, session.origin_res_id, work_token)
        if out is None:
            return {"ok": False, "mine": False, "released": False,
                    "reason": f"no engine for {session.origin_model!r}"}
        return out

    @api.model
    def work_release(self, work_token, reason=None, run=None):
        """Give the record back after a turn that ended without an outcome (a hung
        model stream, a crashed run). ``run`` (optional) is the ``record_run`` payload
        written to the session first, so the reason lands even when the engine holds
        no exit. Returns ``{ok, released, reason, state}``: ``released`` is True only
        when the engine moved the record out of the bot's hands under this token."""
        session = self._work_session(work_token)
        if not session:
            return {"ok": False, "released": False, "reason": f"unknown work token {work_token!r}"}
        if run:
            self.record_run(work_token, run=run)
        out = self._work_release(session.origin_model, session.origin_res_id, work_token, reason)
        if out is None:
            return {"ok": False, "released": False, "reason": f"no engine for {session.origin_model!r}"}
        return out

    # The engine hooks. Each returns None when this bridge holds no engine for
    # ``origin_model``; an engine module extends oteny.bot and answers for its models.

    def _work_consume(self, origin_model, res_id, work_token):
        return None

    def _work_probe(self, origin_model, res_id, work_token):
        return None

    def _work_release(self, origin_model, res_id, work_token, reason):
        return None

    @api.model
    def attach_browser_sessions(self, work_token, session_ids=None):
        """Mid-run attach of opaque broker browser session ids (Watch live).

        The Discuss adapter calls this as soon as the first ``browser_*`` tool opens
        a cloud-browser session — so Bot Activity shows *Live now* + Watch while the
        run is still ``dispatched``. Ids only (never URLs). Merges with any ids already
        on the token-matched session; never changes ``outcome`` (a closed run stays
        closed; a dispatched run stays dispatched). Possessing ``work_token`` proves
        the claim epoch. Returns ``{ok, session_id, browser_session_ids}``."""
        if not work_token:
            return {"ok": False, "reason": "attach_browser_sessions needs a work_token"}
        Session = self.env["oteny.bot.session"].sudo()
        session = Session.search([("work_token", "=", work_token)], order="id desc", limit=1)
        if not session:
            return {"ok": False, "reason": f"no session for work_token {work_token!r}"}
        incoming = [s for s in (session_ids or []) if isinstance(s, str) and s]
        existing = session.browser_session_ids or []
        if not isinstance(existing, list):
            existing = []
        merged = list(existing)
        for sid in incoming:
            if sid not in merged:
                merged.append(sid)
        if merged != existing:
            session.browser_session_ids = merged
        return {
            "ok": True,
            "session_id": session.id,
            "browser_session_ids": merged,
        }

    @api.model
    def _canonical_same_user_bot(self, *, exclude_id=None):
        """Oldest active ``oteny.bot`` for the calling bot user (seeded xmlid when present).

        Business apps seed one channel-bound bot per seam user; Hand-to-Barney / domain
        dispatch resolve that seed by xmlid. Fresh box provision must rehome *that* row,
        never leave it on a destroyed uplink_ref while a fork holds the channel."""
        uid = self.env.uid
        if not uid:
            return self.sudo().browse()
        domain = [("bot_user_id", "=", uid), ("active", "=", True)]
        if exclude_id:
            domain.append(("id", "!=", exclude_id))
        return self.sudo().search(domain, order="id asc", limit=1)

    @api.model
    def _collapse_onto_canonical(self, bot, uplink_ref):
        """If ``bot`` is a younger same-user fork, move ``uplink_ref`` (+ channel) onto the seed.

        Returns the canonical recordset (empty when no collapse). Deactivates the fork so
        ``unique(uplink_ref)`` and xmlid dispatch stay on one row."""
        if not bot:
            return self.sudo().browse()
        canonical = self._canonical_same_user_bot(exclude_id=bot.id)
        if not canonical or canonical.id > bot.id:
            return self.sudo().browse()
        # Free the unique slot, then point the seed at the live box.
        if bot.uplink_ref == uplink_ref:
            bot.uplink_ref = False
        if bot.discuss_channel_id and not canonical.discuss_channel_id:
            canonical.discuss_channel_id = bot.discuss_channel_id
            bot.discuss_channel_id = False
        canonical.uplink_ref = uplink_ref
        if not canonical.bot_user_id:
            canonical.bot_user_id = bot.bot_user_id or self.env.uid
        bot.active = False
        return canonical

    @api.model
    def ensure_bot(self, uplink_ref, name=None):
        """Idempotently ensure an ``oteny.bot`` exists for ``uplink_ref`` (owner-visibility, generic).

        The bot lives outside this Odoo (its own machine); it has no way to pre-seed its own record
        here, so the uplink calls this the first time the bot acts — the "Barney" record then simply
        appears in the owner's Odoo, ready to accrue activity. ``sudo`` internally (the bot's
        least-privilege key need not carry create on ``oteny.bot``); a true upsert on ``uplink_ref``,
        never renamed on a re-call (the owner may have relabelled it). The ``unique(uplink_ref)``
        constraint makes it race-safe: two overlapping sweeps that both miss the search collide on
        create, and the loser returns the winner's record rather than forking the log. Kwargs are
        never named ``ids`` (the /json/2/ recordset selector). Returns ``{ok, bot_id, created}``
        (``adopted`` / ``rehomed`` when a seeded or stale same-user row was reused)."""
        bot = self.sudo().search([("uplink_ref", "=", uplink_ref)], limit=1)
        if bot:
            collapsed = self._collapse_onto_canonical(bot, uplink_ref)
            if collapsed:
                return {"ok": True, "bot_id": collapsed.id, "created": False, "rehomed": True}
            return {"ok": True, "bot_id": bot.id, "created": False}
        # Adopt a pre-seeded bot: a business app (e.g. crewradar) seeds the oteny.bot with its
        # discuss_channel_id + bot_user_id but no uplink_ref (it can't know the Oteny tenant ref).
        # The first write-back — authenticated AS that bot user — claims the seeded record by
        # setting its ref, so the channel-bound record is reused (the dispatch needs it), not forked.
        seeded = self.sudo().search(
            [("bot_user_id", "=", self.env.uid), ("uplink_ref", "in", (False, ""))],
            order="id asc", limit=1)
        if seeded:
            seeded.uplink_ref = uplink_ref
            return {"ok": True, "bot_id": seeded.id, "created": False, "adopted": True}
        # Fresh provision / --fresh destroy: the seeded row still carries the *old* uplink_ref.
        # Rehome it onto the new box instead of creating a mute sibling (xmlid Hand-to-Barney
        # would keep dispatching against the seed while the channel sat on the fork).
        stale = self._canonical_same_user_bot()
        if stale and stale.uplink_ref and stale.uplink_ref != uplink_ref:
            stale.uplink_ref = uplink_ref
            return {"ok": True, "bot_id": stale.id, "created": False, "rehomed": True}
        try:
            with self.env.cr.savepoint():
                bot = self.sudo().create({"uplink_ref": uplink_ref, "name": name or uplink_ref})
                bot.flush_recordset()   # force the INSERT so a unique-violation fires in the savepoint
            return {"ok": True, "bot_id": bot.id, "created": True}
        except IntegrityError:
            # a concurrent ensure_bot won the create race (unique(uplink_ref)); return its record.
            bot = self.sudo().search([("uplink_ref", "=", uplink_ref)], limit=1)
            if bot:
                collapsed = self._collapse_onto_canonical(bot, uplink_ref)
                if collapsed:
                    return {"ok": True, "bot_id": collapsed.id, "created": False, "rehomed": True}
                return {"ok": True, "bot_id": bot.id, "created": False}
            return {"ok": False, "reason": f"could not ensure oteny.bot for {uplink_ref!r}"}

    def bind_discuss_channel(self, uplink_ref, channel_id=None):
        """Ensure ``uplink_ref`` owns the Discuss channel (rehome seed; clear orphan siblings).

        A second provision for a new tenant ref must not fork a channel-less bot while the
        seeded xmlid row keeps the old uplink_ref — Hand-to-Barney resolves the seed and
        stays mute. ``ensure_bot`` rehomes the same-user seed onto ``uplink_ref``; this
        method then moves ``discuss_channel_id`` onto that row and clears every other bot
        that held the channel. ``channel_id`` optional — when omitted, take the channel from
        a sibling that shares ``bot_user_id`` (or the caller's uid). Returns
        ``{ok, bot_id, channel_id}``."""
        ensured = self.ensure_bot(uplink_ref)
        if not ensured.get("ok"):
            return ensured
        bot = self.sudo().browse(ensured["bot_id"])
        channel = None
        if channel_id:
            channel = self.env["discuss.channel"].sudo().browse(int(channel_id)).exists()
        if not channel:
            uid = bot.bot_user_id.id or self.env.uid
            donor = self.sudo().search([
                ("bot_user_id", "=", uid),
                ("id", "!=", bot.id),
                ("discuss_channel_id", "!=", False),
            ], limit=1)
            if not donor:
                # Also look at inactive forks (collapse may have just deactivated one that
                # still held the channel until we moved it — or an older clear left it here).
                donor = self.sudo().with_context(active_test=False).search([
                    ("id", "!=", bot.id),
                    ("discuss_channel_id", "!=", False),
                    "|", ("bot_user_id", "=", False), ("bot_user_id", "=", self.env.uid),
                ], limit=1)
            channel = donor.discuss_channel_id if donor else bot.discuss_channel_id
        if not channel:
            return {"ok": False, "reason": "no_channel", "bot_id": bot.id}
        others = self.sudo().with_context(active_test=False).search([
            ("discuss_channel_id", "=", channel.id),
            ("id", "!=", bot.id),
        ])
        if others:
            others.write({"discuss_channel_id": False})
        if not bot.bot_user_id:
            bot.bot_user_id = self.env.uid
        bot.discuss_channel_id = channel
        return {"ok": True, "bot_id": bot.id, "channel_id": channel.id}

    def mint_uplink_key(self, key_name):
        """Rotate the bot user's ``/json/2/`` key and return the plaintext, once.

        Provisioning a bot hands it this key as its uplink credential. Odoo has no RPC way for
        an administrator to make a key for ANOTHER user: ``res.users.apikeys.make_key`` sits
        behind ``check_identity`` (an interactive password prompt), and ``generate`` only renews
        the caller's own key. So the mint needed an ``odoo-bin shell`` on the server, which on
        odoo.sh means SSH from an IP somebody whitelisted by hand. This method lets an
        administrator's ``/json/2/`` key do it over HTTPS instead.

        Only a system administrator may call it, and only for the user this bot record names
        (``bot_user_id``), never for a login passed as an argument. The previous key of the
        same name is revoked first, so a re-run rotates instead of piling up keys; a bot still
        running on the old key loses its uplink until it is given the new one. The key is
        global (scope None, which ``/json/2/`` requires) and never expires. Odoo stores only
        its hash, so the return value is the one moment the plaintext exists: the caller must
        not log it. Returns ``{ok, key, db, bot_user_id}``; ``db`` is the database the key
        belongs to, which an odoo.sh caller cannot read any other way.
        """
        self.ensure_one()
        if not self.env.is_system():
            raise AccessError(_("Only an administrator can mint a bot's uplink key."))
        if not self.bot_user_id:
            raise UserError(_("Bot %s has no bot user to mint a key for.", self.name))
        Key = self.env["res.users.apikeys"]
        Key.sudo().search(
            [("user_id", "=", self.bot_user_id.id), ("name", "=", key_name)])._remove()
        # with_user() clears superuser mode, so sudo() goes LAST: only a superuser environment
        # may create a key without an expiration date (_check_expiration_date).
        key = Key.with_user(self.bot_user_id).sudo()._generate(None, key_name, None)
        return {
            "ok": True,
            "key": key,
            "db": self.env.cr.dbname,
            "bot_user_id": self.bot_user_id.id,
        }

    # --- channel admission: which rooms this bot may serve (the D248 two lanes) ---------- #

    @api.model
    def _bot_for_seam_user(self):
        """The bot asking — resolved from the AUTHENTICATED login, never from an argument.

        The bot reaches this Odoo as its own scoped seam user (``bot_user_id``), so the
        caller's identity IS the answer. That is the whole access control on the admission
        read: a bot can only ever ask about itself, and a compromised uplink key cannot
        enumerate another bot's rooms by passing a different id."""
        return self.sudo().search([("bot_user_id", "=", self.env.uid)], limit=1)

    def _autoauth_refusal(self, channel):
        """Why ``channel`` may NOT be auto-served, or '' when it passes every gate.

        "Add the bot and it answers" is only safe because three independent gates hold, and
        all are checked HERE (server-side) where group membership and channel membership
        actually live — the bot's own machine is never asked to police its own admission:

        1. **Somebody actually added it.** A channel carrying ``group_ids`` auto-subscribes
           every member of those groups — Odoo's own mechanism, and how the default
           ``general`` channel works. The bot's seam user is an internal user, so it is
           swept into such rooms the moment the login is created. Nobody chose that, so it
           is not consent; an operator who genuinely wants the bot company-wide adds it to
           a normal channel or binds a role.
        2. **Who put it there.** The channel's creator must hold the Oteny Bot Operator
           group, and must be a *person* — never the bot's own seam login. An ordinary
           internal user cannot conjure a room, drop the bot in, and get an answering agent
           that reads this Odoo through the bot's grants; and since a seam login is normally
           an HR user (which implies the operator group), a bot that talked itself into
           creating a channel would otherwise be admitted into a room of its own making.
        3. **Who can read the answers.** Every member must be an internal user — no portal
           user, no guest. Barney's replies quote employee and client data; a room with an
           outside reader is refused outright rather than quietly served.

        A DM ('chat') passes the same gates — a private room with an operator is a
        legitimate casual lane. Live-chat/WhatsApp channel types never are."""
        self.ensure_one()
        if channel.channel_type not in ("channel", "group", "chat"):
            return f"channel type {channel.channel_type!r} is not a staff room"
        if channel.group_ids:
            return "auto-subscription channel — nobody added the bot to it"
        creator = channel.create_uid
        if creator and creator == self.bot_user_id:
            return "created by the bot's own login — a bot may not authorize itself"
        if not creator or not creator.has_group("oteny_bot.group_oteny_bot_operator"):
            return (f"created by {creator.name or '?'}, who is not an Oteny Bot Operator")
        for member in channel.channel_member_ids:
            if member.guest_id:
                return "has a guest member"
            partner = member.partner_id
            if partner == self.bot_user_id.partner_id:
                continue
            users = partner.user_ids
            if not users or all(u.share for u in users):
                return f"has a non-internal member ({partner.name or '?'})"
        return ""

    @api.model
    def channels_for_bot(self):
        """Which Discuss channels the calling bot may serve → ``{ok, channels, skipped}``.

        The client Odoo owns this verdict (D248) — the bot's adapter only consumes it. Two
        lanes come back, distinguished by ``declared``:

        * **declared** — the home channel plus every ``channel_ids`` role binding. A
          deliberate act by whoever configured the bot, so it is served even when the
          operator has switched autoauth off on the Oteny side.
        * **casual** (``declared: False``) — a room the bot was simply ADDED to, admitted
          only when it passes ``_autoauth_refusal``'s two gates. Refusals come back in
          ``skipped`` with their reason so a room that quietly fails the gate shows up in
          the bot's log instead of just being absent.

        ``role`` names the persona/skills the bot should run in that room; blank means the
        casual desk persona. Channels the bot is not a member of are never listed — it
        could not read them anyway."""
        bot = self._bot_for_seam_user()
        if not bot:
            return {"ok": False, "reason": "no oteny.bot is bound to this login"}
        channels, skipped, seen = [], [], set()

        def _add(channel, role, declared):
            seen.add(channel.id)
            channels.append({"id": channel.id, "name": channel.name or "",
                             "role": role, "declared": declared})

        if bot.discuss_channel_id:
            _add(bot.discuss_channel_id, "", True)
        for binding in bot.channel_ids:
            if binding.channel_id and binding.channel_id.id not in seen:
                _add(binding.channel_id, binding.role, True)
            elif binding.channel_id:
                # The home channel IS the role channel — name its role rather than
                # dropping the binding (the common single-room deployment).
                for row in channels:
                    if row["id"] == binding.channel_id.id and not row["role"]:
                        row["role"] = binding.role
        member_of = self.env["discuss.channel"].sudo().search([
            ("channel_member_ids.partner_id", "=", bot.bot_user_id.partner_id.id),
        ]) if bot.bot_user_id.partner_id else self.env["discuss.channel"]
        for channel in member_of:
            if channel.id in seen:
                continue
            refusal = bot._autoauth_refusal(channel)
            if refusal:
                skipped.append({"id": channel.id, "name": channel.name or "",
                                "reason": refusal})
            else:
                _add(channel, "", False)
        return {"ok": True, "channels": channels, "skipped": skipped}

    def _channel_for_role(self, role):
        """The room bound to ``role``, falling back to the home channel.

        A client that never bound the role still gets its filings in the one room it has —
        the home channel doubles as the dedicated lane (matching the adapter's
        ``home_role``), so a role-targeted dispatch can never land nowhere."""
        self.ensure_one()
        if role:
            binding = self.channel_ids.filtered(lambda b: b.role == role)[:1]
            if binding.channel_id:
                return binding.channel_id
        return self.discuss_channel_id

    def dispatch_isolated_turn(self, prompt, work=None, verbose=False, role=None):
        """Dispatch ONE isolated agent turn to this bot over Discuss (the trigger that replaces the
        Oteny-side harness poll). Posts ``<sentinel> [work header] {prompt} [token trailer]`` into
        the bot's channel; the bot's gateway poll picks it up and runs it as a FRESH, isolated
        session — not a turn in the accumulating team chat. The dispatch is a real channel message,
        so the owner sees it. ``work`` (optional) is the token-fenced work reference
        ``{res_model, res_id, token}`` from a winning ``bot_claim``: it renders the machine
        header the adapter consumes (bot_run_claim — at most one run per dispatch) plus the token
        trailer the run needs for its advances. Requires ``discuss_channel_id``. Returns
        ``{ok, message_id, channel_id}`` (``{ok: False}`` if unbound).

        ``role`` (D248) targets the room the client bound that role to, so a workflow's
        dispatches land in the dedicated lane — where the run starts with that role's persona
        and preloaded skills — instead of in whatever casual room shares the bot. Unbound
        role → the home channel, which is the dedicated lane by default."""
        self.ensure_one()
        channel = self._channel_for_role(role)
        if not channel:
            return {"ok": False, "reason": "bot has no discuss_channel_id"}
        head = ISOLATED_TURN_SENTINEL
        if verbose:
            # opt-in: ask the run to narrate each uplink tool call into the channel live
            head += " " + VERBOSE_SENTINEL
        tail = ""
        if work:
            head += " " + WORK_HEADER_FMT.format(
                res_model=work["res_model"], res_id=work["res_id"], token=work["token"])
            tail = WORK_TOKEN_TRAILER.format(token=work["token"])
        body = f"{head} {(prompt or '').strip()}{tail}".strip()
        # OdooBot signs the dispatch, whichever user's transaction fires it. A drain fires
        # INSIDE the bot's own uplink call (the escalate that freed the slot), and ``sudo()``
        # keeps that user as the author. The bot's gateway drops a message its own partner
        # authored (the echo guard in hh-discuss ``select_inbound``), so a dispatch signed by
        # the bot is never consumed and sits until the 3-min belt re-posts it as OdooBot
        # (test1 session 51, 2026-09-05). ``with_user(SUPERUSER_ID)`` is the cron's own path.
        msg = channel.with_user(SUPERUSER_ID).message_post(
            body=body, message_type="comment", subtype_xmlid="mail.mt_comment")
        return {"ok": True, "message_id": msg.id, "channel_id": channel.id}

    def action_open_sessions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.name,
            "res_model": "oteny.bot.session",
            "view_mode": "list,form",
            "domain": [("bot_id", "=", self.id)],
            "context": {"search_default_bot_id": self.id},
        }

    def action_open_discuss_channel(self):
        """Open this bot's Discuss home channel via the standard Discuss client action."""
        self.ensure_one()
        channel = self.discuss_channel_id
        if not channel:
            raise UserError(_("This bot has no Discuss home channel."))
        return channel._get_access_action()


class OtenyBotChannel(models.Model):
    """One of the bot's declared ROLES, bound to one of this Odoo's Discuss channels (D248).

    A bot runs a Talent that declares roles (``routing.channels`` — e.g. ``mfnl_filing``),
    each with its own persona and preloaded skills. Which ROOM plays a role is the client's
    call, not Oteny's: the control plane never learns this Odoo's channel ids, so the pairing
    is made here and read back over the admission seam. Binding a role also routes that
    workflow's dispatches (``dispatch_isolated_turn(role=…)``) into the room.

    A room without a binding is not shut out — it is simply CASUAL: the bot answers there
    with the desk persona. Roles buy focus, not authority; the bot's grants are its seam
    user's, identical in every room."""

    _name = "oteny.bot.channel"
    _description = "Oteny Bot Role Channel"
    _order = "bot_id, role"
    _role_uniq = models.Constraint(
        "unique(bot_id, role)",
        "A bot can bind each role to only one channel.",
    )

    bot_id = fields.Many2one("oteny.bot", required=True, ondelete="cascade", index=True)
    role = fields.Char(
        required=True,
        help="The role name as the bot's Talent declares it (routing.channels[].role), "
        "e.g. permit_filing. Must match exactly — a typo silently leaves the room casual.")
    channel_id = fields.Many2one(
        "discuss.channel", required=True, ondelete="cascade", string="Channel",
        help="The room that plays this role. The bot must be a member of it.")


class OtenyBotSession(models.Model):
    _name = "oteny.bot.session"
    _description = "Oteny Bot Activity (one exchange)"
    _order = "started_at desc, id desc"
    # This model IS the bot's activity log: the bot writes each exchange here over /json/2/ and
    # the owner reads it under Bot Activity. Auditing it copies every machine-written record a
    # second time into oteny.audit.log, which buries the real business changes an auditor came
    # to read. There is no human edit to attribute either -- ordinary users are read-only on it
    # (security/ir.model.access.csv), so only the bot's seam user ever writes. Same call the
    # equivalent in-process log already makes (wilma.debug.session / wilma.debug.turn).
    _oteny_audit_ignore = True

    bot_id = fields.Many2one("oteny.bot", required=True, ondelete="cascade", index=True)
    discuss_channel_id = fields.Many2one(
        related="bot_id.discuss_channel_id",
        string="Home Channel",
        help="The bot's Discuss home channel. Used to open that room from this activity.")
    name = fields.Char("Task", help="A short label for the exchange (the task / request summary).")
    kind = fields.Selection(
        [("conversational", "Conversational"), ("isolated_turn", "Isolated turn")],
        default="isolated_turn",
        help="conversational = a chat turn in the channel session; isolated_turn = a fresh, "
        "single-purpose run anchored on one task (the workflow-driven path).")
    request = fields.Text("Request / anchored task", readonly=True)
    response = fields.Text("Response", readonly=True)
    outcome = fields.Selection(
        [("dispatched", "Working"), ("ok", "OK"), ("halted", "Halted / handed back"),
         ("escalated", "Escalated"), ("timeout", "Timed out"), ("error", "Error")],
        index=True, readonly=True,
        help="dispatched = the isolated turn was posted and is (presumed) running (the Working "
        "pill); the workflow layer closes it deterministically to ok / escalated / timeout when "
        "the record exits its bot in-progress state.")
    outcome_detail = fields.Char("Close Reason", readonly=True)
    response_first_line = fields.Char(
        "Outcome Detail", compute="_compute_response_first_line",
        help="First non-empty line of response. List label only — not the stored close reason.")
    request_preview = fields.Text(
        compute="_compute_request_preview",
        help="First three lines of the request, for the Summary rollup.")
    work_token = fields.Char(
        "Work Token", index=True, readonly=True, copy=False,
        help="The dispatch token of the claim epoch this session records (D174 write-back). Set "
        "at dispatch; the workflow layer matches on it to close the session when the record "
        "exits the bot in-progress state — exact correlation, no LLM self-report needed.")
    # Generic origin — the Odoo record this exchange is about (e.g. a riverflow.service). A SOFT
    # reference (model name + id), not a Many2one, so this generic addon never depends on the
    # consuming module's models; the workflow/app layer sets these + renders an embedded view.
    origin_model = fields.Char("Origin Model", index=True, readonly=True)
    origin_res_id = fields.Integer("Origin Res ID", index=True, readonly=True)
    has_origin = fields.Boolean(
        compute="_compute_origin_display",
        help="True when this activity stored an origin model and id. The row may since be gone.")
    origin_model_label = fields.Char(
        "Related model",
        compute="_compute_origin_display",
        help="Translated ir.model name for origin_model (Service, not riverflow.service).")
    origin_ref = fields.Reference(
        string="Related record",
        selection="_selection_origin_models",
        compute="_compute_origin_display",
        help="Clickable display name of the origin row. Empty when the row is gone.")
    started_at = fields.Datetime(default=fields.Datetime.now, index=True, readonly=True)
    duration_s = fields.Float("Duration (s)", readonly=True)
    # Opaque broker browser session ids this run used (never live-view URLs — R3).
    # Written by the bot via record_run; powers Watch live / Replay from Bot Activity.
    browser_session_ids = fields.Json(
        "Browser Session Ids", readonly=True, copy=False,
        help="Opaque cloud-browser session ids for this activity. Viewer URLs are "
        "minted on click and never stored.")
    browser_status = fields.Char(
        "Browser", compute="_compute_browser_status",
        help="Plain-language status of the cloud browser for this activity.")
    has_browser_session = fields.Boolean(
        compute="_compute_browser_status",
        help="True when this activity linked at least one cloud-browser session.")
    turn_ids = fields.One2many("oteny.bot.turn", "session_id", string="Turns", readonly=True)
    turn_count = fields.Integer(compute="_compute_turn_metrics")
    tool_call_count = fields.Integer(compute="_compute_turn_metrics")

    # Access window for forensic replay (matches the platform 48h promise).
    _BROWSER_REPLAY_HOURS = 48

    @api.depends("turn_ids", "turn_ids.tool_call_count")
    def _compute_turn_metrics(self):
        for s in self:
            s.turn_count = len(s.turn_ids)
            s.tool_call_count = sum(s.turn_ids.mapped("tool_call_count"))

    @api.depends("response")
    def _compute_response_first_line(self):
        for s in self:
            s.response_first_line = s._first_nonempty_line(s.response)

    @api.depends("request")
    def _compute_request_preview(self):
        for s in self:
            s.request_preview = s._first_n_lines(s.request, 3)

    @staticmethod
    def _first_nonempty_line(text):
        if not text:
            return False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return False

    @staticmethod
    def _first_n_lines(text, n):
        if not text:
            return False
        lines = text.splitlines()[:n]
        return "\n".join(lines) if lines else False

    @staticmethod
    def _markdown_to_html(text):
        """Render a bot's markdown text (``response``/``request``) as sanitized HTML.

        A bot writes markdown (``**bold**``, bullet lists, links); a view that reads
        the raw field and shows it as plain text displays the literal ``**`` markers
        instead of formatting. html_sanitize is the safety net for any tag mistune
        passes through untouched from the bot's own markdown (mirrors the platform's
        Discuss adapter, which sanitizes on the message body write)."""
        if not text:
            return False
        return html_sanitize(_MARKDOWN(text))

    @api.model
    def _selection_origin_models(self):
        return [(m.model, m.name) for m in self.env["ir.model"].sudo().search([])]

    @api.depends("origin_model", "origin_res_id")
    def _compute_origin_display(self):
        wanted = {s.origin_model for s in self if s.origin_model}
        labels = {}
        if wanted:
            for rec in self.env["ir.model"].sudo().search([("model", "in", list(wanted))]):
                labels[rec.model] = rec.name
        existing = {}
        by_model = {}
        for s in self:
            if s.origin_model and s.origin_res_id and s.origin_model in self.env:
                by_model.setdefault(s.origin_model, set()).add(s.origin_res_id)
        for model, ids in by_model.items():
            for rec in self.env[model].browse(list(ids)).exists():
                existing[(model, rec.id)] = rec
        for s in self:
            s.has_origin = bool(s.origin_model and s.origin_res_id)
            s.origin_model_label = labels.get(s.origin_model) or s.origin_model or False
            rec = existing.get((s.origin_model, s.origin_res_id))
            s.origin_ref = "%s,%s" % (s.origin_model, rec.id) if rec else False

    def _origin_record(self):
        """The origin row the current user can browse, or an empty recordset."""
        self.ensure_one()
        if not self.origin_model or not self.origin_res_id:
            return self.browse()
        if self.origin_model not in self.env:
            return self.browse()
        return self.env[self.origin_model].browse(self.origin_res_id).exists()

    def action_open_origin(self):
        """Open the origin record's form. Used from the Bot Activity header."""
        self.ensure_one()
        if not self.origin_model or not self.origin_res_id:
            raise UserError(_("This activity is not linked to a record."))
        if self.origin_model not in self.env:
            raise UserError(_(
                "The related model %(model)s is not installed on this database.",
                model=self.origin_model,
            ))
        record = self._origin_record()
        if not record:
            raise UserError(_(
                "The related %(model)s record is no longer available.",
                model=self.origin_model_label or self.origin_model,
            ))
        return {
            "type": "ir.actions.act_window",
            "name": record.display_name,
            "res_model": self.origin_model,
            "res_id": record.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }

    @api.model
    def search_latest_for_origin(self, origin_model, origin_res_id):
        """Latest session for a soft origin, or an empty recordset."""
        if not origin_model or not origin_res_id:
            return self.browse()
        return self.sudo().search(
            [("origin_model", "=", origin_model), ("origin_res_id", "=", origin_res_id)],
            order="started_at desc, id desc",
            limit=1,
        )

    def action_toggle_request(self):
        """Reload this form with the full request shown or hidden."""
        self.ensure_one()
        ctx = dict(self.env.context)
        ctx["oteny_bot_show_full_request"] = not ctx.get("oteny_bot_show_full_request")
        return {
            "type": "ir.actions.act_window",
            "name": _("Bot Activity"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
            "context": ctx,
        }

    def action_open_discuss_channel(self):
        """Open this bot's Discuss home channel (mail.action_discuss)."""
        self.ensure_one()
        return self.bot_id.action_open_discuss_channel()

    @api.depends(
        "browser_session_ids", "outcome", "started_at", "duration_s",
    )
    def _compute_browser_status(self):
        now = fields.Datetime.now()
        Broker = self.env["oteny.broker.client"]
        replay_mintable = Broker._broker_purpose_configured("replay-view")
        for s in self:
            ids = s.browser_session_ids or []
            if not isinstance(ids, list):
                ids = []
            s.has_browser_session = bool(ids)
            if not ids:
                s.browser_status = _("No browser used")
                continue
            if s.outcome == "dispatched":
                s.browser_status = _("Live now")
                continue
            closed = s.started_at
            if closed and s.duration_s:
                closed = closed + timedelta(seconds=int(s.duration_s))
            if closed:
                # hours left in the 48h access window (not a deletion claim).
                age_h = (now - closed).total_seconds() / 3600.0
                left = s._BROWSER_REPLAY_HOURS - age_h
                if left > 0:
                    # Honest: do not claim "Replay available" when only a login-gate
                    # otci_ is wired — that mint 401s. Dedicated replay-view (or
                    # dog-food otmt_) must be present.
                    if replay_mintable:
                        s.browser_status = _(
                            "Replay available · about %(hours)sh left",
                            hours=max(1, int(round(left))),
                        )
                    else:
                        s.browser_status = _(
                            "Recording kept · replay not configured"
                        )
                    continue
            s.browser_status = _("Browser session ended")

    def _can_mint_browser_viewer(self):
        """HR users (and bot managers) may mint a Watch/Replay URL."""
        user = self.env.user
        if user.has_group("oteny_bot.group_oteny_bot_manager"):
            return True
        return user.has_group("hr.group_hr_user")

    def _latest_browser_session_id(self):
        self.ensure_one()
        ids = self.browser_session_ids or []
        if not isinstance(ids, list):
            return None
        for sid in reversed(ids):
            if isinstance(sid, str) and sid:
                return sid
        return None

    def action_watch_live_browser(self):
        """Mint a read-only live view and open it in a new tab (R3 — URL not stored)."""
        self.ensure_one()
        if not self._can_mint_browser_viewer():
            raise UserError(_(
                "Only HR users can open the live browser view."
            ))
        sid = self._latest_browser_session_id()
        if not sid:
            raise UserError(_(
                "This activity did not use a cloud browser, so there is nothing to watch."
            ))
        Broker = self.env["oteny.broker.client"]
        try:
            res = Broker._broker_post(
                f"/v1/browser/session/{sid}/viewer",
                purpose="live-watch",
            )
        except UserError as exc:
            # Friendly wrap — never surface HTTP codes / Steel / session ids.
            msg = str(exc)
            if "404" in msg or "unknown session" in msg.lower():
                raise UserError(_(
                    "The live browser is no longer available. "
                    "If a recording is still within the retention window, try Replay."
                )) from exc
            if (
                "401" in msg
                or "inactive token" in msg.lower()
                or "unknown or inactive" in msg.lower()
                or "not configured" in msg.lower()
            ):
                raise UserError(_(
                    "Live browser watch is not configured on this environment. "
                    "Ask an administrator to wire the live-watch broker token."
                )) from exc
            raise UserError(_(
                "Could not open the live browser view. "
                "Try again in a moment, or ask an administrator if this keeps happening."
            )) from exc
        url = (res or {}).get("session_viewer_url") or ""
        if not url:
            raise UserError(_(
                "The live browser is no longer available."
            ))
        return {
            "type": "ir.actions.act_url",
            "url": url,
            "target": "new",
        }

    def action_replay_browser(self):
        """Mint a 48h forensic replay URL and open it (R3 — URL not stored)."""
        self.ensure_one()
        if not self._can_mint_browser_viewer():
            raise UserError(_(
                "Only HR users can open a browser replay."
            ))
        sid = self._latest_browser_session_id()
        if not sid:
            raise UserError(_(
                "This activity did not use a cloud browser, so there is nothing to replay."
            ))
        Broker = self.env["oteny.broker.client"]
        try:
            res = Broker._broker_post(
                f"/v1/browser/session/{sid}/replay",
                purpose="replay-view",
            )
        except UserError as exc:
            msg = str(exc)
            if "410" in msg or "expired" in msg.lower():
                raise UserError(_(
                    "The browser recording is no longer available "
                    "(the access window has closed)."
                )) from exc
            # Distinct 409 bodies — check the specific error string before a bare "409".
            if "recording_pending" in msg.lower():
                raise UserError(_(
                    "The browser recording is still being finalized. "
                    "Try Replay again in a minute."
                )) from exc
            if "session_busy" in msg.lower():
                raise UserError(_(
                    "Someone is still using this browser session for a login. "
                    "Finish or cancel that login, then try Replay again."
                )) from exc
            if "404" in msg or "unknown session" in msg.lower():
                raise UserError(_(
                    "No browser recording is available for this activity."
                )) from exc
            if "501" in msg or "not implemented" in msg.lower() or "unavailable" in msg.lower():
                raise UserError(_(
                    "Browser replay is not available yet on this environment."
                )) from exc
            if (
                "401" in msg
                or "inactive token" in msg.lower()
                or "unknown or inactive" in msg.lower()
                or "not configured" in msg.lower()
            ):
                raise UserError(_(
                    "Browser replay is not configured on this environment. "
                    "Ask an administrator to wire the replay-view broker token."
                )) from exc
            raise UserError(_(
                "Could not open the browser replay. "
                "Try again in a moment, or ask an administrator if this keeps happening."
            )) from exc
        url = (res or {}).get("session_viewer_url") or (res or {}).get("replay_url") or ""
        if not url:
            raise UserError(_(
                "No browser recording is available for this activity."
            ))
        return {
            "type": "ir.actions.act_url",
            "url": url,
            "target": "new",
        }


class OtenyBotTurn(models.Model):
    _name = "oteny.bot.turn"
    _description = "Oteny Bot Activity Turn (one LLM call)"
    _order = "session_id, sequence, id"
    # Per-LLM-call detail under an already-ignored session -- see OtenyBotSession.
    _oteny_audit_ignore = True

    session_id = fields.Many2one("oteny.bot.session", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    system_prompt = fields.Text(readonly=True)
    user_message = fields.Text(readonly=True)
    llm_response = fields.Text(readonly=True)
    tool_calls = fields.Json(
        "Tool Calls", readonly=True,
        help="This turn's tool calls (name, args, result) — the exchange detail an admin reviews.")
    tool_call_count = fields.Integer(readonly=True)
    llm_model = fields.Char("LLM Model", readonly=True)
    tokens_in = fields.Integer(readonly=True)
    tokens_out = fields.Integer(readonly=True)
    duration_s = fields.Float("Duration (s)", readonly=True)
