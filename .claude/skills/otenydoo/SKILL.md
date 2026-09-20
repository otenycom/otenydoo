---
name: otenydoo
description: The repository of Oteny's generic Odoo modules — the layering rule, the release order, and how a business module extends a generic one.
---

# Otenydoo

## The rule

A module here depends on Odoo and on modules here, never on a business's
module, and no code here names a business's model. The customer-template gate
holds the rule on every commit. A comment or a docstring may name a business
as an example or as history.

## The layering

`oteny_shortcut`, `oteny_audit`, `oteny_backup_trigger`, `oteny_bot` and
`oteny_knowledge_sync` sit at the top with no dependency among them.
`riverflow` sits below `oteny_shortcut` and `oteny_bot`: it calls the bridge to
dispatch work to a bot, and it implements the bridge's work contract for its
own records. `odoo_parallel_tests` is tooling beside them.

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
