# Oteny Base

A small module that the Oteny modules depend on. Today it holds one menu:
the section "Oteny" under Settings › Technical (`oteny_base.menu_oteny`).

A menu record belongs to one module. Two Oteny modules that do not depend on
each other cannot share a section that one of them owns, so they both depend
on this module and put their menus in its section:

- `oteny_cloud_filestore`: Cloud Filestore Cache (sequence 10);
- `oteny_oops`: Oops Log (sequence 20).

The section has no action. Odoo hides it while it holds no menu that the
user can see. The Technical menu needs debug mode, so the section does too.

A database that already has a module that moves its menu into the section
gets `oteny_base` with the next upgrade of that module:
`ir.module.module.button_upgrade` installs a dependency in the state
`uninstalled`.

Test: `tests/test_oteny_base_menus.py` (tag `oteny_base`).
