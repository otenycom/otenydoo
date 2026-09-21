# Set up a connection the owner offers

Load this when the owner wants you to work their own Odoo and the
project has not bound it for you. Everything stays on this bot. The
address, the database and the login go in a small file. The key goes
in a variable that the secure connect page sets.

## Checklist

1. Ask the owner for three things: the Odoo address (`https://…`),
   the database name, and the login of the bot user they made for
   you. Never ask for the key in chat. If the owner pastes a key or a
   password, do not repeat it. Tell them to replace it.
2. Pick a connection name after their system, such as `crm` or
   `erp`: lower case, digits, `-` and `_`. Never `odoo`. That name is
   the platform's own connection, and it points at another Odoo.
3. Raise the key through the secure connect page. On Oteny that is
   `connect_account`, with `env_var` of the form `<NAME>_ODOO_KEY`,
   for example `CRM_ODOO_KEY`. Send the owner the link. Wait until
   `credential_status` says the key reached this bot.
4. Write the connection file,
   `~/.hermes/data/oteny-odoo-access-talent/connections/<name>.yaml`:

       url: https://erp.example.com
       db: example-main
       login: bot
       key_env: CRM_ODOO_KEY

   Use flat `key: value` lines. Put no key, password or token in it.
5. Run the check. Go on only at `READY: yes`. Each `READY: no` line
   says what to fix.

       python3 ~/.hermes/skills/talents/oteny-odoo-access-talent/scripts/odoo_call.py \
         --connection <name> --check

6. Make one read the owner recognises, such as their own company
   name. Tell them the connection works.

## What the script refuses, and why

| Refused | Why |
| --- | --- |
| `key_env` starting with `OTENY_` or `TELEGRAM_` | The platform owns those names. `OTENY_CONN_ODOO_KEY` opens the platform's own Odoo, so the script would send that key to the owner's server. |
| A file that holds a key, password, secret or token | A key in a file is as exposed as a key in chat. |
| An address that is not `https://` | Except `http://` on loopback, for an Odoo inside this box. |
| A redirect | The key would follow it to the new host. Put the final address in the file. |
| A `mail.message` read with no `res_id` clause | A scan of all chatter can run the box out of memory. Read one record's chatter. |

## Map the models

The owner may ask for a map of their Odoo: which models exist, and
how they relate. Do not read `ir.model` or `ir.model.fields`. Odoo
gives those only to "Access Rights", and a bot never holds that
group. Use Odoo's documentation instead:

    python3 …/odoo_call.py --connection <name> --doc --out <file>
    python3 …/odoo_call.py --connection <name> --doc res.partner

`--doc` lists every model your login may read, with its field count.
`--doc <model>` gives every field of one model: its type, its label,
its relation target and its inverse field, and the model's method
names. Both are compact views. `--out <file>` writes Odoo's whole
document to a file instead, uncut, with help texts and method
signatures. Keep a map you make in your own data folder.

These calls need the "Technical Documentation" group. When the
script says your login is not in it, ask the owner's admin to add
that one group. Never ask for "Access Rights": it lets a login edit
users and groups.

## Limits

- Your login decides what you can do. A 403 is the owner's rule.
  Report it; do not retry it and do not look for another way in.
- `--check` proves that the key works and that the login exists. Odoo
  19 does not say which login a key belongs to.
- This lane runs the script from your terminal. A bot without a
  terminal reaches its Odoo only through `odoo_client`.
