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

1. Make a bucket at an S3-compatible provider. On Odoo.sh, add
   `boto3>=1.36` to the `requirements.txt` of the business repository before
   you install the module. Without it, the module cannot be installed. The
   module needs botocore 1.36 or newer for the checksum options. With an
   older botocore, the store fails to build. That only logs a warning and
   posts the alert, and the files stay local.
2. Make a write key with put, get, head and delete rights on the bucket.
3. Make a read key with only `s3:GetObject`.
4. Set these system parameters (all prefixed `oteny_cloud_filestore.`):
   `endpoint_url`, `region`, `bucket`, `addressing_style` (default `path`),
   `key_prefix` (default empty), `read_access_key_id`,
   `read_secret_access_key`, `hold_days` (default `365`, minimum `120`) and
   `alert_channel_id` (the id of the `discuss.channel` for outage messages).
   `outage_since` is internal state: do not edit it.
5. Set `alert_channel_id`. The Discuss alert needs it. Without it, an outage
   shows only in the log.
6. Put the write key in `odoo.conf` of the production server only:
   `cloud_filestore_write_access_key_id` and
   `cloud_filestore_write_secret_access_key`.
7. Restart Odoo, so the options are read. At every start, Odoo logs an
   "unknown option ... stored as-is" warning for each of the two options.
   This is expected: Odoo does not know the options, and the module reads
   them.

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
  gets empty bytes, as Odoo gives for a missing local file. In production, a
  store that cannot be used posts the alert. An object that is missing, or a
  refusal of one file, only logs a warning.
- **A download in the browser**: Odoo reads the bytes from the bucket and
  sends them itself. The browser never sees the bucket. A file that is
  missing in the bucket gives a 404 and never an empty 200 that a browser
  could cache.
- **A delete**: Odoo's garbage collection removes the local file as before.
  In production the object in the bucket stays for the hold time
  (`hold_days`, default 365). The hold time counts from the last delete of
  that name. A name that is used again before the hold time ends keeps its
  object. A clone never deletes in the bucket. A bad `hold_days` value never
  shortens the hold time. A value below 120 days is replaced by 120 with a
  warning, because Odoo.sh keeps backups for 3 to 4 months, and a backup
  must never point to a deleted object.

## When the Bucket Is Down

A save never fails and never loses the file. On a bucket error the file stays
in the local filestore. The module posts one Discuss message in the channel
`alert_channel_id` when the outage starts, and one when the bucket works
again. Several workers at the same time still post once.

A cron job, "Cloud filestore: upload local files to the bucket", runs every
10 minutes. It uploads the local user files in batches of 500 and removes
the local copy only after the bucket reports the same size. It stops at the
first outage error (no connection, a timeout, an HTTP 5xx), and it stops
after 240 seconds, so a run stays below the time limit of an Odoo.sh cron.
The next run continues. The same job does the first migration of an
existing database: install the module, set the parameters and the write key,
and the job moves the old files in batches.

A refusal that concerns the whole store counts as an outage. This is an HTTP
401 or 403, or one of the error codes `AccessDenied`, `InvalidAccessKeyId`,
`SignatureDoesNotMatch`, `NoSuchBucket`, `AllAccessDisabled` and
`InvalidBucketName`. Examples are a write key without the right to put, a
revoked key and a bucket that does not exist (for a put and a get;
see Known Limits for head). The file stays local, the
admins get one alert, and the cron job stops its run. The alert text never
holds a key.

A refusal of one file (for example `400 EntityTooLarge`) is not an outage.
The file stays local, the log shows a warning `upload of ... refused`, and
no alert is posted. A save and the cron job skip that file and continue.

An object that is missing in the bucket is not an outage either. A read
logs a warning and gives empty bytes. It never opens or closes the alert,
so a lost object cannot make the alert open and close again and again.

## Files That Stay Local

Asset bundles (url starting with `/web/assets/`) and files written while a
module is installed or updated never go to the bucket. Odoo rebuilds a
bundle at any time, and a module file comes back from its module. The field
`cloud_local_only` on the attachment records this, so the catch-up job skips
these files too.

The install of the module also flags the files that existed before. The
images of records that a real module created stay local, for example the
company logo and the avatar of the admin user. They stay local also after a
later change of that image. The images of imported records (xmlid module
`__import__` or `__export__`) are user data and go to the bucket. Every
other existing file is a user file.

## The Reverse Migration

To move the files back to the local filestore, do these steps in this order.
The order matters. In production mode the upload cron runs every 10 minutes,
and every save removes its local copy after the put. Both would undo a
download.

