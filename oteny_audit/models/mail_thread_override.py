from odoo import models, api
from odoo.addons.mail.models.mail_thread import MailThread


# Store original MailThread methods
original_mailthread_create = MailThread.create
original_mailthread_write = MailThread.write


@api.model_create_multi
def patched_mailthread_create(self, vals_list):
    """Override MailThread.create to disable tracking by default."""
    return original_mailthread_create(self.with_context(tracking_disable=True), vals_list)


def patched_mailthread_write(self, vals):
    """Override MailThread.write to disable tracking by default."""
    return original_mailthread_write(self.with_context(tracking_disable=True), vals)


def patched_mailthread_compute_field_value(self, field):
    """Recompute a stored computed or related field without chatter tracking.

    The recompute runs at the flush, in the env of whoever flushes, not in the
    write that disabled tracking, so MailThread._compute_field_value prepared
    the tracking of the recomputed fields (a ship's Operator Name and Operator
    Email reached the chatter). Skip MailThread's step, which only prepares
    that tracking, and run the rest of the chain.
    """
    return super(MailThread, self)._compute_field_value(field)


# Apply patches to MailThread class specifically
MailThread.create = patched_mailthread_create
MailThread.write = patched_mailthread_write
MailThread._compute_field_value = patched_mailthread_compute_field_value
