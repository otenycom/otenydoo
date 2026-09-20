# Otenydoo skills

> The skills our developers and AI agents read to work on the generic Oteny
> modules. `oteny_knowledge_sync` publishes this tree into the Knowledge app of
> every Odoo that lists this repository as a root.

## Available skills

| Skill | Description |
|-------|-------------|
| [otenydoo](otenydoo/SKILL.md) | The repository itself: the layering rule, the release order, how a business module extends a generic one. |
| [odoo-development](odoo-development/SKILL.md) | The Odoo 19 craft: module structure, coding patterns, XML, SCSS, tests, migrations, the parallel test runner. |
| [oteny-shortcut](oteny-shortcut/SKILL.md) | Shortcuts and filters on any model's views. |
| [oteny-audit](oteny-audit/SKILL.md) | The audit trail model and mixin. |
| [oteny-bot](oteny-bot/SKILL.md) | The bridge to an Oteny business bot: channel, activity log, form session, the work contract. |
| [riverflow](riverflow/SKILL.md) | The workflow engine: services, states, transitions, bot dispatch. |
| [oteny-knowledge-sync](oteny-knowledge-sync/SKILL.md) | Publishes this tree (and a consuming business's own) into Odoo Knowledge: sync process, link resolution across roots, image embedding, troubleshooting. |

`oteny_backup_trigger` and `odoo_parallel_tests` are small enough that their
`README.md` and the `odoo-development` references cover them.
