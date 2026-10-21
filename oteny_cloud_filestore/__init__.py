from . import models
from . import tools

from odoo.tools import SQL

from .models.ir_attachment import wrap_base_file_read


def post_load():
    """Read from the bucket from server start on.

    Odoo runs this when it imports the module: at server start for a
    server-wide module (--load), before any registry exists, and else when a
    registry loads the module. The wrap is done once per process.
    """
    wrap_base_file_read()


def post_init_hook(env):
    """Flag the files that existed before the module was installed.

    Two rules, one UPDATE each: asset bundles by their url, and images of
    records that a module created (a record with an xmlid of a module). Files
    of imported or exported records (xmlid module __import__ or __export__)
    are user data and are not flagged. Every other existing row gets an
    explicit FALSE: Odoo adds the new column with NULL on existing rows, and
    a NULL would read as neither "user file" nor "local only" in SQL.
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
    env.cr.execute(SQL("UPDATE ir_attachment SET cloud_local_only = FALSE WHERE cloud_local_only IS NULL"))
    env["ir.attachment"].invalidate_model(["cloud_local_only"])
