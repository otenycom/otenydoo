"""Take over the articles of the Enterprise Knowledge app at install.

A database that comes from Odoo Enterprise holds its prompts, rules and synced
skills in knowledge.article. When oteny_article is installed there (the
phase 1 upgrade of the move to Community, while Knowledge is still
installed), each workspace and shared article is copied into oteny.article
with the same id, and everything that points at an article by model name
follows:

- the xmlids of the modules that ship articles, so their data files load
  onto the copies instead of creating new rows; Knowledge's own gallery
  templates (module "knowledge", is_template) are not copied;
- the attachments (images in article bodies).

A private article is not copied: it is the page of one user that nobody else
sees. On radar these are the "Welcome <user>" pages that Knowledge makes for
every new user and a few empty pages; the uninstall of Knowledge deletes them.

An article has no chatter, so the messages, followers and activities of the
Knowledge articles stay behind; the uninstall of Knowledge deletes them.

Many2one fields to knowledge.article (for example the parsing prompt of a
credential type) keep their values: the ids are the same, and the upgrade of
the module that declares the field moves its foreign key to oteny_article.
The Knowledge app is uninstalled afterwards; its tables go with it.
"""

import logging

from odoo.tools.sql import table_exists

_logger = logging.getLogger(__name__)

# The columns both models share; the Knowledge-only columns (members,
# permissions, covers, templates, properties) have no counterpart.
_COPIED_COLUMNS = [
    "id",
    "name",
    "icon",
    "body",
    "html_field_history",
    "active",
    "sequence",
    "parent_id",
    "is_locked",
    "create_uid",
    "create_date",
    "write_uid",
    "write_date",
]


def post_init_hook(env):
    cr = env.cr
    if not table_exists(cr, "knowledge_article"):
        return
    columns = ", ".join(_COPIED_COLUMNS)
    # Knowledge allows an article without a name (shown as "Untitled");
    # oteny.article requires one.
    selected = ", ".join(
        "COALESCE(NULLIF(name, ''), 'Untitled')" if column == "name" else column
        for column in _COPIED_COLUMNS
    )
    cr.execute(
        f"""
        INSERT INTO oteny_article ({columns})
        SELECT {selected}
          FROM knowledge_article
         WHERE NOT COALESCE(is_template, FALSE)
           AND category != 'private'
        ON CONFLICT (id) DO NOTHING
        """
    )
    copied = cr.rowcount
    cr.execute(
        "SELECT setval('oteny_article_id_seq', GREATEST((SELECT MAX(id) FROM oteny_article), 1))"
    )
    cr.execute(
        """
        UPDATE ir_model_data
           SET model = 'oteny.article'
         WHERE model = 'knowledge.article'
           AND module != 'knowledge'
           AND res_id IN (SELECT id FROM oteny_article)
        """
    )
    xmlids = cr.rowcount
    cr.execute(
        """
        UPDATE ir_attachment
           SET res_model = 'oteny.article'
         WHERE res_model = 'knowledge.article'
           AND res_id IN (SELECT id FROM oteny_article)
        """
    )

    # parent_path and the root article are stored computed values; the SQL
    # copy left them empty.
    articles = env["oteny.article"].with_context(active_test=False).search([])
    articles._parent_store_compute()
    env.add_to_compute(articles._fields["root_article_id"], articles)
    _logger.info(
        "oteny_article: took over %d Knowledge articles and %d xmlids", copied, xmlids
    )
