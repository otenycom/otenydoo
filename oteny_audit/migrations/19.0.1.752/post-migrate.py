"""Post-migration 19.0.1.752 — hard delete every chatter tracking message (Ries, 19-Nov-2026).

oteny_audit keeps chatter tracking off: the audit log holds every field change. The chatters
still held the tracking messages written before the module came, and those of stored computed
and related fields, which the patch let through until 19-Nov-2026 (a ship's Operator Name and
Operator Email). A message that carries a tracking value is a notification that tracking made:
delete it; its tracking values, notifications and links go with it (ON DELETE CASCADE). SQL,
not unlink: the ORM unlink of a mail.message writes one audit tombstone per message.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute(
        """
        DELETE FROM mail_message m
         WHERE m.message_type = 'notification'
           AND EXISTS (SELECT 1 FROM mail_tracking_value t WHERE t.mail_message_id = m.id)
        """
    )
    _logger.info("oteny_audit: deleted %s chatter tracking messages", cr.rowcount)