1. Remove the two write options
   (`cloud_filestore_write_access_key_id` and
   `cloud_filestore_write_secret_access_key`) from `odoo.conf` of the
   production server, and restart Odoo. Odoo is now in clone mode. New saves
   stay on the local disk, the upload cron does nothing, and reads still come
   from the bucket with the read key.
2. Run this in `odoo-bin shell`, and commit after each run:

   ```python
   env["ir.attachment"]._cloud_filestore_download_all(limit=500)
   env.cr.commit()
   ```

   The method returns the number of files written. It never overwrites a
   local file, and it checks that the bytes match the content-hash name.
   Repeat the run. Stop only when a run writes 0 files AND its summary log
   line (`Cloud filestore download job: ... written, ... missing in the
   bucket, ...`) shows 0 missing objects AND the log has no warning with
   "stopped" of any kind (`download job stopped at`, `stopped by an error`)
   AND no warning `not written`. A return value of 0 alone is not enough.
   The method also returns 0 after a bucket error, after a local disk error,
   and when every remaining object is missing or damaged.
3. Only then clear the system parameter `oteny_cloud_filestore.endpoint_url`
   (or `bucket`). Odoo is now in off mode and works as plain Odoo on its
   local filestore.

## Rollout Condition

Do not put the write key in the `odoo.conf` of a production database before
one of these two conditions is true:

- A second copy of the bucket exists (plan 2, the bucket copy).
- The owners accept two facts. First, the bucket is the only copy of the
  files once the Odoo.sh backups that still hold them expire (after 3 to 4
  months). Second, reads are slower until the read cache of plan 2 exists.

## Known Limits

- A download holds the whole file in memory while Odoo sends it. Large files
  need the chunked streaming of plan 2.
- `head` on a missing bucket returns None. A HEAD answer has no body, so the
  error code is only `404`, and the module reads that as "no such key". This
  is safe. The upload job puts first, and a put to a missing bucket raises
  `NoSuchBucket`, so the job stops and posts the alert. A None from `head`
  counts as "not confirmed", so the local file stays.
- A clone reads every file from the bucket each time. A read cache comes in
  plan 2 (D21).
- During an outage, a download of a file that is only in the bucket answers
  404, and an image shows as broken. There is no placeholder. A copy of a
  record with such a file (a duplicate, a forwarded mail) fails with "The
  file is temporarily unavailable; try again later.", so no empty copy is
  made. This holds when the store cannot be used. A file that is gone for
  good copies empty, as plain Odoo does.
- A file that is not local and not in the bucket logs a warning and posts no
  alert. A wrong `key_prefix` shows this way, so watch the log. A wrong
  `bucket` is different: a put or a get raises `NoSuchBucket`, and in
  production that posts the alert.
- A user file that a module update (`-u`) writes again stays local, because
  writes during a module install or update stay local. The attachment is
  still a user file, so the next upload run moves it. Seen once on a
  production copy: a `sign.document` PDF.
- The first push of a database is slow per file: about 104 ms per file, so
  about 42 minutes for 24,000 files in one run, or about 8 hours through the
  cron ([lab-results.md](references/lab-results.md)).
- Bucket deletes run inside the garbage collection lock of Odoo, one request
  at a time. Plan 2 batches them, before the first delete markers reach the
  hold time (R13).
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
`read_secret_access_key`, and the optional `key_prefix` (the folder the
test keys may write in, for example `dev/`). Never print or commit this
file. The live tests
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

The live test store of the development team (Garage, laptop only) and its
limits are described in [test-store.md](references/test-store.md).

## References

- [design-decisions.md](references/design-decisions.md): D1 to D27.
- [Plan 1: core module](plans/2026-10-12-plan-1-core-module.md).
- [test-store.md](references/test-store.md): the live test store.
- [lab-results.md](references/lab-results.md): measured latency and the
  first push of a production copy.

## Roadmap

**Status key:** `[x]` done · `[~]` in progress · `[ ]` not started

- [x] Plan 1, the core module: modes, upload on save, read and streaming,
  hold time, catch-up and reverse jobs, alert, live tests.
- [ ] Plan 2: the read cache (D21). The lab shows a read from the bucket is
  more than 100 times slower than a local read.
- [ ] Plan 2: streaming in chunks for large files (D18), `odoo-bin
  cloud_dump` (D9, D26), the copy of the bucket (D16, D22, D23, D27), and
  bucket deletes in batches (R13).
- [ ] Plan 2 candidate: a faster first push. Upload several files at the same
  time, or confirm an upload from the answer of the put instead of a
  separate `head`. Measured cost today: about 104 ms per file.
- [ ] Measure once from an Odoo.sh branch against the production bucket
  before the rollout.
