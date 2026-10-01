import logging

from odoo import api, models
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY

_logger = logging.getLogger(__name__)


class MailThread(models.AbstractModel):
    """Colleague message copy: a staff user's chatter message to a colleague of another team is
    also posted in that colleague's home team channel.

    Why: staff users address one colleague personally ("can you book the B&B for ..."), and with
    "Handle by Emails" the message only reaches that colleague's mailbox. When the colleague is
    away or overlooks it, the request is lost. The copy makes it visible to the whole team.

    This sits on mail.thread, so it works on every record with a chatter. It is the staff-side
    counterpart of the external message copy in riverflow.mail.thread.review.mixin, which copies a
    message from an OUTSIDE sender into the record's Responsible team channel and skips staff
    authors. The two rules never copy the same message. Business rules:
    .claude/skills/riverflow/references/team-channels.md
    """

    _inherit = "mail.thread"

    def _message_create(self, values_list):
        messages = super()._message_create(values_list)
        # A message inside a Discuss channel is chat, not chatter. Skipping channels also stops the
        # copy, which is itself a channel post, from being copied again.
        if self._name != "discuss.channel":
            self._post_colleague_message_copies(messages)
        return messages

    def _post_colleague_message_copies(self, messages):
        """Post a copy of each staff-written message in the home team channel of every recipient
        of another team. Routing is system machinery, so it reads users and teams as sudo: the
        sender does not need rights on riverflow.team or on other users."""
        for message in messages.sudo():
            teams_recipients = self._colleague_copy_teams(message)
            for team, recipients in teams_recipients.items():
                try:
                    with self.env.cr.savepoint():
                        team._post_colleague_message_copy(message, recipients)
                except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
                    # Odoo's retryable class (serialization failure, deadlock, lock timeout): the
                    # HTTP layer rolls the request back and replays it. A channel post bumps the
                    # discuss_channel row, which is where such collisions happen.
                    raise
                except Exception:  # noqa: BLE001 — the copy must never block the message itself
                    _logger.exception(
                        "colleague message copy of mail.message(%s) to team %s failed",
                        message.id,
                        team.name,
                    )

    def _colleague_copy_teams(self, message):
        """Return {team: recipient partners of that team} for one message; empty when no copy is
        due. The rules, in order:

        - Only the message kinds a person writes: "comment" (Send message, Log note) and "email"
          (the Email Sender Wizard, a staff email reply). Tracking, notifications and bot
          notifications are left out. The external message copy looks at the same two kinds.
        - Only when a staff user wrote it; an outside sender is the external message copy's case.
        - Recipients are the partners the message addresses (the "To" list and every @mention),
          never the sender. A follower that was not addressed does not count.
        - A recipient's team is its home team, or the team itself when the recipient is a team
          contact.
        - Only between teams: the sender's own home team gets no copy. A sender without a home
          team is in no team, so every recipient team gets a copy.
        - A team without a channel, or an archived team, gets no copy.
        """
        if message.message_type not in ("comment", "email") or not message.model or not message.res_id:
            return {}
        if message.model == "discuss.channel" or not message.partner_ids:
            return {}
        sender_users = message.author_id.user_ids.filtered(lambda u: u.active and not u.share)
        if not sender_users:
            return {}

        teams_recipients = {}
        for partner in message.partner_ids - message.author_id:
            staff_users = partner.user_ids.filtered(lambda u: u.active and not u.share)
            for team in partner.team_ids | staff_users.home_team_id:
                teams_recipients.setdefault(team, partner.browse())
                teams_recipients[team] |= partner

        return {
            team: recipients
            for team, recipients in teams_recipients.items()
            if team.active and team.discuss_channel_id and team not in sender_users.home_team_id
        }

    @api.model
    def _routing_handle_bounce(self, email_message, message_dict):
        """After the bounce write, open one review service for that message.

        The write in this method sets notification_status to bounce and
        failure_type to mail_bounce. The temporary exception that
        mail.mail._send writes on the way out is a different write
        (exception / unknown) and does not come through here, so mail that
        then sends does not open a service.
        """
        super()._routing_handle_bounce(email_message, message_dict)
        bounced_message = message_dict.get("bounced_message")
        if bounced_message:
            self.env["riverflow.service"]._add_review_for_bounced_message(bounced_message)
