import logging
import os

from lxml import etree

from odoo import models, fields, api
from odoo.exceptions import UserError
from odoo.modules.module import get_module_path, get_manifest
from odoo.tools.convert import xml_import
from markupsafe import escape

_logger = logging.getLogger(__name__)


class RiverflowWorkflow(models.Model):
    _name = "riverflow.workflow"
    _description = "Workflow"
    _order = "model,name"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "display_name"

    name = fields.Char("Workflow name", index="trigram", required=True)
    description = fields.Text("Description", required=False)
    icon = fields.Char("Workflow icon", help="Font awesome icon e.g. fa-tasks")
    icon_name_html = fields.Html(
        "Name",
        compute="_compute_icon_name_html",
        help="Combination of Icon and name",
        store=True,
    )
    active = fields.Boolean("Active", default=True)

    state_ids = fields.One2many("riverflow.state", "workflow_id", string="Workflow states")

    auto_complete_state_id = fields.Many2one(
        "riverflow.state",
        string="Auto-Complete State",
        domain="[('workflow_id', '=', id), ('is_end_state', '=', True)]",
        help="End state for services auto-completed when a credential plan item "
        "becomes 'issued'. Set to 'Not Needed' or similar end state.",
    )

    enforce_single_open = fields.Boolean(
        "Enforce Single Open Service",
        default=False,
        help="One open service per subject. The automatic rules create at most one "
        "open (active, not closed) service per record on this workflow, the "
        "database refuses a second one, and a person who archives or deletes the "
        "open service that has not started gets it back at once, with a message "
        "that names the step to close it. Use it where a record must always have "
        "exactly one open task, such as a document renewal.",
    )

    workflow_start_transition_ids = fields.One2many(
        "riverflow.transition",
        "workflow_id",
        domain=[("from_state_id", "=", False)],
        string="Start transitions",
    )

    model_id = fields.Many2one("ir.model", "Applies to", required=True, ondelete="cascade")
    model = fields.Char("Related Model", related="model_id.model", index=True, store=True, readonly=True)
    friendly_model_name = fields.Char(
        "Friendly Model Name", compute="_compute_friendly_model_name", store=True
    )

    display_name = fields.Char("Display Name", compute="_compute_display_name", store=True, index=True)

    is_supply_order = fields.Boolean(
        "Is Supply Order",
        help="If checked, this workflow is for a supply order, e.g a Taxi Order",
        required=False,
        tracking=True,
    )
    has_supply_quantity = fields.Boolean(
        "Has Supply Quantity",
        help="If checked, this workflow has a supply quantity field",
        tracking=True,
        default=False,
    )
    has_supply_unit_price = fields.Boolean(
        "Has Cost Price",
        help="If checked, this workflow has a cost price field",
        tracking=True,
        default=False,
    )
    has_supplier = fields.Boolean(
        "Has Supplier",
        help="If checked, this workflow has a supplier field. If not, it is a self arranged service such as a flight booking.",
        tracking=True,
        default=False,
    )
    has_legs = fields.Boolean(
        "Has Legs",
        help="If checked, this workflow has route-legs for ground transport services.",
        tracking=True,
        default=False,
    )
    has_supply_time = fields.Boolean(
        "Has Time of Day",
        help="If checked, services in this workflow show a time-of-day field "
        "for recording appointment times, pickup times, or departure times.",
        tracking=True,
        default=False,
    )

    # Upgrade to Odoo 19 constraint style
    _name_uniq = models.Constraint(
        "unique (name)",
        "Workflow name already exists!",
    )

    @api.depends("icon", "name")
    def _compute_icon_name_html(self):
        for record in self:
            if record.icon:
                record.icon_name_html = (
                    f'<span><span class="fa {escape(record.icon)}"></span>&nbsp;{escape(record.name)}</span>'
                )
            else:
                record.icon_name_html = escape(record.name)

    @api.depends("model_id")
    def _compute_friendly_model_name(self):
        for record in self:
            record.friendly_model_name = record.model_id.name

    @api.depends("name", "friendly_model_name")
    def _compute_display_name(self):
        for record in self:
            record.display_name = f"{record.name} ({record.friendly_model_name})"

    def workflow_add_start_transition(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Add Start Transition to: " + self.name,
            "view_mode": "form",
            "res_model": "riverflow.transition",
            "context": {"default_workflow_id": self.id, "is_start_transition": True},
            "target": "current",
        }

    def workflow_add_state(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Add Workflow State to: " + self.name,
            "view_mode": "form",
            "res_model": "riverflow.state",
            "context": {"default_workflow_id": self.id},
            "target": "current",
        }

    def reset_workflow_to_xml(self):
        """Reset this workflow's states and transitions to match XML data files.

        Finds all XML data files that define or patch this workflow's records,
        re-imports them to restore field values, re-activates XML-defined records
        that were manually archived, and archives manually-added records that
        have no XML ID.

        Cross-module patches (e.g. crewradar patching a riverflow workflow) are
        detected by scanning modules that depend on the owning module(s).
        """
        self.ensure_one()

        WORKFLOW_MODELS = {"riverflow.workflow", "riverflow.state", "riverflow.transition"}
        IMD = self.env["ir.model.data"].sudo()

        # Collect all records belonging to this workflow (including archived)
        states = self.env["riverflow.state"].with_context(active_test=False).search(
            [("workflow_id", "=", self.id)]
        )
        transitions = self.env["riverflow.transition"].with_context(active_test=False).search(
            [("workflow_id", "=", self.id)]
        )

        # Find ir.model.data entries for the workflow, its states and transitions
        workflow_imd = IMD.search([("model", "=", "riverflow.workflow"), ("res_id", "=", self.id)])
        state_imd = IMD.search([("model", "=", "riverflow.state"), ("res_id", "in", states.ids)])
        transition_imd = IMD.search(
            [("model", "=", "riverflow.transition"), ("res_id", "in", transitions.ids)]
        )

        all_imd = workflow_imd | state_imd | transition_imd
        if not workflow_imd:
            raise UserError("This workflow has no XML ID. Only XML-defined workflows can be reset.")

        # Build lookup set of XML IDs (both short and fully-qualified forms)
        # so we can match records referenced within the same module or cross-module
        xml_id_set = set()
        for rec in all_imd:
            xml_id_set.add(rec.name)
            xml_id_set.add(f"{rec.module}.{rec.name}")

        # Determine which modules to scan: the owning modules plus any installed
        # module that directly depends on them (cross-module patches)
        owning_modules = set(all_imd.mapped("module"))
        dependent_modules = (
            self.env["ir.module.module"]
            .sudo()
            .search(
                [
                    ("state", "=", "installed"),
                    ("dependencies_id.name", "in", list(owning_modules)),
                ]
            )
            .mapped("name")
        )
        modules_to_check = owning_modules | set(dependent_modules)

        # Scan each module's data files for records matching this workflow
        files_to_import = []
        for module_name in sorted(modules_to_check):
            module_path = get_module_path(module_name, display_warning=False)
            if not module_path:
                continue

            manifest = get_manifest(module_name)
            for data_file in manifest.get("data", []):
                if not data_file.endswith(".xml"):
                    continue

                file_path = os.path.join(module_path, data_file)
                if not os.path.isfile(file_path):
                    continue

                try:
                    tree = etree.parse(file_path)
                except Exception:
                    continue

                matching_records = []
                for record_el in tree.iter("record"):
                    rec_id = record_el.get("id", "")
                    rec_model = record_el.get("model", "")
                    if rec_model in WORKFLOW_MODELS and rec_id in xml_id_set:
                        matching_records.append(record_el)

                if matching_records:
                    files_to_import.append((module_name, file_path, matching_records))

        if not files_to_import:
            raise UserError("No XML data files found for this workflow.")

        # Track which records the XML explicitly sets active=False on,
        # so we don't re-activate them after import
        explicitly_inactive_ids = set()
        for module_name, _file_path, record_elements in files_to_import:
            for rec_el in record_elements:
                rec_id = rec_el.get("id", "")
                for field_el in rec_el.iterchildren("field"):
                    if field_el.get("name") == "active":
                        val = (field_el.text or "").strip().lower()
                        if val in ("false", "0"):
                            qualified = rec_id if "." in rec_id else f"{module_name}.{rec_id}"
                            explicitly_inactive_ids.add(qualified)

        # Re-import: use xml_import with mode='init' and noupdate=False to
        # bypass noupdate guards and force-update the records from XML.
        # We call _tag_record directly on selected record elements only,
        # avoiding side effects on other records in the same data file.
        for module_name, file_path, record_elements in files_to_import:
            idref = {}
            importer = xml_import(
                self.env,
                module_name,
                idref,
                mode="init",
                noupdate=False,
                xml_filename=file_path,
            )
            # _tag_record reads from the _sequences stack (normally pushed by
            # _tag_root); push None to disable auto_sequence behaviour
            importer._sequences.append(None)

            for record_el in record_elements:
                try:
                    importer._tag_record(record_el)
                except Exception:
                    _logger.warning(
                        "Failed to import record %s from %s",
                        record_el.get("id"),
                        file_path,
                        exc_info=True,
                    )

        # Collect XML ids that ARE present in the scanned XML files. Records
        # whose ir.model.data entry points at a workflow record but whose
        # XML id is NOT in this set were removed from XML — they're
        # orphan XML records. With noupdate="1" data files Odoo's standard
        # orphan cleanup leaves them alone, so we archive them here.
        present_xml_ids = set()
        for module_name, _file_path, record_elements in files_to_import:
            for rec_el in record_elements:
                rec_id = rec_el.get("id", "")
                present_xml_ids.add(rec_id)
                if "." in rec_id:
                    present_xml_ids.add(rec_id.split(".", 1)[1])
                else:
                    present_xml_ids.add(f"{module_name}.{rec_id}")

        orphan_state_imd = state_imd.filtered(
            lambda imd: imd.name not in present_xml_ids
            and f"{imd.module}.{imd.name}" not in present_xml_ids
        )
        orphan_transition_imd = transition_imd.filtered(
            lambda imd: imd.name not in present_xml_ids
            and f"{imd.module}.{imd.name}" not in present_xml_ids
        )

        # Re-activate XML-defined states/transitions that were manually archived,
        # unless the XML explicitly sets active=False — and skip orphans (they're
        # about to be archived below).
        xml_state_ids = set(state_imd.mapped("res_id"))
        xml_transition_ids = set(transition_imd.mapped("res_id"))
        orphan_state_record_ids = set(orphan_state_imd.mapped("res_id"))
        orphan_transition_record_ids = set(orphan_transition_imd.mapped("res_id"))

        for imd_rec in state_imd | transition_imd:
            qualified_id = f"{imd_rec.module}.{imd_rec.name}"
            if qualified_id in explicitly_inactive_ids:
                continue
            if imd_rec.res_id in orphan_state_record_ids or imd_rec.res_id in orphan_transition_record_ids:
                continue
            record = self.env[imd_rec.model].with_context(active_test=False).browse(imd_rec.res_id)
            if record.exists() and not record.active:
                record.active = True

        # Archive orphan XML-defined records (XML record removed from data files)
        orphan_states = (
            self.env["riverflow.state"]
            .with_context(active_test=False)
            .browse(list(orphan_state_record_ids))
            .filtered("active")
        )
        if orphan_states:
            orphan_states.active = False
            _logger.info(
                "Archived %d XML-orphan states for workflow %s: %s",
                len(orphan_states),
                self.name,
                ", ".join(orphan_states.mapped("name")),
            )

        orphan_transitions = (
            self.env["riverflow.transition"]
            .with_context(active_test=False)
            .browse(list(orphan_transition_record_ids))
            .filtered("active")
        )
        if orphan_transitions:
            orphan_transitions.active = False
            _logger.info(
                "Archived %d XML-orphan transitions for workflow %s: %s",
                len(orphan_transitions),
                self.name,
                ", ".join(orphan_transitions.mapped("name")),
            )

        # Archive manually-added records (those without an XML ID)
        manual_states = states.filtered(lambda s: s.id not in xml_state_ids)
        if manual_states:
            manual_states.active = False
            _logger.info(
                "Archived %d manually-added states for workflow %s: %s",
                len(manual_states),
                self.name,
                ", ".join(manual_states.mapped("name")),
            )

        manual_transitions = transitions.filtered(lambda t: t.id not in xml_transition_ids)
        if manual_transitions:
            manual_transitions.active = False
            _logger.info(
                "Archived %d manually-added transitions for workflow %s: %s",
                len(manual_transitions),
                self.name,
                ", ".join(manual_transitions.mapped("name")),
            )

        _logger.info(
            "Reset workflow '%s' from XML: %d files processed, "
            "%d states (%d XML / %d orphan archived / %d manual archived), "
            "%d transitions (%d XML / %d orphan archived / %d manual archived)",
            self.name,
            len(files_to_import),
            len(states),
            len(xml_state_ids),
            len(orphan_states),
            len(manual_states),
            len(transitions),
            len(xml_transition_ids),
            len(orphan_transitions),
            len(manual_transitions),
        )
