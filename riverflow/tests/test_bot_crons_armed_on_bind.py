"""Binding a bot on a neutralized copy arms the engine's bot crons.

Neutralize switches off every cron, and the two bot crons are ``noupdate``, so no deploy
switches them on again: on test1 (2026-10-20) the dispatch belt was still off after two
deploys, three days after the restore. A bound bot without its crons looks healthy until
the first lost dispatch. ``oteny.bot.bind_discuss_channel`` is the deliberate act that
connects a bot to a copy, so it arms them there, and never touches a cron on a database
that is not neutralized (production).
"""

from odoo.addons.riverflow.models.riverflow_oteny_bot import BOT_CRON_XMLIDS
from odoo.tests import new_test_user, tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "riverflow", "test_bot_crons_armed_on_bind")
class TestBotCronsArmedOnBind(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.params = cls.env["ir.config_parameter"].sudo()
        cls.crons = cls.env["ir.cron"]
        for xmlid in BOT_CRON_XMLIDS:
            cls.crons |= cls.env.ref(xmlid)
        cls.channel = cls.env["discuss.channel"].create({"name": "Bot Room"})
        # A bot binds itself as its own seam user, which may not read ir.cron.
        cls.bot_user = new_test_user(cls.env, login="cron.bind.bot", groups="base.group_user")

    def _bind(self, ref):
        return self.env["oteny.bot"].with_user(self.bot_user).bind_discuss_channel(
            ref, channel_id=self.channel.id)

    def test_a_bind_arms_the_bot_crons_on_a_neutralized_copy_only(self):
        self.crons.active = False

        # Not neutralized (production): a bind leaves every cron as it is.
        self.params.set_param("database.is_neutralized", False)
        res = self._bind("hh09001")
        self.assertTrue(res["ok"])
        self.assertEqual(res["bot_crons_armed"], [])
        self.assertEqual(self.crons.mapped("active"), [False, False])

        # Neutralized copy: the bind arms both and says so.
        self.params.set_param("database.is_neutralized", "1")
        res = self._bind("hh09002")
        self.assertTrue(res["ok"])
        self.assertEqual(res["bot_crons_armed"], list(BOT_CRON_XMLIDS))
        self.assertEqual(self.crons.mapped("active"), [True, True])

        # A re-bind finds them armed and names nothing.
        res = self._bind("hh09003")
        self.assertEqual(res["bot_crons_armed"], [])
