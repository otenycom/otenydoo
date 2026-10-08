"""One open service per subject on a single-open workflow, and the auto-add's
clean context (radar plan pipeline-applicants-and-legal-work-checks, release R2,
decisions 66, 87 and 89).

- The unique index refuses a second open service on a workflow with
  enforce_single_open, and allows it on any other workflow (decision 66).
- A service the auto-add makes inside the save of a step screen follows its own
  deadline mode: the screen's default_* values do not reach it (decision 87,
  radar service 37725).
- A person who archives or deletes the open task gets it back at once, with a
  message that names the step that stops it; a started task cannot be archived
  or deleted (decision 89).

The subject is a res.partner and the workflows are made here, so the tests hold
for every app that uses the auto-add.
"""

from datetime import timedelta
from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger


@tagged("post_install", "-at_install", "riverflow", "test_auto_add", "test_single_open")
class TestSingleOpenWorkflow(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        State = cls.env["riverflow.state"]
        Transition = cls.env["riverflow.transition"]
        default_action = cls.env.ref("riverflow.transition_action_default")
        service_model = cls.env["ir.model"]._get("riverflow.service").id
        partner_model = cls.env["ir.model"]._get("res.partner").id

        # The renewal-like workflow: one open task per subject.
        cls.workflow = cls.env["riverflow.workflow"].create(
            {"name": "Single open renewal", "model_id": service_model, "enforce_single_open": True}
        )
        cls.not_started = State.create({"name": "Not Started", "workflow_id": cls.workflow.id, "sequence": 10})
        cls.in_progress = State.create({"name": "In Progress", "workflow_id": cls.workflow.id, "sequence": 20})
        cls.done = State.create(
            {"name": "Done", "workflow_id": cls.workflow.id, "sequence": 30, "is_end_state": True}
        )
        cls.not_needed = State.create(
            {
                "name": "Not Needed",
                "workflow_id": cls.workflow.id,
                "sequence": 40,
                "is_end_state": True,
                "is_cancelled_state": True,
            }
        )
        for name, from_state, to_state, sequence in (
            ("Start", cls.not_started, cls.in_progress, 10),
            ("Finish Early", cls.not_started, cls.done, 20),
            ("Skip This One", cls.not_started, cls.not_needed, 30),
            ("Finish", cls.in_progress, cls.done, 10),
        ):
            Transition.create(
                {
                    "name": name,
                    "from_state_id": from_state.id,
                    "to_state_id": to_state.id,
                    "action_id": default_action.id,
                    "sequence": sequence,
                }
            )
        cls.template = cls.env["riverflow.service"].create(
            {
                "name": "Renew Something",
                "is_this_a_template": True,
                "workflow_id": cls.workflow.id,
                "state_id": cls.not_started.id,
                "use_project_deadline_from": "self",
            }
        )
        domain = cls.env["riverflow.named.domain"].create(
            {
                "name": "Single open test subjects",
                "applies_to_model_id": partner_model,
                "domain": "[('name', 'ilike', 'SingleOpenSubject')]",
            }
        )
        cls.rule = cls.env["riverflow.auto.add.service"].create(
            {"domain_id": domain.id, "service_template_id": cls.template.id}
        )

        # A second workflow without the flag, for the control of the index and
        # for the step screen of decision 87.
        cls.other_workflow = cls.env["riverflow.workflow"].create(
            {"name": "Ordinary steps", "model_id": service_model}
        )
        cls.other_open = State.create({"name": "Open", "workflow_id": cls.other_workflow.id, "sequence": 10})
        cls.other_next = State.create({"name": "Next", "workflow_id": cls.other_workflow.id, "sequence": 20})
        cls.other_step = Transition.create(
            {
                "name": "Go On",
                "from_state_id": cls.other_open.id,
                "to_state_id": cls.other_next.id,
                "action_id": default_action.id,
                "sequence": 10,
            }
        )

        cls.subject = cls.env["res.partner"].create({"name": "SingleOpenSubject One"})

    def _open_tasks(self, subject=None, workflow=None):
        subject = subject or self.subject
        return self.env["riverflow.service"].search(
            [
                ("res_model", "=", "res.partner"),
                ("res_id", "=", subject.id),
                ("workflow_id", "=", (workflow or self.workflow).id),
                ("is_open", "=", True),
            ]
        )

    def _new_task(self):
        self.env["riverflow.auto.add.service"].auto_add_services(self.subject)
        task = self._open_tasks()
        self.assertEqual(len(task), 1)
        return task

    def _service(self, workflow, state, **vals):
        return self.env["riverflow.service"].create(
            {
                "name": vals.pop("name", "Hand-made"),
                "workflow_id": workflow.id,
                "state_id": state.id,
                "res_model": "res.partner",
                "res_id": self.subject.id,
                **vals,
            }
        )

    # --- decision 66: the unique index -----------------------------------------

    @mute_logger("odoo.sql_db")
    def test_second_open_service_is_refused_by_the_index(self):
        """A second open service on a single-open workflow is refused by the
        database; a second open service on another workflow is allowed, and a
        closed one never counts."""
        self._new_task()
        with self.assertRaises(IntegrityError), self.env.cr.savepoint():
            self._service(self.workflow, self.not_started)
            self.env.flush_all()

        self._service(self.workflow, self.done, name="Closed one")
        self._service(self.other_workflow, self.other_open, name="Ordinary one")
        self._service(self.other_workflow, self.other_open, name="Ordinary two")
        self.env.flush_all()
        self.assertEqual(len(self._open_tasks()), 1)
        self.assertEqual(len(self._open_tasks(workflow=self.other_workflow)), 2)

    # --- decision 87: the clean context of the auto-add ------------------------

    def test_service_made_inside_a_step_screen_follows_its_own_deadline_mode(self):
        """The step screen of an open service carries that service's values as
        default_* keys. A service the auto-add makes during its save follows its
        own deadline mode: this template sets no date, so the new task has none.
        Before the fix it took the date of the service whose step was saved
        (radar service 37725, 7-Oct-2026)."""
        step_date = fields.Date.today() + timedelta(days=12)
        stepped = self._service(
            self.other_workflow,
            self.other_open,
            name="Upload a document",
            use_project_deadline_from="self",
            project_deadline=step_date,
        )
        action = stepped.with_context(transition_id=self.other_step.id).prepare_transition_action()
        self.assertEqual(action["context"].get("default_project_deadline"), step_date)
        wizard = self.env[action["res_model"]].with_context(**action["context"]).create({})

        # An app runs the auto-add when a step changes its data, inside the save.
        Service = type(self.env["riverflow.service"])
        on_state_changed = Service._on_state_changed

        def run_auto_add(services):
            on_state_changed(services)
            services.env["riverflow.auto.add.service"].auto_add_services(self.subject)

        with patch.object(Service, "_on_state_changed", run_auto_add):
            wizard.with_context(**action["context"]).action_save()

        self.assertEqual(stepped.state_id, self.other_next)
        task = self._open_tasks()
        self.assertEqual(len(task), 1)
        self.assertEqual(task.use_project_deadline_from, "self")
        self.assertFalse(task.project_deadline, "the step's date leaked into the new task")

    def test_start_screen_date_reaches_the_root_but_not_its_children(self):
        """The start screen's date is the date of the service the user starts.
        Its sub-services follow their own deadline mode: a child template without
        a date gets none, not the date of the start screen."""
        start_date = fields.Date.today() + timedelta(days=20)
        child_template = self.env["riverflow.service"].create(
            {
                "name": "Child step",
                "is_this_a_template": True,
                "parent_id": self.template.id,
                "workflow_id": self.other_workflow.id,
                "state_id": self.other_open.id,
                "use_project_deadline_from": "self",
            }
        )
        wizard = (
            self.env["riverflow.start.service"]
            .with_context(
                default_res_model="res.partner",
                default_res_id=self.subject.id,
                default_project_deadline=fields.Date.to_string(start_date),
                default_use_project_deadline_from="self",
            )
            .create({})
        )
        action = wizard.with_context(
            template_service_id=self.template.id,
            default_res_model="res.partner",
            default_res_id=self.subject.id,
            default_project_deadline=fields.Date.to_string(start_date),
            default_use_project_deadline_from="self",
        ).action_apply_template()
        root = self.env["riverflow.service"].browse(action["res_id"])
        self.assertEqual(root.project_deadline, start_date)
        self.assertEqual((root.res_model, root.res_id), ("res.partner", self.subject.id))
        child = root.child_ids
        self.assertEqual(child.template_service_id, child_template)
        self.assertFalse(child.project_deadline, "the start screen's date leaked into a child")

    # --- decision 89: archive and delete heal, with a message ------------------

    def _notifications(self):
        return patch.object(type(self.env["bus.bus"]), "_sendone")

    def _messages(self, mock_send):
        return [
            call.args[2]
            for call in mock_send.call_args_list
            if call.args[1] == "simple_notification"
        ]

    def test_archived_open_task_comes_back_with_a_message(self):
        """A person archives the Not Started task. A new one is open at once, the
        archived one stays archived, and the person reads which step stops it."""
        task = self._new_task()
        with self._notifications() as mock_send:
            task.action_archive()
        self.assertFalse(task.active)
        new_task = self._open_tasks()
        self.assertEqual(len(new_task), 1)
        self.assertNotEqual(new_task, task)
        self.assertEqual(new_task.state_id, self.not_started)
        self.assertFalse(new_task.project_deadline, "the new task follows its own mode")

        messages = self._messages(mock_send)
        self.assertEqual(len(messages), 1)
        message = messages[0]["message"]
        self.assertIn("Renew Something comes back", message)
        self.assertIn(self.subject.display_name, message)
        # The step into the cancelled end state stops it; "Finish Early" records
        # the work as done, so it is not offered.
        self.assertIn("To stop it, use Skip This One.", message)
        self.assertNotIn("Finish Early", message)

    def test_deleted_open_task_comes_back_with_a_message(self):
        task = self._new_task()
        with self._notifications() as mock_send:
            task.unlink()
        self.assertEqual(len(self._open_tasks()), 1)
        self.assertEqual(len(self._messages(mock_send)), 1)

    def test_message_without_a_stop_step_points_to_the_workflow(self):
        self.env["riverflow.transition"].search(
            [("from_state_id", "=", self.not_started.id), ("to_state_id", "=", self.not_needed.id)]
        ).active = False
        task = self._new_task()
        with self._notifications() as mock_send:
            task.action_archive()
        self.assertIn("To stop it, close it through its workflow.", self._messages(mock_send)[0]["message"])

    def test_task_of_a_subject_out_of_scope_does_not_come_back(self):
        """The auto-add decides: when the subject no longer matches the rule, the
        removed task stays gone and no message says otherwise."""
        task = self._new_task()
        self.subject.name = "Someone else"
        with self._notifications() as mock_send:
            task.action_archive()
        self.assertFalse(self._open_tasks())
        self.assertFalse(self._messages(mock_send))

    def test_started_task_cannot_be_archived_or_deleted(self):
        task = self._new_task()
        task.state_id = self.in_progress
        with self.assertRaises(UserError):
            task.action_archive()
        with self.assertRaises(UserError):
            task.unlink()
        self.assertTrue(task.is_open)

    def test_ordinary_workflow_task_does_not_come_back(self):
        """Only a single-open workflow heals: on any other workflow an archive
        stays an archive."""
        service = self._service(self.other_workflow, self.other_open)
        with self._notifications() as mock_send:
            service.action_archive()
        self.assertFalse(self._open_tasks(workflow=self.other_workflow))
        self.assertFalse(self._messages(mock_send))
