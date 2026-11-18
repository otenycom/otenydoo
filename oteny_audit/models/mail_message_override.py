from odoo import models
from odoo.tools import SQL


class MailMessage(models.Model):
    """Audit-log integration for chatter messages.

    Intent: chatter messages on a business record are cascade-deleted when the
    record is unlinked, so the audit log is the only place that can preserve
    them after that point. We keep audit on `mail.message` and HTML-strip the
    `body` field (≈94% size reduction observed in production) so storage stays
    bounded while the prose remains readable for forensic purposes.
    """

    _inherit = "mail.message"

    # Strip HTML to plain text in the audit log copy of `body` (raw + display).
    # `mail.message` is itself in `OtenyAuditLog._model_parent_keys` mapped to
    # the polymorphic `model,res_id` parent reference, so audit log rows
    # automatically link to the business record's audit trail.
    _oteny_audit_html_strip_fields = {"body"}

    # How Odoo delivered the message, filled in by mail when it posts: nobody types these,
    # and none changes what the message says or to whom it went (plan
    # oteny-audit/plans/audit-storage-reduction.md, rule N3). `message_id` is the e-mail
    # header id. `email_from`, `author_id`, `message_type`, `subtype_id`, `is_internal`,
    # `model`, `res_id`, `parent_id`, `date`, `subject`, `body` and `incoming_email_cc`
    # stay: for an incoming mail `email_from` is the outside sender.
    _oteny_audit_ignore_fields = {
        "reply_to",
        "record_alias_domain_id",
        "record_company_id",
        "email_layout_xmlid",
        "email_add_signature",
        "reply_to_force_new",
        "message_id",
    }

    _OTENY_AUDIT_NAME_TEXT_LENGTH = 80

    def _oteny_audit_display_name(self, record):
        """The name of a message in its audit rows: what was posted, and where.

        Odoo's display name of a message is its subject, and most messages have none: on the
        production backup of 18-Nov-2026, 15,132 of 23,533 message inserts had an empty name,
        and since an insert holds the id only (19.0.1.747) such a row said "Insert Message by
        Fred" and nothing more (Ries, 18-Nov-2026). So the name is the subject, else the start
        of the body as plain text (80 characters), else the subtype ("Note"), and then the
        document the message is on: "'Flight booked KL 808 …' on Sardius | Book Flight ON".
        """
        from .base_patch import _audit_parent_name, _strip_html_for_audit

        text = record.subject or _strip_html_for_audit(str(record.body or "")) or ""
        if len(text) > self._OTENY_AUDIT_NAME_TEXT_LENGTH:
            text = text[: self._OTENY_AUDIT_NAME_TEXT_LENGTH].rstrip() + "…"
        text = f"'{text}'" if text else (record.subtype_id.name or "")
        document = _audit_parent_name(record)
        return f"{text} on {document}".strip() if document else text

    def _oteny_audit_ignore_record(self, record):
        """Skip a message posted into a container that is itself audit-ignored.

        The reason `mail.message` stays audited is the sentence in the class docstring
        above: chatter is cascade-deleted with its business record, so the audit row is
        the only durable copy. A message posted into a `discuss.channel` has no such
        record. The channel is already in `_DEFAULT_IGNORED_MODEL_NAMES`, judged to hold
        near-zero audit value, and a message inside it inherits that judgement — the
        message survives in `mail_message` for as long as the channel does, and dies with
        it. Auditing it is a copy of a log, and on a chatty bot room it is the single
        largest source of audit rows (measured on test1 on 2026-08-25: 15,282 rows /
        57 MB all time, 248 of 259 audited message inserts in one Barney session-day).

        A message with an EMPTY `model` is still audited. Zero such rows exist on test1,
        so there is no measured noise to remove and no reason to widen the rule.
        """
        target = record.model
        if not target:
            return False
        return self.env["oteny.audit.log"]._is_audit_ignored(target)

    def _oteny_audit_purge_record_sql(self):
        """The rows of the messages that _oteny_audit_ignore_record skips, for the purge of
        the stored rows (oteny.audit.log._purge_rows_outside_coverage). Only the insert and
        delete snapshots hold `model`, so the record ids come from them."""
        self.env.cr.execute(SQL(
            """SELECT DISTINCT snapshot->'model'->>'raw' FROM oteny_audit_log
                WHERE model_name = 'mail.message' AND field_name = '__snapshot__'
                  AND snapshot ? 'model'"""
        ))
        Log = self.env["oteny.audit.log"]
        containers = [row[0] for row in self.env.cr.fetchall() if row[0] and Log._is_audit_ignored(row[0])]
        if not containers:
            return None
        return SQL(
            """log.record_id IN (
                   SELECT snap.record_id FROM oteny_audit_log snap
                    WHERE snap.model_name = 'mail.message' AND snap.field_name = '__snapshot__'
                      AND snap.snapshot->'model'->>'raw' IN %s)""",
            tuple(containers),
        )
