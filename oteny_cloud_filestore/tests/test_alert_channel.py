from odoo.tests import TransactionCase, tagged


@tagged("oteny_cloud_filestore", "post_install", "-at_install")
class TestCloudFilestoreAlertChannel(TransactionCase):
    def test_install_creates_the_alert_channel(self):
        """The install creates the channel, welcomes the reader and points the alerts at it.

        Nobody is added automatically: the admins who act on an outage join it themselves.
        """
        channel = self.env.ref("oteny_cloud_filestore.channel_alerts")
        param = self.env["ir.config_parameter"].sudo().get_param("oteny_cloud_filestore.alert_channel_id")
        self.assertEqual(param, str(channel.id))
        self.assertFalse(channel.group_ids)
        welcome = self.env.ref("oteny_cloud_filestore.channel_alerts_welcome")
        self.assertEqual((welcome.model, welcome.res_id), ("discuss.channel", channel.id))
        self.assertIn("gets the alerts of the cloud filestore", welcome.body)
