"""The save check: a guarded button judges the record before it acts.

A form often has to tell the user something before it saves. An error stops
the save; the user must fix it. A warning lets the save go on, but only when
the user ticks "I confirm — save with these warnings". The check runs when
the user clicks OK (``action_save``), never live on change.

The gate wraps ``action_save`` on the final registry class, so it runs before
every override. When it holds, it returns the client action
``riverflow_save_check_hold``: the dialog stays open, reloads and shows the
findings. A person and a bot take the same path: save, OK, read the
findings, tick the confirmation, save, OK.

The first consumer is the incomplete-children warning of
``riverflow.service.wizard``: a parent that would complete by itself when its
children are done, moved by hand to an end state while children are open.

Plan: radar ``plans/save-check.md``. This file names no client workflow.
"""

from unittest.mock import patch

from odoo.tests import Form, tagged
from odoo.tests.common import TransactionCase

HOLD_TAG = "riverflow_save_check_hold"
SAVE_CHECK_FIELDS = ("save_check_state", "save_check_findings", "save_check_confirmed")


@tagged("post_install", "-at_install", "riverflow", "test_save_check")
class TestSaveCheck(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        State = cls.env["riverflow.state"]
        Workflow = cls.env["riverflow.workflow"]
        Transition = cls.env["riverflow.transition"]
        service_model = cls.env["ir.model"]._get("riverflow.service")
        default_action = cls.env.ref("riverflow.transition_action_default")

        # A parent that waits for its children, as in test_auto_progress.
        cls.parent_workflow = Workflow.create({
            "model_id": service_model.id,
            "name": "Save Check Parent WF",
        })
        cls.state_await = State.create({
            "workflow_id": cls.parent_workflow.id,
            "name": "Await Children",
            "sequence": 10,
            "auto_progress_on_children_done": True,
        })
        cls.state_done = State.create({
            "workflow_id": cls.parent_workflow.id,
            "name": "Done",
            "sequence": 20,
            "is_end_state": True,
        })
        cls.trans_done = Transition.create({
            "name": "Mark done",
            "from_state_id": cls.state_await.id,
            "to_state_id": cls.state_done.id,
            "action_id": default_action.id,
            "sequence": 10,
        })

        cls.child_workflow = Workflow.create({
            "model_id": service_model.id,
            "name": "Save Check Child WF",
        })
        cls.child_open = State.create({
            "workflow_id": cls.child_workflow.id,
            "name": "Open",
            "sequence": 10,
        })
        cls.child_closed = State.create({
            "workflow_id": cls.child_workflow.id,
            "name": "Closed",
            "sequence": 20,
            "is_end_state": True,
        })

        cls.Service = cls.env["riverflow.service"].with_context(bot_no_inline_dispatch=True)

    def _parent_with_open_child(self, name="Parent"):
        parent = self.Service.create({
            "name": name,
            "workflow_id": self.parent_workflow.id,
            "state_id": self.state_await.id,
        })
        self.Service.create({
            "name": f"{name} child",
            "parent_id": parent.id,
            "workflow_id": self.child_workflow.id,
            "state_id": self.child_open.id,
        })
        return parent

    def _open(self, service, transition):
        """Open the wizard as the transition button does, as a test Form."""
        action = service.with_context(transition_id=transition.id).prepare_transition_action()
        Wizard = self.env[action["res_model"]].with_context(**action["context"])
        return action, Form(Wizard, view=action["views"][0][0])

    def _reopen(self, action, wizard):
        """The form after the hold: the web client reloads the same record."""
        Wizard = self.env[action["res_model"]].with_context(**action["context"])
        return Form(Wizard.browse(wizard.id), view=action["views"][0][0])

    def _ok(self, action, wizard):
        """The OK button: the web client calls action_save with the action's
        context on the saved record."""
        return wizard.with_context(**action["context"]).action_save()

    def test_incomplete_children_warning_holds_then_confirm_saves(self):
        parent = self._parent_with_open_child()
        action, form = self._open(parent, self.trans_done)
        wizard = form.save()

        result = self._ok(action, wizard)

        self.assertEqual(result.get("type"), "ir.actions.client")
        self.assertEqual(result.get("tag"), HOLD_TAG)
        params = result["params"]
        self.assertEqual(params["res_model"], "riverflow.service.wizard")
        self.assertEqual(params["res_id"], wizard.id)
        self.assertEqual(params["state"], "warning")
        [finding] = params["findings"]
        self.assertEqual(finding["level"], "warning")
        self.assertIn("Parent child (Open)", finding["message"])
        self.assertIn("or confirm below", finding["message"])
        self.assertEqual(parent.state_id, self.state_await, "A hold moves nothing.")
        self.assertEqual(wizard.save_check_state, "warning")
        self.assertEqual(wizard.save_check_findings, params["findings"])
        self.assertFalse(wizard.save_check_confirmed)

        form = self._reopen(action, wizard)
        form.save_check_confirmed = True
        wizard = form.save()
        result = self._ok(action, wizard)

        self.assertNotEqual(result.get("tag"), HOLD_TAG)
        self.assertEqual(parent.state_id, self.state_done)

    def test_same_warnings_keep_the_confirmation(self):
        parent = self._parent_with_open_child()
        action, form = self._open(parent, self.trans_done)
        wizard = form.save()
        self._ok(action, wizard)

        form = self._reopen(action, wizard)
        form.save_check_confirmed = True
        form.new_note = "<p>Children are handled elsewhere.</p>"
        wizard = form.save()
        self._ok(action, wizard)

        self.assertEqual(parent.state_id, self.state_done)

    def test_different_findings_clear_the_confirmation(self):
        parent = self._parent_with_open_child()
        action, form = self._open(parent, self.trans_done)
        wizard = form.save()
        self._ok(action, wizard)
        form = self._reopen(action, wizard)
        form.save_check_confirmed = True
        wizard = form.save()

        other = [{"level": "warning", "message": "Another warning."}]
        with patch.object(type(wizard), "_save_check", lambda rec: other):
            result = self._ok(action, wizard)

        self.assertEqual(result.get("tag"), HOLD_TAG)
        self.assertEqual(result["params"]["findings"], other)
        self.assertFalse(wizard.save_check_confirmed)
        self.assertEqual(parent.state_id, self.state_await)

    def test_error_holds_even_when_confirmed(self):
        parent = self._parent_with_open_child()
        action, form = self._open(parent, self.trans_done)
        wizard = form.save()
        error = [{"level": "error", "message": "Upload the document first."}]
        with patch.object(type(wizard), "_save_check", lambda rec: error):
            result = self._ok(action, wizard)
            self.assertEqual(result["params"]["state"], "error")
            self.assertEqual(wizard.save_check_state, "error")
            # Even a ticked box (a stale one, or one set by code) does not
            # let an error through.
            wizard.save_check_confirmed = True
            result = self._ok(action, wizard)

        self.assertEqual(result.get("tag"), HOLD_TAG)
        self.assertFalse(wizard.save_check_confirmed)
        self.assertEqual(parent.state_id, self.state_await)

    def test_save_check_runs_once_per_call(self):
        """One OK runs the check once, also when overrides call super(),
        and also on a wizard with its own _name that inherits a guarded
        wizard (its class has the parent's class as a base)."""
        for model in ("riverflow.service.wizard", "riverflow.set.deadline.tomorrow.wizard"):
            with self.subTest(model=model):
                parent = self.Service.create({
                    "name": f"Clean {model}",
                    "workflow_id": self.parent_workflow.id,
                    "state_id": self.state_await.id,
                })
                ctx = {
                    "active_model": "riverflow.service",
                    "active_ids": parent.ids,
                    "transition_id": self.trans_done.id,
                }
                Wizard = self.env[model].with_context(**ctx)
                wizard = Wizard.create(Wizard.default_get(list(Wizard._fields)))
                calls = []
                original = type(wizard)._save_check

                def counting(rec):
                    calls.append(rec.id)
                    return original(rec)

                with patch.object(type(wizard), "_save_check", counting):
                    wizard.action_save()
                self.assertEqual(calls, [wizard.id])
                self.assertEqual(parent.state_id, self.state_done)

    def test_guard_sits_on_the_registry_class(self):
        for model in ("riverflow.service.wizard", "riverflow.supplier.confirmed.wizard"):
            with self.subTest(model=model):
                model_class = type(self.env[model])
                self.assertIs(model_class, self.env.registry[model])
                self.assertTrue(
                    getattr(model_class.__dict__.get("action_save"), "_save_check_guarded", False),
                    "The gate must wrap action_save on the final class, so it "
                    "runs before every override.",
                )

    def test_clean_wizard_writes_no_save_check_field(self):
        parent = self.Service.create({
            "name": "Childless",
            "workflow_id": self.parent_workflow.id,
            "state_id": self.state_await.id,
        })
        action, form = self._open(parent, self.trans_done)
        wizard = form.save()

        result = self._ok(action, wizard)

        self.assertNotEqual(result.get("tag"), HOLD_TAG)
        self.assertEqual(parent.state_id, self.state_done)
        for fname in SAVE_CHECK_FIELDS:
            self.assertFalse(wizard[fname], fname)

    def test_bot_claim_on_held_wizard_answers_not_ok(self):
        parent = self._parent_with_open_child("Claimed")

        result = self.Service.bot_claim(parent.id, self.trans_done.id)

        self.assertFalse(result.get("ok"))
        self.assertIn("Claimed child (Open)", result.get("reason") or "")
        self.assertEqual(parent.state_id, self.state_await)

    def test_bot_follows_the_person_path(self):
        """The person's manual is the bot's manual: OK, read the warning,
        tick the confirmation, OK again (oteny.form.session verbs)."""
        if "oteny.form.session" not in self.env:
            self.skipTest("oteny_bot is not installed")
        Session = self.env["oteny.form.session"]
        parent = self._parent_with_open_child("Bot")
        action = parent.with_context(transition_id=self.trans_done.id).prepare_transition_action()

        photo = Session.open(action=action)
        self.assertNotIn(
            "save_check_confirmed", [f["name"] for f in photo["fields"]],
            "Nothing to confirm yet: the box is hidden.",
        )
        saved = Session.save(photo["handle"])
        ctx = dict((Session.browse(photo["handle"]).view_state or {}).get("context") or {})
        result = self.env[saved["model"]].with_context(**ctx).browse(saved["res_id"]).action_save()

        self.assertEqual(result.get("tag"), HOLD_TAG)
        self.assertIn("Bot child (Open)", result["params"]["findings"][0]["message"])
        self.assertEqual(parent.state_id, self.state_await)

        photo = Session.open(action=action, res_id=result["params"]["res_id"])
        self.assertIn("save_check_confirmed", [f["name"] for f in photo["fields"]])
        Session.set(photo["handle"], {"save_check_confirmed": True})
        saved = Session.save(photo["handle"])
        ctx = dict((Session.browse(photo["handle"]).view_state or {}).get("context") or {})
        self.env[saved["model"]].with_context(**ctx).browse(saved["res_id"]).action_save()

        self.assertEqual(parent.state_id, self.state_done)

    def test_every_transition_wizard_form_shows_the_save_check_box(self):
        """Every form of a transition wizard carries the three lines, so a
        hold always has a box to show and a checkbox to tick. Runs on the
        full install, so it also checks the forms of other modules."""
        missing = []
        for model_name in self.env.registry:
            Model = self.env[model_name]
            if Model._abstract or not {"save_check_confirmed", "transition_id"} <= set(Model._fields):
                continue
            views = self.env["ir.ui.view"].search([
                ("model", "=", model_name),
                ("type", "=", "form"),
                ("mode", "=", "primary"),
                ("active", "=", True),
            ])
            for view in views:
                arch = Model.get_views([(view.id, "form")])["views"]["form"]["arch"]
                for fname in SAVE_CHECK_FIELDS:
                    if f'name="{fname}"' not in arch:
                        missing.append(f"{view.xml_id or view.id} ({model_name}): {fname}")
        self.assertFalse(missing, "Forms without the save check box:\n" + "\n".join(missing))
