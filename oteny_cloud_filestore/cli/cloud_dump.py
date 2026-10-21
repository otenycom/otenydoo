import logging
import optparse
import sys

import odoo
from odoo.exceptions import UserError
from odoo.tools import config

from . import Command

_logger = logging.getLogger(__name__)


class CloudDump(Command):
    """Copy the bucket files that the database wants into a folder (bucket copy and standby fill).

    Exit codes: 0 done; 1 stopped by a bucket or disk error (partial copy); 2 bad arguments,
    or the cloud filestore is not in use (module not installed, mode off); 3 unexpected error;
    4 the cloud filestore is on but has no usable store (read key or bucket settings). Every run that reached the database logs the line "Cloud dump result: ...", so a caller
    can tell a run from an exit of odoo-bin itself (an unknown command also exits 1).
    """

    name = "cloud_dump"

    def run(self, args):
        parser = config.parser
        parser.prog = self.prog
        group = optparse.OptionGroup(
            parser, "Cloud dump", "Copy the files of the cloud filestore of the database `-d` into a folder."
        )
        group.add_option("--into", dest="cloud_dump_into", help="Folder of the copy (required)")
        group.add_option(
            "--checklist",
            dest="cloud_dump_checklist",
            help="Folder with the checklist markers (default: checklist folder of the database filestore)",
        )
        group.add_option(
            "--seed",
            dest="cloud_dump_seed",
            help="Folder that may hold the files already: a file with the right SHA-1 is linked, "
            "not downloaded",
        )
        parser.add_option_group(group)
        options = config.parse_config(args, setup_logging=True)

        dbnames = config["db_name"]
        if not options.cloud_dump_into or len(dbnames) != 1:
            _logger.error("cloud_dump needs --into <folder> and exactly one database (-d)")
            sys.exit(2)
        try:
            with odoo.modules.registry.Registry(dbnames[0]).cursor() as cr:
                env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
                exit_code = self.dump(
                    env, options.cloud_dump_into, options.cloud_dump_checklist, options.cloud_dump_seed
                )
        except Exception:
            # Exit 1 means a partial copy; a crash (no database, no folder) must differ from it.
            _logger.exception("cloud_dump crashed")
            sys.exit(3)
        sys.exit(exit_code)

    @staticmethod
    def dump(env, into, checklist=None, seed=None):
        """Run the copy job in `env`; return the exit code.

        A database without the module, or with the cloud filestore off, has
        nothing to copy: that is exit 2, with a result line that says so, so
        the nightly standby run can skip the step before production uses the
        module, instead of reporting a failed night.

        A database with the cloud filestore on but without a usable store (a
        missing or wrong read key, an incomplete bucket setting) is a fault,
        not "not in use": the standby would silently miss every file that is
        only in the bucket. That is exit 4, so the nightly run fails the step.
        """
        installed = env["ir.module.module"].search_count(
            [("name", "=", "oteny_cloud_filestore"), ("state", "=", "installed")]
        )
        if not installed:
            _logger.info("Cloud dump result: skipped, the module oteny_cloud_filestore is not installed")
            return 2
        attachments = env["ir.attachment"]
        if attachments._cloud_filestore_mode() == "off":
            _logger.info("Cloud dump result: skipped, the cloud filestore is off")
            return 2
        try:
            result = attachments._cloud_filestore_copy_into(into, checklist, seed)
        except UserError as error:
            _logger.error("Cloud dump result: failed, no usable store (check the read key and bucket): %s", error)
            return 4
        _logger.info("Cloud dump result: %s", result)
        return 1 if result["stopped"] else 0
