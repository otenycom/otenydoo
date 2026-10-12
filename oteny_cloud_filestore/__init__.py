from . import models
from . import tools

from odoo.tools import SQL


def post_init_hook(env):
    """Flag the files that existed before the module was installed.

    Two rules, one UPDATE each: asset bundles by their url, and images of
    records that a module created (a record with an xmlid of a module). Files
    of imported or exported records (xmlid module __import__ or __export__)
    are user data and are not flagged.
    """
    env.cr.execute(SQL("UPDATE ir_attachment SET cloud_local_only = TRUE WHERE url LIKE %s", "/web/assets/%"))
    env.cr.execute(
        SQL(
            """
            UPDATE ir_attachment
               SET cloud_local_only = TRUE
              FROM ir_model_data imd
             WHERE ir_attachment.res_field IS NOT NULL
               AND imd.model = ir_attachment.res_model
               AND imd.res_id = ir_attachment.res_id
               AND imd.module NOT IN ('__import__', '__export__')
            """
        )
    )
    env["ir.attachment"].invalidate_model(["cloud_local_only"])
