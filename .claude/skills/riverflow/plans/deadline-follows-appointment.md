# Plan: a deadline that follows an appointment is read-only and hidden

Status: decisions agreed by Ries on 3-Nov-2026; not built. Modules: riverflow
(the flag, the form, the two calendars, the write check, the hint),
rivercreds (adds "Credential appointment" to the flag). Origin: radar plan
`crewradar-cuneus/plans/work-permit-change-appointment.md` (decision 11, the
protected appointment credential) and the analysis of typed deadlines on the
radar production copy of 3-Nov-2026.

## Why

A service can take its deadline from an appointment. riverflow has the mode
"Top-level appointment" (`root_appointment`): a child service is due a number
of days before or after the appointment of its top-level service. rivercreds
adds "Credential appointment" (`credential_appointment`): the service is due
on the appointment of its own appointment credential. When the appointment
moves, these deadlines move with it.

Since rivercreds 19.0.3.378 the appointment itself is protected: while its
service is open, only a transition of the service changes the appointment
credential. The deadline that follows the appointment is not protected. Three
routes change it by hand, and each one switches the service to "Self", so the
deadline stops following the appointment:

- the service form: the user switches Deadline From to "Self" and types a
  date;
- a drag in the services calendar (`riverflow.service`,
  `date_start="deadline"`);
- a drag of a service row in the Radar calendar (`riverflow.state.record`).

A drag writes `deadline`, and `_inverse_deadline` switches the service to
"Self".

What the radar production copy of 3-Nov-2026 shows (Cuneus work permits,
audit log since 7-May-2026):

- HR typed a deadline 13 times on main services in Appointment Booked.
  Service 27128 was parked until 19-Jan-2027 after its appointment had taken
  place. Service 38086 got a reminder three days before the visit.
- Services 29172 and 35244 kept the old appointment date as their deadline
  after the appointment moved on 2-Nov-2026. Their booking had copied the
  appointment date as a fixed date ("Self"). Every typed date gives the same
  fault: after Change Appointment, a service on "Self" stays on the old day.

The form also shows the same date twice. On main service 29191 (Appointment
Booked, 3-Nov-2026) the left column shows Appointment 26-Nov-26 09:15. The
right column shows Deadline 26-Nov-26, Priority 1, Deadline From "Credential
appointment", Day 0 and Weekend "Bring to Friday". Users mix up the
appointment and the deadline.

## Decisions (Ries, 3-Nov-2026)

1. **The services.** A service whose Deadline From is "Top-level appointment"
   or "Credential appointment": *a service whose deadline follows an
   appointment*. This covers the main service and the child services of every
   team. In radar: HR's Arrange Work Permit at AB in Appointment Booked, and
   Operations NL's AB Appointment, AB Arrange Transport, Inform Employee Travel
   Plan, AB Appointment Inform Client and Review AB Appointment.
2. **The form shows the appointment and hides the deadline.** The Appointment
   (date and time) stays visible: on a main service where the module above
   shows it today, on a child service in the right column (riverflow). The
   form hides Deadline, Priority, Deadline From, the days before or after the
   appointment, and Weekend. Debug mode (`base.group_no_one`) shows them. The
   due date stays in the Radar and in the lists.
3. **Nobody changes the deadline by hand.** The two calendars do not let a
   user drag such a service. The server refuses a change of `deadline`,
   `project_deadline`, `use_project_deadline_from`,
   `days_relative_to_project` or `weekend_deadline_rule` outside a
   transition. The message ends with `_appointment_change_hint()`, the
   sentence that names the step that changes the appointment (radar: Change
   Appointment).
4. **One flag.** A computed field on `riverflow.service`,
   `is_deadline_from_appointment`: true for "Top-level appointment" in
   riverflow, and rivercreds adds "Credential appointment". The form, the two
   calendars and the check read it. `_appointment_change_hint()` moves from
   rivercreds down to riverflow, because the check of a child service runs in
   riverflow. rivercreds and the modules above override it as today.

## What stays as it is

- A transition still writes the deadline: the rule of the state it enters,
  Change Appointment, a cancel.
- The days before or after the appointment stay a setting of the service
  template. A user can no longer change them on one service outside debug
  mode (one Inform Employee Travel Plan on the radar copy runs on −1 day
  instead of −2).
- A service leaves the scope as soon as a transition gives it another
  Deadline From, for example AT Applied, which sets a follow-up date.

## Open question for the build

- [ ] **Q1 — How does the check tell a transition from a hand edit?**

  **Situation.** The transition wizard runs with `transition_id` in its
  context (`wizards/riverflow_transition_wizard.py`). Other code can write the
  deadline fields too: a transition run in code
  (`_execute_transition_in_code`), the cancel cascade, crons and migrations.
  Whether each of them has `transition_id` in its context is not checked yet.

  **Question.** Which writes pass the check?

  **Recommended.** A write passes when its context has `transition_id`, or
  when it runs as superuser (crons, migrations). A transition run in code and
  the cascades get `transition_id` where they lack it. Then only the form, the
  lists and the calendars meet the refusal, and those are the routes of a
  hand edit.

## Build steps

1. riverflow: the flag, the form and the hint. The fields that hide for users
   show a second time with `groups="base.group_no_one"`. Check: in the browser
   on a main service and a child service whose deadline follows an
   appointment, as user and in debug mode.
2. riverflow: no drag in the two calendars for a flagged service. Check: in
   the browser.
3. riverflow: the write check (Q1). Check by tests: a hand write of each of
   the five fields is refused on a flagged service and passes on a service
   that is not flagged; a transition passes.
4. rivercreds: "Credential appointment" joins the flag; its own copy of the
   hint goes. Check: the tests of step 3 on a service with an appointment
   credential.
5. radar: the Change Appointment tests still pass.

## Not in this plan

- The other rules that HR overrides by typing (the renewal marker in Not
  Started, the follow-up dates in Appointment Requested and AT Applied): radar
  plan `crewradar-cuneus/plans/work-permit-setup-review.md`.
- A reminder before the visit for HR: radar's AB Visit Documents service has
  no deadline (12 of 12 open services on the copy of 3-Nov-2026).
- Main services 29172 and 35244, which keep the old appointment date: the
  migration of radar's Change Appointment (question to Ries, 3-Nov-2026).
