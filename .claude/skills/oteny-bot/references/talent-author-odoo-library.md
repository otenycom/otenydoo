# Talent author: Odoo library

A company's bot reaches Odoo through two host Talents. Neither
Talent belongs to one client app.

## Part 1 — standard list and form

Module Talent:
[`oteny_bot/talents/oteny-odoo-access-talent/`](../../../../oteny_bot/talents/oteny-odoo-access-talent/SKILL.md).

Host: `oteny.form.session` in `oteny_bot`.

## Part 2 — riverflow execute

Module Talent:
[`riverflow/talents/riverflow-execute-talent/`](../../../../riverflow/talents/riverflow-execute-talent/SKILL.md).

Host: `riverflow.state.mixin` plus the same form session.

## Delivery

Each Talent arrives by git path. Same git as the addon. A
subdirectory. See [`oteny-bot` skill — Host Talent
delivery](../SKILL.md).

A client Talent is a third path. It composes these skills. It
does not copy them.

`oteny_bot` does not depend on `riverflow`. `riverflow` does not
depend on `oteny_bot`. Glue is: prepare the action, then `open`
the dict. Call **`prepare_transition_action`** on `/json/2/` — the
underscore name is private and returns 403.
