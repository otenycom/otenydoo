import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import test_python_expr

# The choices a user gets for "Show in" instead of writing the Show When
# expression by hand (Thijs, 2026-09-15: the expression is too technical for
# most users). Odoo's own words, explained on the form: a view is a list,
# kanban or calendar opened from a menu; a form is the record's own screen,
# where the lists inside it (the tabs) show these records. Each choice is one
# expression; "custom" keeps whatever the filter holds and is the only one
# that needs the expression itself. The keys are fixed.
SHOW_PRESETS = [
    ("everywhere", "Everywhere"),
    ("views", "Views only"),
    ("forms", "Forms only"),
    ("subject", "One form only"),
    ("custom", "Custom rule"),
]

PRESET_EXPRESSIONS = {
    "everywhere": "",
    "views": "not subject",
    "forms": "view == 'form'",
}

# `subject == 'hr.employee'`, with either quote style and any spacing.
SUBJECT_EXPRESSION = re.compile(r"^subject\s*==\s*['\"]([\w.]+)['\"]$")


def expression_for_preset(preset, subject_model=None):
    """The Show When expression a preset stands for."""
    if preset == "subject":
        return f"subject == '{subject_model}'" if subject_model else ""
    return PRESET_EXPRESSIONS.get(preset, "")


def preset_for_expression(expression):
    """(preset, subject model) for a stored expression: the reverse mapping,
    so the form opens on the choice the filter already holds. Anything the
    presets do not cover is "custom".
    """
    expression = (expression or "").strip()
    for preset, preset_expression in PRESET_EXPRESSIONS.items():
        if expression == preset_expression:
            return preset, None
    match = SUBJECT_EXPRESSION.match(expression)
    if match:
        return "subject", match.group(1)
    return "custom", None

# The shortcut fields the client reads. get_filters() carries them on every
# favorite a view loads, so the banner needs no request of its own, and
# get_shortcuts() reads the same list for the in-form row.
SHORTCUT_FIELDS = [
    "shortcut_sequence",
    "shortcut_view_type",
    "shortcut_icon",
    "shortcut_layout",
    "shortcut_show_when",
]


