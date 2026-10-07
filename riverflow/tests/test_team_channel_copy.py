from datetime import date

from odoo import Command
from odoo.tests import tagged
from odoo.tests.common import HttpCase, TransactionCase, new_test_user


@tagged("riverflow", "post_install", "-at_install", "test_team_channels")
class TestTeamChannelCopy(TransactionCase):
    """Colleague message copy: a staff user's chatter message to a staff member of another team
    is also posted in that colleague's home team channel (models/mail_thread.py).

    The motivating case is two offices of one business: Operations asks one Duisburg colleague
    to book a B&B, and with "Handle by Emails" the request reached only that colleague's
    mailbox. Rules: .claude/skills/riverflow/references/team-channels.md
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Channel = cls.env["discuss.channel"]
        Team = cls.env["riverflow.team"]
        cls.ops_team = Team.create({"name": "TC Operations", "discuss_channel_id": Channel.create({"name": "TC Ops"}).id})
        cls.dus_team = Team.create({"name": "TC Duisburg", "discuss_channel_id": Channel.create({"name": "TC Dus"}).id})
        cls.fin_team = Team.create({"name": "TC Finance", "discuss_channel_id": Channel.create({"name": "TC Fin"}).id})
        cls.no_channel_team = Team.create({"name": "TC No Channel"})

        groups = "base.group_user,base.group_partner_manager,riverflow.group_service_writer"
        cls.ops = new_test_user(cls.env, login="tc_ops", name="TC Ops User", groups=groups)
        cls.dus_a = new_test_user(cls.env, login="tc_dus_a", name="TC Dus A", groups=groups)
        cls.dus_b = new_test_user(cls.env, login="tc_dus_b", name="TC Dus B", groups=groups)
        cls.no_home = new_test_user(cls.env, login="tc_nohome", name="TC No Home", groups=groups)
        cls.no_channel_member = new_test_user(cls.env, login="tc_nochan", name="TC No Chan", groups=groups)
        cls.ops.home_team_id = cls.ops_team
        cls.dus_a.home_team_id = cls.dus_team
        cls.dus_b.home_team_id = cls.dus_team
        cls.no_channel_member.home_team_id = cls.no_channel_team
        cls.external = cls.env["res.partner"].create({"name": "TC Taxi Supplier", "email": "taxi@example.com"})

        # One record that uses the review mixin and one plain chatter record: the copy works on
        # every record with a chatter.
        cls.service = cls.env["riverflow.service"].create(
            {
                "name": "TC B&B booking",
                "project_deadline": date(2026, 10, 1),
                "responsible_team_id": cls.ops_team.partner_id.id,
            }
        )
        cls.contact = cls.env["res.partner"].create({"name": "TC Ship Contact"})

    def _post(self, record, user, partners, **kwargs):
        values = {"body": "Can you book the B&B for mr. Tolentino?", "message_type": "comment",
                  "subtype_xmlid": "mail.mt_comment"}
        values.update(kwargs)
        return record.with_user(user).message_post(partner_ids=partners.ids, **values)

    def _channel_messages(self, team):
        return team.discuss_channel_id.message_ids

    def test_copy_between_teams(self):
        """Send message, a Log note @mention and an email-type post (the Email Sender Wizard) from
        Operations to one Duisburg colleague each give one copy in the Duisburg channel, and none
        in the sender's own Operations channel. The same holds on a plain chatter record."""
        dus_before = self._channel_messages(self.dus_team)
        ops_before = self._channel_messages(self.ops_team)

        self._post(self.service, self.ops, self.dus_a.partner_id)
        self._post(self.service, self.ops, self.dus_a.partner_id, subtype_xmlid="mail.mt_note")
        self._post(self.service, self.ops, self.dus_a.partner_id, message_type="email",
                   subject="Hertogin | Tolentino | 13/14 SEP")
        self._post(self.contact, self.ops, self.dus_a.partner_id)

        copies = self._channel_messages(self.dus_team) - dus_before
        self.assertEqual(len(copies), 4, "one copy per message in the recipient's home team channel")
        self.assertEqual(self._channel_messages(self.ops_team), ops_before, "the sender's own team gets no copy")

        self.assertEqual(copies.author_id, self.ops.partner_id, "the copy is posted as the original sender")
        self.assertEqual(set(copies.mapped("message_type")), {"comment"})
        self.assertFalse(copies.partner_ids, "the copy mentions nobody, so nobody gets a second email")
        for copy in copies:
            self.assertIn("Tolentino", copy.body, "the copy carries the original body")
            self.assertIn("TC Dus A", copy.body, "the header names the recipients of this team")

        def links_to(record):
            # the mail sanitizer rewrites the link's attributes with double quotes
            return copies.filtered(
                lambda m: f'data-oe-model="{record._name}"' in m.body and f'data-oe-id="{record.id}"' in m.body
            )

        service_copies = links_to(self.service)
        contact_copies = links_to(self.contact)
        self.assertEqual(len(service_copies), 3, "the header links to the service")
        self.assertEqual(len(contact_copies), 1, "the header links to the plain chatter record")
        self.assertEqual(
            len(copies.filtered(lambda m: "<strong>Hertogin | Tolentino | 13/14 SEP</strong>" in m.body)), 1,
            "an email's subject is shown in the header",
        )

    def test_no_copy_cases(self):
        """No colleague copy when sender and recipient share a team, when the recipient has no
        home team, when the only other recipient is outside, when the recipient's team has no
        channel, and for a message posted inside a Discuss channel (chat, not chatter)."""
        all_channels = (self.ops_team | self.dus_team | self.fin_team).discuss_channel_id
        before = all_channels.message_ids

        self._post(self.service, self.dus_a, self.dus_b.partner_id)  # same team
        self._post(self.service, self.ops, self.no_home.partner_id)  # recipient without a home team
        self._post(self.service, self.ops, self.ops.partner_id | self.external)  # sender + outsider
        self._post(self.service, self.ops, self.no_channel_member.partner_id)  # team without a channel

        chat = self.env["discuss.channel"].create(
            {"name": "TC chat", "channel_member_ids": [Command.create({"partner_id": self.dus_a.partner_id.id})]}
        )
        chat.sudo().message_post(
            body="Chat mention", message_type="comment", author_id=self.ops.partner_id.id,
            partner_ids=self.dus_a.partner_id.ids, subtype_xmlid="mail.mt_comment",
        )

        self.assertEqual(all_channels.message_ids, before, "none of these messages gives a colleague copy")

    def test_team_contact_and_one_copy_per_team(self):
        """Two recipients of one team give one copy. A team contact as recipient gives a copy in
        that team's channel. A sender without a home team is in no team, so it gives a copy."""
        dus_before = self._channel_messages(self.dus_team)
        fin_before = self._channel_messages(self.fin_team)

        self._post(self.service, self.ops, (self.dus_a | self.dus_b).partner_id)
        copies = self._channel_messages(self.dus_team) - dus_before
        self.assertEqual(len(copies), 1, "one copy per team, however many members are recipients")
        self.assertIn("TC Dus A, TC Dus B", copies.body)

        self._post(self.service, self.ops, self.fin_team.partner_id, body="Charge the day of Mr. Francisco")
        fin_copies = self._channel_messages(self.fin_team) - fin_before
        self.assertEqual(len(fin_copies), 1, "a team contact as recipient reaches that team's channel")
        self.assertIn("Charge the day", fin_copies.body)

        dus_before = self._channel_messages(self.dus_team)
        self._post(self.service, self.no_home, self.dus_a.partner_id)
        self.assertEqual(len(self._channel_messages(self.dus_team) - dus_before), 1,
                         "a sender without a home team still reaches the recipient's team")

    def test_external_copy_unchanged(self):
        """A message from an outside sender keeps going to the Responsible team channel (the
        external message copy) and gives no colleague copy."""
        ops_before = self._channel_messages(self.ops_team)
        dus_before = self._channel_messages(self.dus_team)

        self.service._send_dummy_external_message()

        self.assertEqual(len(self._channel_messages(self.ops_team) - ops_before), 1,
                         "the Responsible team still gets the external message copy")
        self.assertEqual(self._channel_messages(self.dus_team), dus_before)

    def test_members(self):
        """The Members list is the other side of the user's home team. Adding a member sets the
        home team; removing one clears it and never deletes the user."""
        self.assertEqual(self.dus_team.member_ids, self.dus_a | self.dus_b)

        self.fin_team.member_ids = [Command.link(self.no_home.id)]
        self.assertEqual(self.no_home.home_team_id, self.fin_team)

        self.fin_team.member_ids = [Command.unlink(self.no_home.id)]
        self.assertFalse(self.no_home.home_team_id)
        self.assertTrue(self.no_home.exists(), "removing a member does not delete the user")

        self.dus_team.member_ids = [Command.set(self.dus_a.ids)]
        self.assertFalse(self.dus_b.home_team_id)
        self.assertTrue(self.dus_b.exists())
        self.assertEqual(self.dus_team.member_ids, self.dus_a)


@tagged("riverflow", "post_install", "-at_install", "test_team_channels")
class TestTeamFormOpens(HttpCase):
    """The team form must open in the browser for both variants of its Members list.

    The form renders the Members list twice with complementary groups: an editable
    many2many-widget list for access-rights administrators and a read-only list for
    everybody else. The server returns a valid view and web_read for both, so only a
    browser run shows whether the web client draws the form. The tour fails on any
    console error.
    """

    def test_team_form_opens_for_admin(self):
        self.start_tour("/odoo/action-riverflow.team_action", "riverflow_team_form_opens", login="admin")

    def test_team_form_opens_for_non_admin(self):
        new_test_user(self.env, login="tc_team_reader", groups="base.group_user,riverflow.group_service_manager")
        self.start_tour("/odoo/action-riverflow.team_action", "riverflow_team_form_opens", login="tc_team_reader")
