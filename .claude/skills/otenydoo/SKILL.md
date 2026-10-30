---
name: otenydoo
description: The repository of Oteny's generic Odoo modules — the layering rule, the release order, how a business module extends a generic one, and which checkout to write in. New work is written in ~/oteny/otenydoo. The otenydoo folder inside a business checkout is the deploy pin.
---

# Otenydoo

## The rule

A module here depends on Odoo and on modules here, never on a business's
module, and no code here names a business's model. The customer-template gate
holds the rule on every commit. A comment or a docstring may name a business
as an example or as history.

## Where you write

New otenydoo work is checked out and written in `~/oteny/otenydoo`. It
lands on `main`, or more often on a `feat/` branch that is checked out
in that same clone. The folder `~/oteny/radar/otenydoo` is the submodule
pin that odoo.sh deploys. Do not create branches there, do not edit code
there, and do not treat a dirty submodule as local development. When the
work is ready for odoo.sh, radar updates the pin to a commit that is
already on otenydoo `main`. The local server reads `~/oteny/otenydoo`
through `odoo.addonsPath`.

The bounce feature was written in the submodule by mistake. The upgrade
could not see it, because the server reads `~/oteny/otenydoo`.

## The layering

`oteny_shortcut`, `oteny_audit`, `oteny_backup_trigger`, `oteny_bot` and
`oteny_knowledge_sync` sit at the top with no dependency among them.
`riverflow` sits below `oteny_shortcut` and `oteny_bot`: it calls the bridge to
dispatch work to a bot, and it implements the bridge's work contract for its
own records. `odoo_parallel_tests` is tooling beside them.

`oteny_base` sits under `oteny_oops` and `oteny_cloud_filestore`: it owns
the section "Oteny" under Settings › Technical (`oteny_base.menu_oteny`),
where both put their menu. A menu record belongs to one module, and the two
do not depend on each other (README of `oteny_base`).

`oteny_home` — the home screen at `/odoo/home` for Odoo Community: every app
as an icon with search; the web client opens it when the URL names no action,
and the apps button opens it. The apps button and the name of the open app are
one link, as in Odoo Enterprise: a click on either opens the home screen, a
hover on either turns the grid into a back arrow (web's own `o_menu_toggle`
styles), and the home screen hides the link. The order is the same for every
user: the apps of the system parameter `oteny_home.first_apps` first, in that order, then the
other apps by name; the apps of `oteny_home.hidden_apps` are left out, also from
the search (both hold menu xmlids, comma-separated; `models/ir_http.py` sends
them in the session info). A business module that depends on it sets both in
its own data. A hidden app stays reachable by its address. A value with an entry
that is not the full xmlid of a top-level menu cannot be saved
(`models/ir_config_parameter.py`): such an entry would do nothing, and the app
would stay without a word.

`oteny_color_scheme` — light and dark mode for Odoo Community: a Theme
preference per user (System, Light, Dark) and our own dark colours in
`web.assets_web_dark`; System follows the operating system through the
`color_scheme` cookie. It defines the same column as Enterprise's
`web_enterprise`, `res_users_settings.color_scheme`, with the same values: a
database that installs it before `web_enterprise` is uninstalled keeps every
user's theme. Its web client service has its own key, `oteny_color_scheme`:
`web_enterprise` registers `color_scheme`, both are installed during such a
move, and one key for both stopped the web client ("Cannot add key
"color_scheme" in the "services" registry: it already exists"). A test run on
Community alone cannot show such a clash; a module that is installed beside
Enterprise is tested with the Enterprise addons path too.

## How a business extends a generic module

Through inheritance and hooks, in the business's own repository. When riverflow
needed a business's rows recomputed on a service's state change, riverflow
gained an empty hook (`_on_state_changed`) and the business's module overrode
it. When the email sender needed the business's subject record, riverflow
gained `_render_context_extra` and the business filled it. Never a patch of a
generic module from a business module, and never a business model name here.

## Release order

A change lands on a feature branch here, is tested on a developer's database
with the business's modules installed beside it, and goes to `main`. The
business repository then moves its submodule pointer and upgrades the modules
here before its own. This repository is released before any business build
that depends on the change.

A business stores a pin: one commit. It does not follow a branch name at
build time. Create a same-name feature branch here only when that
business's work changes a module here. Do not invent `dev` or `test1` in
this repository. The only promotion line is `main`.

`increment_version.py` at this root bumps only the generic manifests.
Radar calls it during a paired or infra deploy, then moves the pin.
`python -m riverdeploy merge-branches feat/X main --repo <this checkout>
--push` merges a feature into `main`. The ceiling is `main` only.

In the CrewRadar pipeline, the generic merge happens when radar
`feat/X` lands on radar `dev`, not when radar reaches `main`. Other
businesses can pin `main` while CrewRadar is still on `test1`.

## Where things are

- `talents/<bundle>/`: the Talent a bot reads to work a module; one git ref
  with the module.
- `.claude/skills/<module>/`: the skill our developers and agents read; the
  same tree the Knowledge app shows.
- `.git_hooks/`: the customer-template gate.
- `.github/workflows/`: module tests on the public runner (community-only
  modules) and the Talent lint.
