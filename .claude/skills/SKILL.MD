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

## Where knowledge goes

The skill bundles follow the module layers (Ries, 11-Oct-2026). A rule, term or
pattern lives in the skill of the lowest layer that owns its concept: the Odoo
craft in `odoo-development`, a generic concept (services, states, transitions,
check results, the Radar) in the skill of its module here. A business repository
(radar) links to these skills and never copies them; its own skills hold only
what its own modules add. `AGENTS.md` holds only how agents work and may point
to a skill in one line.

Before you answer, design or write, search the skill of the concept and the
layers below it, and extend the section that exists instead of starting a
second copy. A rule written in the wrong place is not found by the next agent,
and two copies drift apart. Radar's version of this rule, with its layer table:
`radar/.claude/skills/SKILL.MD`, "Where Knowledge Goes".
