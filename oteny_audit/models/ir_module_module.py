import logging

from odoo import models

_logger = logging.getLogger(__name__)


class IrModuleModule(models.Model):
    _inherit = "ir.module.module"

    def _button_immediate_function(self, function):
        """
        Override to run audit log action setup after module installation.
        This method wraps the registry reload, so running our function after
        the super call guarantees the new registry is ready.
        """
        res = super()._button_immediate_function(function)

        try:
            # Check if oteny_audit is installed in the new registry
            audit_module = self.env["ir.module.module"].search(
                [("name", "=", "oteny_audit"), ("state", "=", "installed")]
            )
            if audit_module:
                # After an uninstall, some records of self are deleted (a module
                # without code, such as studio_customization, leaves the table).
                installed_modules = [m.name for m in self.exists() if m.state == "installed"]
                _logger.info(
                    f"New modules installed: {installed_modules}. "
                    "Running audit log setup in the new registry."
                )
                self.env["oteny.audit.log"].install_for_all_models_action()
                _logger.info("Audit log setup completed successfully after runtime install.")
            else:
                _logger.debug(
                    "oteny_audit module not installed in the new registry. " "Skipping audit log setup."
                )
        except Exception:
            _logger.error("Failed to run audit log setup after module installation.", exc_info=True)

        return res
