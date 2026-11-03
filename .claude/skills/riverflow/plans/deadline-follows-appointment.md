# Plan: a deadline that follows an appointment is read-only and hidden

Status: decisions agreed by Ries on 3-Nov-2026; built on 3-Nov-2026
(riverflow 19.0.1.1391), see "Build record". Modules: riverflow (the flag and
the form), rivercreds (adds "Credential appointment" to the flag). Origin: radar plan
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
3. **No block on write** (Q1, Ries, 3-Nov-2026). The form only hides the
   fields. The server refuses nothing, and the two calendars stay as they
   are: a drag of such a service still switches it to "Self". Hiding the
   fields removes the route that users took; a block on write would need a
   way to tell a transition from a hand edit, for little gain.
4. **One flag.** A computed field on `riverflow.service`,
   `is_deadline_from_appointment`: true for a mode in
   `_appointment_deadline_modes()`, which is "Top-level appointment" in
   riverflow; rivercreds adds "Credential appointment". The form reads it.

## What stays as it is

- A transition still writes the deadline: the rule of the state it enters,
  Change Appointment, a cancel.
- The days before or after the appointment stay a setting of the service
  template. A user can no longer change them on one service outside debug
  mode (one Inform Employee Travel Plan on the radar copy runs on −1 day
  instead of −2).
- A service leaves the scope as soon as a transition gives it another
  Deadline From, for example AT Applied, which sets a follow-up date.

## Build record (3-Nov-2026)

- **riverflow 19.0.1.1391.** `models/riverflow_service.py`: the field
  `is_deadline_from_appointment` and `_appointment_deadline_modes()`
  (`root_appointment`). `views/riverflow_service_views.xml`: Deadline (both
  nodes, `project_deadline` and `deadline`), Priority, Deadline From, the
  days and Weekend hide while the flag is set. Each field except
  `project_deadline` has a twin with `groups="base.group_no_one"` right
  after it, visible only while the flag is set. `project_deadline` needs no
  twin: a flagged service uses a relative mode, so the read-only `deadline`
  shows its date in debug mode. The twin comes after the first node, so the
  crewradar xpath on the first Deadline From (`deadline_log_entry_id` after
  it) keeps its place.
- **rivercreds.** `models/riverflow_service.py` adds `credential_appointment`
  to `_appointment_deadline_modes()`.
- **Found:** Odoo 19 does not remove the node of a group that the user lacks.
  `get_view` keeps it with `invisible="1"` (odoo-development
  `xml-guidelines.md`, "A field only in debug mode"). So the form test counts
  the nodes that can show.
- **Tests:** riverflow `test_deadline_follows_appointment` (5: the flag for
  each mode, a change of mode, the deadline still follows the appointment,
  one node per field that can show without debug mode); rivercreds
  `test_appointment_of_open_service` (+1, "Credential appointment"). A
  focused run with `test_project_deadline`, `test_weekend_deadline_rule` and
  `test_state_deadline_on_entry` on `cr-test`: 0 failed of 73.
- **Browser check** on `crmain` (production copy of 3-Nov-2026, after `-u
  riverflow,rivercreds`). Without debug mode: main service 29191 in
  Appointment Booked shows the Appointment (26-Nov-26 09:15) in the left
  column and an empty right column; its child service 39924 (AB Arrange
  Transport) shows only the Appointment in the right column. In debug mode
  both show Deadline, Priority, Deadline From, the days and Weekend (39924:
  24-Nov-26, "Top-level appointment", −2). A service on another rule (template
  5428, "Credential renewal marker") still shows all fields without debug
  mode. No console errors.

## Not in this plan

- The other rules that HR overrides by typing (the renewal marker in Not
  Started, the follow-up dates in Appointment Requested and AT Applied): radar
  plan `crewradar-cuneus/plans/work-permit-setup-review.md`.
- A reminder before the visit for HR: radar's AB Visit Documents service has
  no deadline (12 of 12 open services on the copy of 3-Nov-2026).
- Main services 29172 and 35244, which keep the old appointment date: the
  migration of radar's Change Appointment (question to Ries, 3-Nov-2026).
