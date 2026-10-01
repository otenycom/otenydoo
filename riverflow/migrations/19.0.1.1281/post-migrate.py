"""Open a review service for a recent bounced email.

The live hook does this when a mail.notification becomes bounce /
mail_bounce. This script catches bounces already stored. It uses the same
create path. It does not send email. A latest send with no bounce is left
alone, and so is a bounce older than 72 hours.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    opened = env["riverflow.service"]._backfill_review_email_bounces()
    _logger.info("review email bounce: opened %s service(s)", opened)
