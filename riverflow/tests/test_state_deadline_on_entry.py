"""The deadline of a task follows the step that brings it into its state (radar
pipeline applicants plan, release R5c, decisions Q151, Q155–Q157).

Before, the deadline belonged to the task alone: a plain step (Back, Restart,
Resend) carried over whatever date the task had, and a task added by hand kept
the fixed date of its start screen for good. On production that left a renewal
task on a date that no longer followed its permit (radar task 32423, Andres).
Now a state carries a deadline setting, the default of every step into it:

- a step into a state with a rule puts the task on that rule, also from a fixed
  date;
- a step with its own deadline key overrides the state;
- a date the user chooses on the step screen overrides the state;
- a task made on a start step takes the setting of its first state;
- a date typed on the task holds until the next step into a state with a
  setting (the manual override stays, Q155);
- a state without a setting keeps the task's date, as before;
- a migration puts the open tasks on a fixed date back on the rule, with a note.

The workflow is made here and the rule is "top-level service" (a source every
riverflow app has), so the tests hold for every app.
"""

from datetime import date, timedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "riverflow", "test_project_deadline", "test_state_deadline_on_entry")
class TestStateDeadlineOnEntry(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        State = cls.env["riverflow.state"]
        Transition = cls.env["riverflow.transition"]
        default_action = cls.env.ref("riverflow.transition_action_default")
        service_model = cls.env["ir.model"]._get("riverflow.service").id
        cls.workflow = cls.env["riverflow.workflow"].create({"name": "Deadline on entry", "model_id": service_model})

        def state(name, sequence, **vals):
            return State.create({"name": name, "workflow_id": cls.workflow.id, "sequence": sequence, **vals})

        # Waiting follows the main task's date minus 2 days, Chasing is a
        # follow-up after 5 days, Free keeps the task's date, Closed is today.
        cls.waiting = state("Waiting", 10, deadline_on_entry="rule", deadline_rule_from="root", deadline_days=-2)
        cls.chasing = state("Chasing", 20, deadline_on_entry="followup", deadline_days=5)
        cls.free = state("Free", 30)
        cls.closed = state("Closed", 40, is_end_state=True, deadline_on_entry="today")

        def step(name, from_state, to_state, context=False):
            return Transition.create(
                {
                    "name": name,
                    "from_state_id": from_state.id if from_state else False,
                    "to_state_id": to_state.id,
                    "action_id": default_action.id,
                    "action_context": context,
                    "workflow_id": cls.workflow.id,
                }
            )

        cls.start = step("Start", False, cls.chasing)
        cls.waiting_to_free = step("Park", cls.waiting, cls.free)
        cls.free_back = step("Back", cls.free, cls.waiting)
        cls.free_to_chasing = step("Chase", cls.free, cls.chasing)
        cls.free_to_chasing_own_key = step("Chase in 9 days", cls.free, cls.chasing, "{'followup_in_days': 9}")
        cls.chasing_to_closed = step("Close", cls.chasing, cls.closed)

        cls.root_deadline = date(2030, 6, 15)
        cls.root = cls.env["riverflow.service"].create({"name": "Main task", "project_deadline": cls.root_deadline})
        cls.today = fields.Date.today()

    def _task(self, state, **vals):
        return self.env["riverflow.service"].create(
            {
                "name": "Sub-task",
                "parent_id": self.root.id,
                "workflow_id": self.workflow.id,
                "state_id": state.id,
                "use_project_deadline_from": "self",
                "project_deadline": date(2029, 1, 1),
                **vals,
            }
        )

    def _run(self, transition, task=None, screen=None):
        """Run a step as the button runs it: the step screen, then OK. `screen`
        holds values the user changes on the screen."""
        records = task if task is not None else self.env["riverflow.service"]
        action = records.with_context(transition_id=transition.id).prepare_transition_action()
        wizard = self.env[action["res_model"]].with_context(**action["context"]).create(screen or {})
        result = wizard.with_context(**action["context"]).action_save()
        return task if task is not None else self.env["riverflow.service"].browse(result["res_id"])

    def test_step_into_a_state_with_a_rule_follows_the_rule(self):
        """A plain Back into Waiting puts a task on a fixed date back on
        Waiting's rule; a step into a state without a setting keeps the date."""
        task = self._task(self.waiting)
        self._run(self.waiting_to_free, task)
        self.assertEqual(task.state_id, self.free)
        self.assertEqual(
            (task.use_project_deadline_from, task.project_deadline),
            ("self", date(2029, 1, 1)),
            "a state without a setting keeps the task's date",
        )

        self._run(self.free_back, task)
        self.assertEqual(task.use_project_deadline_from, "root")
        self.assertEqual(task.deadline, self.root_deadline - timedelta(days=2))

    def test_follow_up_and_today_on_entry(self):
        """A follow-up state gives today plus its days as a fixed date; an end
        state with "today" freezes the day of the step."""
        task = self._task(self.free)
        self._run(self.free_to_chasing, task)
        self.assertEqual(
            (task.use_project_deadline_from, task.project_deadline), ("self", self.today + timedelta(days=5))
        )
        self._run(self.chasing_to_closed, task)
        self.assertEqual(task.project_deadline, self.today)

    def test_a_step_with_its_own_key_overrides_the_state(self):
        """The step's own followup_in_days wins over the state's 5 days."""
        task = self._task(self.free)
        self._run(self.free_to_chasing_own_key, task)
        self.assertEqual(task.project_deadline, self.today + timedelta(days=9))

    def test_a_date_chosen_on_the_screen_wins(self):
        """The screen proposes the state's rule; a fixed date the user chooses
        there stays, also on a state with a rule."""
        task = self._task(self.free)
        chosen = self.today + timedelta(days=40)
        self._run(self.free_back, task, screen={"use_project_deadline_from": "self", "project_deadline": chosen})
        self.assertEqual((task.use_project_deadline_from, task.project_deadline), ("self", chosen))

    def test_the_screen_proposes_the_state_setting(self):
        """The step screen shows the deadline the task will get, not its old one."""
        task = self._task(self.free)
        action = task.with_context(transition_id=self.free_back.id).prepare_transition_action()
        wizard = self.env[action["res_model"]].with_context(**action["context"]).create({})
        self.assertEqual((wizard.use_project_deadline_from, wizard.days_relative_to_project), ("root", -2))

    def test_a_task_made_on_a_start_step_takes_the_first_state_setting(self):
        """A task added by hand on a start step takes its first state's setting
        instead of the start screen's fixed date (radar task 32423 got the
        field default Self). The same code path applies a rule; a follow-up is
        used here because a task without a parent has no top-level date."""
        task = self._run(self.start, screen={"name": "Added by hand"})
        self.assertEqual(task.state_id, self.chasing)
        self.assertEqual(
            (task.use_project_deadline_from, task.project_deadline), ("self", self.today + timedelta(days=5))
        )

    def test_a_typed_date_holds_until_the_next_step(self):
        """The manual override stays: a date typed on the task makes it fixed,
        and the next step into a state with a rule puts it on the rule again."""
        task = self._task(self.waiting, use_project_deadline_from="root", days_relative_to_project=-2)
        task.deadline = self.today + timedelta(days=3)
        self.assertEqual(task.use_project_deadline_from, "self")
        self._run(self.waiting_to_free, task)
        self.assertEqual(task.project_deadline, self.today + timedelta(days=3), "a plain step keeps it")
        self._run(self.free_back, task)
        self.assertEqual(task.use_project_deadline_from, "root")

    def test_fixed_open_tasks_go_back_on_the_rule_with_a_note(self):
        """The migration helper moves only open tasks on a fixed date, and names
        the old date in a note; a closed task and a task on the rule stay."""
        fixed = self._task(self.waiting)
        on_rule = self._task(self.waiting, use_project_deadline_from="root", days_relative_to_project=-2)
        closed = self._task(self.closed)
        closed.state_id = self.closed
        moved = self.waiting._put_fixed_open_tasks_on_rule()
        self.assertEqual(moved, fixed)
        self.assertEqual(fixed.use_project_deadline_from, "root")
        self.assertIn("01-Jan-29", fixed.message_ids[:1].body)
        self.assertEqual(on_rule.use_project_deadline_from, "root")
        self.assertEqual(closed.use_project_deadline_from, "self")
        self.assertFalse(self.free._put_fixed_open_tasks_on_rule(), "a state without a rule moves nothing")

    def test_only_resets_the_named_tasks(self):
        """Radar decision 161: a fixed date can be a date a person typed on
        purpose, so a migration names the tasks whose date came by accident. A
        task outside ``only`` keeps its date; a named task that is no longer on
        Self is left alone."""
        accidental = self._task(self.waiting)
        typed = self._task(self.waiting)
        named_on_rule = self._task(self.waiting, use_project_deadline_from="root", days_relative_to_project=-2)
        moved = self.waiting._put_fixed_open_tasks_on_rule(only=accidental | named_on_rule)
        self.assertEqual(moved, accidental)
        self.assertEqual(accidental.use_project_deadline_from, "root")
        self.assertEqual(typed.use_project_deadline_from, "self", "a task outside only keeps its date")
        self.assertFalse(typed.message_ids.filtered(lambda m: "fixed date" in (m.body or "")))
