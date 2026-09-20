# Talents of Otenydoo

The Talents a bot reads to work these modules. One folder per bundle, in the
shape the Oteny Talent standard defines (`agent-profile.yaml`, `SKILL.md`,
`required_artifacts.yaml`, `README.md`). A Talent and the module it drives are
one git ref: a bot that runs a given commit of a module reads the Talent of the
same commit.

| Bundle | Module | What the bot learns |
|---|---|---|
| `oteny-odoo-access-talent` | `oteny_bot` | The form verbs (`views`, `list`, `open`, `set`, `save`, `discard`) on any Odoo model through the views a person already has. |
| `riverflow-execute-talent` | `riverflow` | Run a riverflow strip the way a person does: list the transition buttons, open the wizard, save. Claim, advance and hand back when a dispatch token is present. |

The platform delivers a bundle from this repository with its Talent source at
`talents/<bundle>` (`hh.talent_source.repo_subpath`). The Talent lint workflow
lints every bundle here on each change.