class IrFilters(models.Model):
    _inherit = "ir.filters"

    # Shortcut fields: promote a saved Favorite to a quick-access button
    # in the view shortcuts banner. Set shortcut_sequence > 0 to enable.
    shortcut_sequence = fields.Integer(
        default=0,
        help="When greater than 0, this filter appears as a shortcut button: in "
        "the banner above the model's views, and above every list of the model "
        "inside a form. Show When narrows where. Lower values appear first.",
    )
    # The user-facing settings (Thijs, 2026-09-15): on the filter's own form,
    # a switch, a choice and a picker that read and write the stored
    # shortcut_sequence and shortcut_show_when, so nobody needs the expression
    # or the order number. A new button goes last; an administrator reorders
    # in debug mode.
    is_shortcut = fields.Boolean(
        string="Show as a button",
        compute="_compute_is_shortcut",
        inverse="_inverse_is_shortcut",
        help="On: a button above the views and lists of these records. Off: "
        "the favorite stays in the Favorites menu only.",
    )
    shortcut_show_preset = fields.Selection(
        SHOW_PRESETS,
        string="Show in",
        compute="_compute_shortcut_show_preset",
        inverse="_inverse_shortcut_show_settings",
        help="Everywhere: in the views of these records and inside the forms "
        "that list them. Views only: the list, kanban and calendar views you "
        "open from a menu. Forms only: the lists inside forms, for example the "
        "Services tab of an employee. One form only: pick the form below. "
        "Custom rule: an expression of your own, for administrators.",
    )
    shortcut_subject_model_id = fields.Many2one(
        "ir.model",
        string="Form",
        compute="_compute_shortcut_show_preset",
        inverse="_inverse_shortcut_show_settings",
        help="For One form only: the form whose list shows these records, "
        "for example Employee for the services of an employee.",
    )
    # The forms the picker offers: see _shortcut_subject_models.
    shortcut_subject_model_ids = fields.Many2many(
        "ir.model",
        compute="_compute_shortcut_subject_model_ids",
    )
    # Where a shortcut button shows (Thijs, 2026-09-14). A shortcut shows
    # everywhere by default: the banner above every multi-record view of the
    # model, and the button row above every list of the model inside a form
    # (an x2many field in list mode). This Python expression, evaluated in the
    # browser at each of those places, narrows it: the services filters under
    # an employee differ from those under a log entry, so a filter needs a rule
    # for where it shows. The names it can use: subject (model of the record
    # the list belongs to, False on a top-level view), subject_id, field (the
    # x2many field name), view ('form' inside a form, else the view type),
    # uid and context. Empty = show everywhere. It governs the buttons only;
    # the favorite itself stays in the Favorites menu. A shortcut's Default
    # Filter applies only where the expression is true, so a form-only default
    # does not narrow the model's top-level views at open.
    # Text, not Char: the form shows it in a code box (the ace editor, which
    # takes text and html fields only) with the list of names beside it.
    shortcut_show_when = fields.Text(
        string="Show When",
        help="Python expression that says where the shortcut button shows. Empty: "
        "everywhere (the banner above the model's views and above every list "
        "of the model inside a form). Names: subject (model of the record the "
        "list belongs to, e.g. 'hr.employee'; False on a top-level view), "
        "subject_id, field (the list's field name), view ('form' inside a form, "
        "else 'list', 'kanban', 'calendar', ...), uid, context. Examples: "
        "subject == 'project.task'; subject in ('hr.employee', "
        "'res.partner'); view == 'calendar'; not subject. A shortcut's "
        "Default Filter applies only where the expression is true.",
    )
    shortcut_view_type = fields.Selection(
        selection="_shortcut_view_type_selection",
        string="Shortcut View",
        help="Preferred view type when clicking the shortcut button. "
        "Leave empty to stay on the current view.",
    )
    # Per-record whitelist of the view types that actually exist for this
    # filter's model. The filterable_selection widget reads it (via the
    # whitelist_fname option) to narrow the Shortcut View dropdown to views the
    # model has — the base selection above lists every switchable view type in
    # the system, this scopes it per model.
    shortcut_view_type_whitelist = fields.Json(
        compute="_compute_shortcut_view_type_whitelist",
    )
    shortcut_icon = fields.Char(
        string="Shortcut Icon",
        help="Font Awesome icon class for the shortcut button, e.g. fa-clock-o",
    )
    # Layout state captured from the view, stored as JSON.
    # For list views: {"optional_columns": [...], "column_widths": {...}}
    # For calendar views: {"scale": "week", "show_weekends": true}
    shortcut_layout = fields.Text(
        string="Shortcut Layout",
        help="JSON layout state captured from the view "
        "(column selection, widths, calendar scale, etc.)",
    )

    @api.model
    def _shortcut_view_type_selection(self):
        """All switchable (multi-record) view types registered in the system.

        Derived from ir.ui.view's own ``type`` selection, so custom view types
        (e.g. the credential planning timeline) are included automatically —
        no per-module selection_add on this field is needed. Non-switchable
        types (form / search / qweb) are dropped: a shortcut only ever switches
        between multi-record views.
        """
        view_types = self.env["ir.ui.view"].fields_get(["type"])["type"]["selection"]
        excluded = {"form", "search", "qweb"}
        return [(value, label) for value, label in view_types if value not in excluded]

    @api.depends("model_id")
    def _compute_shortcut_view_type_whitelist(self):
        """View types available for each filter's model: the switchable view
        types that have at least one ir.ui.view defined for the model. Feeds the
        filterable_selection widget so the Shortcut View dropdown cannot offer a
        view the model does not have. (Per-action availability is enforced
        separately, client-side, when the shortcut is clicked.)
        """
        switchable = {value for value, _label in self._shortcut_view_type_selection()}
        View = self.env["ir.ui.view"]
        for flt in self:
            available = []
            if flt.model_id:
                model_types = set(View.search([("model", "=", flt.model_id)]).mapped("type"))
                available = sorted(model_types & switchable)
            flt.shortcut_view_type_whitelist = available

    @api.depends("shortcut_sequence")
    def _compute_is_shortcut(self):
        for record in self:
            record.is_shortcut = record.shortcut_sequence > 0

    def _inverse_is_shortcut(self):
        """Switching the button on gives it the last position; switching it
        off clears the position, so the favorite is no shortcut any more.
        """
        for record in self:
            if record.is_shortcut and record.shortcut_sequence <= 0:
                record.shortcut_sequence = record._next_shortcut_sequence()
            elif not record.is_shortcut and record.shortcut_sequence > 0:
                record.shortcut_sequence = 0

    def _next_shortcut_sequence(self):
        """After the highest position among the model's buttons."""
        last = self.search(
            [("model_id", "=", self.model_id), ("shortcut_sequence", ">", 0)],
            order="shortcut_sequence desc",
            limit=1,
        )
        return (last.shortcut_sequence or 0) + 10

    @api.depends("shortcut_show_when")
    def _compute_shortcut_show_preset(self):
        """The choice and the form the stored expression stands for."""
        for record in self:
            preset, subject_model = preset_for_expression(record.shortcut_show_when)
            record.shortcut_show_preset = preset
            record.shortcut_subject_model_id = (
                self.env["ir.model"]._get(subject_model) if subject_model else False
            )

    def _inverse_shortcut_show_settings(self):
        """The choice writes the expression; Custom rule leaves it as it is,
        for an administrator to edit in debug mode. A choice other than One
        form only also drops the picked form, so the cache and the expression
        agree without waiting for a recompute.
        """
        for record in self:
            preset = record.shortcut_show_preset
            if preset == "custom":
                continue
            if preset != "subject" and record.shortcut_subject_model_id:
                record.shortcut_subject_model_id = False
            record.shortcut_show_when = expression_for_preset(
                preset, record.shortcut_subject_model_id.model
            )

    @api.depends("model_id")
    def _compute_shortcut_subject_model_ids(self):
        for record in self:
            record.shortcut_subject_model_ids = self._shortcut_subject_models(record.model_id)

    @api.model
    def _shortcut_subject_models(self, res_model):
        """The models whose form shows a list of ``res_model``: the relation
        fields that point at it, kept when a form view of their model places
        that field. Without the form check the list names every model that
        merely relates to these records (for services: plan slots, a wizard
        base), which is noise to a user. Among those, the models a user opens
        from a menu are preferred (for services: Employee, Logbook Entry,
        Service, Ship); the others are kept only when no model has a menu.
        """
        if not res_model:
            return self.env["ir.model"]
        View = self.env["ir.ui.view"]
        relation_fields = self.env["ir.model.fields"].search(
            [("relation", "=", res_model), ("ttype", "in", ("one2many", "many2many"))]
        )
        models_with_list = self.env["ir.model"]
        for field in relation_fields:
            model = field.model_id
            if model.transient or model in models_with_list:
                continue
            in_a_form = View.search_count(
                [
                    ("model", "=", model.model),
                    ("type", "=", "form"),
                    ("arch_db", "ilike", f'name="{field.name}"'),
                ]
            )
            if in_a_form:
                models_with_list |= model
        return self._prefer_models_with_a_menu(models_with_list).sorted("name")

    @api.model
    def _prefer_models_with_a_menu(self, models):
        """The models among ``models`` that an ordinary user's menu opens, or
        all of them when none has such a menu. A menu under Settings >
        Technical (developer mode only, e.g. Server Actions, or the raw
        Email/Messages lists) is not a door a user opens, so it must not
        outrank a model with a real, user-facing menu. A leaf item there
        often carries no group of its own; only its Technical folder
        ancestor does. So the whole path to the root is checked, the same
        way the sidebar itself prunes a menu whose folder is hidden.
        """
        if not models:
            return models
        actions = self.env["ir.actions.act_window"].search(
            [("res_model", "in", models.mapped("model"))]
        )
        menus = self.env["ir.ui.menu"].search(
            [("action", "in", [f"ir.actions.act_window,{action.id}" for action in actions])]
        )
        menus = menus.filtered(lambda m: self._menu_is_user_reachable(m))
        models_with_menu = {
            action.res_model for action in actions if any(m.action == action for m in menus)
        }
        preferred = models.filtered(lambda m: m.model in models_with_menu)
        return preferred or models

    @api.model
    def _menu_is_user_reachable(self, menu):
        """A user reaches ``menu`` by clicking through the sidebar only when
        it, and every folder above it, carries no group the user lacks.
        Settings > Technical (``base.group_no_one``) is developer-mode only,
        so a group requirement met only through it never counts.
        """
        allowed = set(self.env.user._get_group_ids())
        allowed.discard(self.env.ref("base.group_no_one").id)
        node = menu
        while node:
            if node.group_ids and not (set(node.group_ids.ids) & allowed):
                return False
            node = node.parent_id
        return True

    @api.constrains("shortcut_show_when")
    def _check_shortcut_show_when(self):
        """Refuse an expression that does not parse, so a typo cannot hide a
        shortcut everywhere. The browser evaluates the expression; a name it
        does not know at runtime hides the button and logs a warning there.
        """
        for record in self:
            expression = (record.shortcut_show_when or "").strip()
            if not expression:
                continue
            message = test_python_expr(expression, mode="eval")
            if message:
                raise ValidationError(
                    _(
                        'The Show When expression of filter "%(name)s" is not valid Python: %(error)s',
                        name=record.name,
                        error=message,
                    )
                )

    @api.model
    def get_filters(self, model, action_id=None, embedded_action_id=None, embedded_parent_res_id=None):
        """The favorites a view loads, with the shortcut fields on each.

        The shortcut banner reads its buttons from these favorites, so a
        view open costs no extra request for shortcuts (Thijs, 2026-09-14).
        The browser decides per favorite whether its button shows here
        (shortcut_show_when) and whether its Default Filter applies here.
        """
        filters = super().get_filters(
            model, action_id, embedded_action_id, embedded_parent_res_id
        )
        if not filters:
            return filters
        by_id = {
            values["id"]: values
            for values in self.browse([f["id"] for f in filters]).read(SHORTCUT_FIELDS)
        }
        for values in filters:
            shortcut_values = by_id[values["id"]]
            values.update({name: shortcut_values[name] for name in SHORTCUT_FIELDS})
        return filters

    @api.model
    def get_shortcuts(self, res_model):
        """Every shortcut of a model the current user may see, for the button
        row above a list of that model inside a form.

        Reuses the built-in ir.filters visibility rules: a filter is visible
        when user_ids is empty (shared with everyone) or contains the
        current user. Inside a form there is no action, so the action a
        filter was saved from does not count: a shortcut applies at model
        level and its Show When expression, evaluated in the browser with
        the form's record as subject, says whether its button shows.
        """
        domain = [
            ("embedded_action_id", "=", False),
            ("embedded_parent_res_id", "in", [0, False]),
            ("model_id", "=", res_model),
            ("shortcut_sequence", ">", 0),
            ("user_ids", "in", [self.env.uid, False]),
        ]
        return self.search(domain, order="shortcut_sequence, name").read(
            ["name", "domain", "is_default"] + SHORTCUT_FIELDS
        )
