"""The skill-sync fields follow the articles from Knowledge to oteny.article.

oteny_article's install hook copied the Knowledge articles with their ids
(the move to Odoo Community); the x_skill_* columns belong to this module, so
it copies them here, after its upgrade has added them to oteny_article. The
next sync then updates the managed articles in place instead of creating a
second tree.
"""

import logging

from odoo.tools.sql import table_exists

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not table_exists(cr, "knowledge_article"):
        return
    cr.execute(
        """
        UPDATE oteny_article o
           SET x_skill_file_path = k.x_skill_file_path,
               x_skill_is_managed = k.x_skill_is_managed,
               x_skill_root = k.x_skill_root
          FROM knowledge_article k
         WHERE k.id = o.id
           AND (k.x_skill_is_managed OR k.x_skill_file_path IS NOT NULL OR k.x_skill_root IS NOT NULL)
        """
    )
    _logger.info("oteny_knowledge_sync: copied the skill fields of %d articles", cr.rowcount)
