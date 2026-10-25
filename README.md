# Otenydoo

Oteny's generic Odoo 19 modules, layered, and the Talents a bot reads to work
them. Every business Odoo that Oteny serves depends on these modules. None of
them depends on a business, and no code here names a business's model. That
one rule is what keeps this repository generic, and the customer-template gate
in `.git_hooks/` holds it on every commit.

## The modules

| Module | Depends on | Job |
|---|---|---|
| `oteny_base` | `base` | The section "Oteny" under Settings › Technical, where the Oteny modules put their menus. |
| `oteny_shortcut` | `web` | Shortcuts and filters on any model's views. |
| `oteny_audit` | `base`, `mail` | The audit trail: who changed which field when, on any model. |
| `oteny_backup_trigger` | `base` | Makes Odoo.sh take a backup: a new version of this module only, pushed to the main branch, makes Odoo.sh back up before the upgrade. |
| `oteny_bot` | `base`, `mail`, `web` | The bridge between an Oteny business bot and this Odoo: the Discuss channel, the activity log, the form session, and the work contract the platform calls. |
| `oteny_article` | `base`, `html_editor` | Articles on Odoo Community: a tree of HTML articles with a search box; code and module data own locked articles (synced skills, prompts). Replaces the Enterprise Knowledge app. |
| `oteny_knowledge_sync` | `base`, `mail`, `oteny_article` | Publish every configured repository's `.claude/skills` as locked articles, one tree per root. A markdown skill path is resolved across every configured root, so a CrewRadar article can name an Otenydoo skill. |
| `oteny_home` | `web` | A home screen for Odoo Community: every app as an icon, with a search box. |
| `oteny_color_scheme` | `web` | Light and dark mode for Odoo Community: a Theme preference per user. |
| `oteny_cloud_filestore` | `base`, `mail`, `web`, `oteny_base` | File contents in an S3-compatible bucket: production writes, a clone reads with a read-only key. |
| `oteny_oops` | `base`, `web`, `oteny_base` | The Oops Log: the error dialog writes every fault a user meets in the web client, for the administrator. Replaces the Share button of Enterprise Documents. |
| `riverflow` | `base`, `mail`, `oteny_shortcut`, `oteny_bot` | The workflow engine: services, states, transitions, and the bot dispatch that hands work to a bot. |
| `odoo_parallel_tests` | `base` | The parallel test runner: clones the test database and runs batches in worker processes. |

The layering is the dependency direction. `oteny_base`, `oteny_shortcut`,
`oteny_audit`, `oteny_backup_trigger`, `oteny_bot`, `oteny_article`,
`oteny_home` and `oteny_color_scheme` sit at the top with no dependency among
them. `riverflow` sits below `oteny_shortcut` and `oteny_bot`;
`oteny_knowledge_sync` below `oteny_article`; `oteny_cloud_filestore` and
`oteny_oops` below `oteny_base`. `odoo_parallel_tests` is tooling beside them.
Every module runs on Odoo Community: none depends on an Enterprise module. A
business module extends a generic one through inheritance and the hooks the
generic module offers; it never patches one.

## Install

Clone beside the other Oteny repositories and put this folder on the addons
path before the business's repository, so a module here shadows nothing and is
shadowed by nothing:

```zsh
git clone git@github.com:otenycom/otenydoo.git ~/oteny/otenydoo
# addons path, in this order
~/odoo/odoo19/addons,~/oteny/otenydoo,~/oteny/radar
```

A business repository (radar is the first) carries this repository as a git
submodule at `otenydoo/`, so its odoo.sh build deploys one known commit. Its
developers write and run the modules from the clone at `~/oteny/otenydoo`.
Upgrade order after a pull: the modules here that the business installs first,
then the business's own.

## Tests

Every module carries its Python tests under `<module>/tests`, tagged with the
module name. A module with screen code also carries hoot tests under
`<module>/static/tests`. Run the tests of the modules you changed on a test
database: `-i` on a new database, `-u` on a database that has the modules.

```zsh
~/odoo/venv/bin/python3 ~/odoo/odoo19/odoo-bin --stop-after-init --test-enable \
  --addons-path=~/odoo/odoo19/addons,~/oteny/otenydoo \
  -d otenydoo-test -i oteny_base,oteny_oops --test-tags=oteny_base,oteny_oops
```

One hoot test file runs without `-u`, for example
`--test-tags "/web:WebSuite.test_unit_desktop[@oteny_oops/error_dialog_patch]"`
(the file `oteny_oops/static/tests/error_dialog_patch.test.js`).

The GitHub workflow `tests.yml` installs and tests `oteny_shortcut`,
`oteny_audit`, `oteny_backup_trigger`, `oteny_bot`, `odoo_parallel_tests`,
`oteny_base` and `oteny_oops` on the official Odoo 19 image. The other modules
run their tests on a developer's database, and in the full test suite of a
business that installs them (radar's runs every module here).

## Talents

`talents/<bundle>/` holds the Talents a bot reads to work these modules; see
[`talents/README.md`](talents/README.md). A Talent and its module are one git
ref.

## Agent skills and articles

`.claude/skills/` holds the skills our developers and AI agents read, one per
module plus the generic Odoo 19 craft (`odoo-development`).
`oteny_knowledge_sync` publishes them as locked articles (`oteny_article`) in
every Odoo that lists this repository as a root, so key users read the same
text. Start at [`.claude/skills/SKILL.MD`](.claude/skills/SKILL.MD).
