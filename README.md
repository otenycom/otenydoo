# Otenydoo

Oteny's generic Odoo 19 modules, layered, and the Talents a bot reads to work
them. Every business Odoo that Oteny serves depends on these modules. None of
them depends on a business, and no code here names a business's model. That
one rule is what keeps this repository generic, and the customer-template gate
in `.git_hooks/` holds it on every commit.

## The modules

| Module | Depends on | Job |
|---|---|---|
| `oteny_shortcut` | `web` | Shortcuts and filters on any model's views. |
| `oteny_audit` | `base`, `mail` | The audit trail: who changed which field when, on any model. |
| `oteny_backup_trigger` | `base` | Trigger an Odoo.sh backup from a button or a cron. |
| `oteny_bot` | `base`, `mail`, `web` | The bridge between an Oteny business bot and this Odoo: the Discuss channel, the activity log, the form session, and the work contract the platform calls. |
| `oteny_knowledge_sync` | `base`, `mail`, `knowledge` | Publish every configured repository's `.claude/skills` as locked Knowledge articles, one tree per root. A markdown skill path is resolved across every configured root, so a CrewRadar article can name an Otenydoo skill. |
| `riverflow` | `base`, `mail`, `documents`, `oteny_shortcut` | The workflow engine: services, states, transitions, and the bot dispatch that hands work to a bot. |
| `odoo_parallel_tests` | `base` | The parallel test runner: clones the test database and runs batches in worker processes. |

The layering is the dependency direction. `oteny_shortcut`, `oteny_audit`,
`oteny_backup_trigger`, `oteny_bot` and `oteny_knowledge_sync` sit at the top
with no dependency among them. `riverflow` sits below `oteny_shortcut` and
`oteny_bot`. `odoo_parallel_tests` is tooling beside them. A business module
extends a generic one through inheritance and the hooks the generic module
offers; it never patches one.

## Install

Clone beside the other Oteny repositories and put this folder on the addons
path before the business's repository, so a module here shadows nothing and is
shadowed by nothing:

```zsh
git clone git@github.com:otenycom/otenydoo.git ~/oteny/otenydoo
# addons path, in this order
~/odoo/odoo19/addons,~/odoo/enterprise19,~/oteny/otenydoo,~/oteny/radar
```

A business repository (radar is the first) carries this repository as a git
submodule at `otenydoo/`, so its odoo.sh build sees the same commit its
developers do. Upgrade order after a pull: the modules here first, then the
business's (`-u oteny_shortcut,oteny_audit,oteny_backup_trigger,oteny_bot,oteny_knowledge_sync,riverflow`).

## Tests

Every module carries its tests under `<module>/tests`. Run them with the
parallel runner on a test database:

```zsh
~/odoo/venv/bin/python3 ~/odoo/odoo19/odoo-bin --stop-after-init --test-enable \
  --addons-path=~/odoo/odoo19/addons,~/odoo/enterprise19,~/oteny/otenydoo \
  -d cr-test -u oteny_shortcut,oteny_audit,oteny_bot,riverflow,oteny_knowledge_sync,odoo_parallel_tests \
  --test-tags=oteny_shortcut,oteny_audit,oteny_bot,riverflow,oteny_knowledge_sync,odoo_parallel_tests
```

The GitHub workflow `tests.yml` runs the community-only modules on the official
Odoo 19 image. `riverflow` and `oteny_knowledge_sync` depend on Enterprise
modules (`documents`, `knowledge`), so their tests run on a developer's or the
lab's database, not on the public runner.

## Talents

`talents/<bundle>/` holds the Talents a bot reads to work these modules; see
[`talents/README.md`](talents/README.md). A Talent and its module are one git
ref.

## Agent skills and Knowledge

`.claude/skills/` holds the skills our developers and AI agents read, one per
module plus the generic Odoo 19 craft (`odoo-development`). `oteny_knowledge_sync`
publishes them into the Knowledge app of every Odoo that lists this repository
as a root, so key users read the same text. Start at
[`.claude/skills/SKILL.MD`](.claude/skills/SKILL.MD).
