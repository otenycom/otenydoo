"""Post-migration 19.0.1.1407 — move the chatter files of a wizard to their record.

A file uploaded in a wizard (a transient model) gets res_model = the wizard
model and res_id = 0. Until this version the email sender wizard posted such a
file to the chatter of the service and left it on the wizard model, and the
ticket wizards did the same before 28-Feb-2026. The file then does not count as
an attachment of the service (message_attachment_count), and a user who is not
an administrator cannot read it with their own rights.

This moves every such file that a chatter message links to the record of that
message. The production backup of 27-Oct-2026 holds 8,382 of them, all on
riverflow.service. A file that an app pinned elsewhere on the service is on a
message of that same service, so this move covers it too.

The transient models come from ir_model, because the registry of a post-migrate
does not yet hold the models of the modules that load after riverflow. Raw SQL
on two columns: no audit rows, no recompute depends on res_model or res_id of a
chatter file. The files that no message links to are left to the garbage
collection of ir.attachment (_gc_wizard_uploads).
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute(
        """
        UPDATE ir_attachment AS upload
           SET res_model = message.model,
               res_id = message.res_id
          FROM message_attachment_rel AS link
          JOIN mail_message AS message ON message.id = link.message_id
         WHERE link.attachment_id = upload.id
           AND upload.res_field IS NULL
           AND upload.res_model IN (SELECT model FROM ir_model WHERE transient)
           AND message.model IS NOT NULL
           AND message.res_id IS NOT NULL
           AND message.res_id != 0
        """
    )
    _logger.info("Moved %d wizard files from the chatter to their record", cr.rowcount)
