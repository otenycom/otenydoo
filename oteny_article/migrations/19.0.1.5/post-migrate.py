"""An article has no chatter any more: drop the mail data on articles.

oteny.article no longer inherits mail.thread. The install hook of 19.0.1.4
moved the messages, followers and activities of the Knowledge articles onto
the articles; no screen shows them now, so they go, the same way Odoo deletes
the mail data of a model that is uninstalled (mail's ir.model.unlink). The
attachments stay: the article bodies show them.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    counts = {}
    for table, column in (
        ("mail_activity", "res_model"),
        ("mail_followers", "res_model"),
        ("mail_message", "model"),
    ):
        cr.execute(f"DELETE FROM {table} WHERE {column} = 'oteny.article'")
        counts[table] = cr.rowcount
    _logger.info("oteny_article: deleted the mail data of the articles: %s", counts)
