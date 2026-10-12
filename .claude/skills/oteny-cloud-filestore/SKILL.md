---
name: oteny-cloud-filestore
description: Generic Odoo 19 module that keeps the contents of user files (ir.attachment) in an S3-compatible bucket. A production database writes with a key from odoo.conf, a clone reads with a read-only key from system parameters and never writes or deletes. Covers the modes (off, production, clone), the setup, the behaviour on save, read, download and delete, the outage fallback and alert, the catch-up cron, the reverse migration, the known limits and the live tests. Use when you set up the bucket for a database, debug a missing file on a clone, read an outage alert, move files back to the local filestore, or extend the module.
---

# Cloud Filestore (oteny_cloud_filestore)

The module stores the contents of user files in an S3 bucket instead of only
in the local filestore. The reason is the clone. A copy of a production
database (a test or staging build) can then read the real files with a
read-only key, and it cannot damage them.

## When to Use This Skill

- **Administrators**: Set up the bucket, read an outage message, move files
  back to the local filestore.
- **Developers**: Debug a file that is missing on a clone, extend the module,
  run its tests.
- **AI Agents**: Check the mode of a database before you change a file
  operation, and keep a clone from ever writing to the bucket.

Design decisions D1 to D27 are in
[design-decisions.md](references/design-decisions.md). The build plans are in
the `plans/` folder of this skill.

## The Three Modes

Odoo decides the mode on every file operation, in this order:

1. **Off**: the system parameters `oteny_cloud_filestore.endpoint_url` or
   `oteny_cloud_filestore.bucket` are empty. Odoo behaves as without the
   module.
2. **Production**: both write options are set in `odoo.conf` AND the system
   parameter `database.is_neutralized` is not set. The database writes and
   reads with the write key.
3. **Clone**: every other case. The database reads with the read key from the
   system parameters. A clone never puts and never deletes an object, also
   when somebody copied the write key into its `odoo.conf`, because a
   neutralized database is always a clone.

An incomplete key never falls back to ambient credentials (environment
variables, `~/.aws`, an instance profile). The store is then disabled, and a
production save keeps the file local.

## Setup

1. Make a bucket at an S3-compatible provider.
2. Make a write key with put, get, head and delete rights on the bucket.
3. Make a read key with only `s3:GetObject`.
4. Set these system parameters (all prefixed `oteny_cloud_filestore.`):
   `endpoint_url`, `region`, `bucket`, `addressing_style` (default `path`),
   `key_prefix` (default empty), `read_access_key_id`,
   `read_secret_access_key`, `hold_days` (default `365`) and
   `alert_channel_id` (the id of the `discuss.channel` for outage messages).
   `outage_since` is internal state: do not edit it.
5. Put the write key in `odoo.conf` of the production server only:
   `cloud_filestore_write_access_key_id` and
   `cloud_filestore_write_secret_access_key`.
6. Restart Odoo, so the options are read.

The write key lives only in `odoo.conf`, so a database copy cannot carry it.
The read key is in a system parameter, so a clone has it from the copy.
The object name in the bucket is `key_prefix` plus the `store_fname` of the
attachment (for example `ab/ab12...`).

## What Happens

- **A save** (production): Odoo writes the file to the local filestore as
  usual. Then the module puts the bytes in the bucket. After the put is
  confirmed, the local copy is removed. The file name is the content hash,
  so the same content is one object.
- **A read**: if the local file exists, Odoo reads it. If not, the module
  reads the object from the bucket. A bucket error never raises: the caller
  gets empty bytes, as Odoo gives for a missing local file.
- **A download in the browser**: Odoo reads the bytes from the bucket and
  sends them itself. The browser never sees the bucket. A file that is
  missing in the bucket gives a 404 and never an empty 200 that a browser
  could cache.
- **A delete**: Odoo's garbage collection removes the local file as before.
  In production the object in the bucket stays for the hold time
  (`hold_days`, default 365). The hold time counts from the last delete of
  that name. A name that is used again before the hold time ends keeps its
  object. A clone never deletes in the bucket. A bad `hold_days` value never
  shortens the hold time.

## When the Bucket Is Down

A save never fails and never loses the file. On a bucket error the file stays
in the local filestore. The module posts one Discuss message in the channel
`alert_channel_id` when the outage starts, and one when the bucket works
again. Several workers at the same time still post once.

A cron job, "Cloud filestore: upload local files to the bucket", runs every
10 minutes. It uploads the local user files in batches of 500 and removes
the local copy only after the bucket reports the same size. It stops at the
first bucket error. The same job does the first migration of an existing
database: install the module, set the parameters and the write key, and the
job moves the old files in batches.

## Files That Stay Local

Asset bundles (url starting with `/web/assets/`) and files written while a
module is installed or updated never go to the bucket. Odoo rebuilds a
bundle at any time, and a module file comes back from its module. The field
`cloud_local_only` on the attachment records this, so the catch-up job skips
these files too.

## The Reverse Migration

To move the files back to the local filestore, run this in `odoo-bin shell`
on a database that is in production or clone mode:

```python
env["ir.attachment"]._cloud_filestore_download_all(limit=500)
env.cr.commit()
```

The method returns the number of files written. Run it again until it
returns 0. It never overwrites a local file, it checks that the bytes match
the content-hash name, and it stops at the first bucket error. After the
last run, clear `oteny_cloud_filestore.endpoint_url` to switch the module
off.

## Known Limits

- A download holds the whole file in memory while Odoo sends it. Large files
  need the chunked streaming of plan 2.
- `head` on a missing bucket returns None. The module counts that as "not
  confirmed", so the local file stays. This is safe, and it means a wrong
  bucket name shows as files that never leave the local filestore.
- A clone reads every file from the bucket each time. A read cache comes in
  plan 2 (D21).
- Plan 2 also builds the command `odoo-bin cloud_dump` (D9, D26) and the
  copy of the bucket (D16, D22, D23, D27).

## Tests

The tests are tagged `oteny_cloud_filestore`. Most use an in-memory fake
store. The live tests (`test_cloud_store_live.py` and
`test_end_to_end_live.py`) use a real bucket and skip without configuration.

To run them, set the environment variable
`OTENY_CLOUD_FILESTORE_TEST_CONFIG` to the path of an INI file with a section
`[s3]` and the keys `endpoint_url`, `region`, `bucket`,
`write_access_key_id`, `write_secret_access_key`, `read_access_key_id` and
`read_secret_access_key`. Never print or commit this file. The live tests
write under a prefix of their own and delete their objects at the end.

```zsh
OTENY_CLOUD_FILESTORE_TEST_CONFIG=~/.config/oteny/cloud_filestore_test.ini \
~/odoo/venv/bin/python3 ~/odoo/odoo19/odoo-bin --stop-after-init -p 8071 \
  --addons-path=~/odoo/odoo19/addons,~/odoo/enterprise19,~/oteny/otenydoo \
  -d cf-test -u oteny_cloud_filestore --without-demo=True \
  --http-interface 127.0.0.1 --max-cron-threads 0 --test-enable \
  --test-tags=oteny_cloud_filestore --log-level=test
```

Use `-i` on the first run of a new database. The run passes when the log
shows `0 failed, 0 error(s)`.

## References

- [design-decisions.md](references/design-decisions.md): D1 to D27.
- [Plan 1: core module](plans/2026-10-12-plan-1-core-module.md).
