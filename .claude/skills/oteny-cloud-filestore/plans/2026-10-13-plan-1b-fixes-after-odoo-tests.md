# Cloud Filestore Plan 1b — Fixes after the Odoo attachment tests

> Status: PROPOSED (13-Oct-2026), waiting for the Radar dev team's approval.
> Spec: [../references/design-decisions.md](../references/design-decisions.md).
> Follows [plan 1](2026-10-12-plan-1-core-module.md).

## Why this plan exists

On 13-Oct-2026 the standard Odoo tests that work with attachments (61 tests
in `base`, `web` and `mail`: `TestIrAttachment`, `TestPermissions`,
`TestMergePartner`, `TestImage`, five asset-bundle classes,
`TestDiscussAttachmentController`, `TestMessageController`,
`TestMessageLinks`, `TestAvatarAcl`) ran three times:

| Run | Result |
|---|---|
| Plain Odoo, module not installed | 61 of 61 pass |
| Module installed, no bucket (mode `off`) | 61 of 61 pass |
| Module in production mode, Google Cloud Storage `dev/` | 59 of 61 pass |

The two differences in production mode, and one finding in the log:

1. `TestImage.test_05_web_image_access_token` runs a request inside
   `freeze_time(...)`, a frozen clock at the token's expiry time. `boto3`
   signs the S3 request with that clock, so Google answers
   `403 RequestTimeTooSkewed`, the read fails and the module answers 404.
   This is a property of the test, not of real use: the clock never stands
   still on a server. **No change.** It is recorded as a known limit of
   running Odoo's own tests in production mode.
2. `TestPermissions.test_field_read_permission` read the image of
   `base.main_partner` as empty bytes. Two causes:
   - **a bug against D24**: after an upload, the module removed the local
     file even though a local-only attachment (the company logo, written
     while `base` was installed) used the same file name, because the
     content was identical. D24 says the local copy stays in that case.
   - **a design gap in the load order**: during a module update (`-u`) Odoo
     loads module after module. The test ran while only `base` was loaded;
     `oteny_cloud_filestore` came 32nd of 41. Until it is loaded, plain
     Odoo reads files, from local disk only, so every file that is only in
     the bucket reads as empty bytes for the modules loaded before it: for
     their tests, data files and migrations. On a CrewRadar update the
     business modules do not depend on this module, so they can load
     before it.
3. The log showed `429 SlowDown`: Google allows about one write per second
   to the same object name, and the module uploads a file again even when
   the object already exists. Nothing was lost (the file stayed local and
   the job tries again), but every repeated save of the same content costs
   an upload and can hit the limit.

## The changes

### Change 1 — Keep the local file that a local-only attachment uses

**What changes.** After a confirmed upload, the module removes the local
file only when no attachment with `cloud_local_only` uses the same file
name. This applies in two places: the save (`_file_write`) and the upload
job (`_cloud_filestore_upload_pending`). One small helper answers the
question "does a local-only attachment use this name?" with one SQL query,
so both places use the same rule.

**Why.** D24: asset bundles and module files never depend on the bucket.
Today a user file with the same content (the same hash, so the same name)
removes their local copy, and they silently become bucket files.

**Cost.** One extra query per upload. Nothing else changes.

**Proof.** A test: a local-only attachment and a user attachment with the
same content; after the save of the user attachment the object is in the
bucket and the local file still exists. The same for the upload job.

### Change 2 — Do not upload an object that is already there

**What changes.** Before an upload, the module asks the bucket for the size
of the object (`head`). When the object exists with the same size, it skips
the upload; the name is the hash of the content, so the same name and size
means the same file. In the upload job the existing `head` after the upload
moves before it, so the job also skips files that are already in the bucket
(for example after a reboot during the first push).

**Why.** It avoids the `429 SlowDown` limit of Google for repeated saves of
the same content, and it makes a repeated save and a resumed first push
cheaper. D13 already named this option ("asks first (`HEAD`) or uploads
again"); the test shows that asking first is the better choice.

**Cost.** For a new file, one extra `head` request (about 27 ms on the lab
store) before the upload. For a file that exists, one `head` instead of an
upload. A `429` keeps its current meaning: a refusal of that one file, no
alert, the file stays local and the job tries again later.

**Proof.** Tests: a save of content that is already in the bucket makes no
put; a save of new content makes a head and a put; the upload job skips an
object that exists and removes the local copy when no local-only row needs
it.

### Change 3 — Read from the bucket from the first module on

**What changes.**
- The module gets a `post_load` hook in its manifest. When Odoo imports the
  module at server start, the hook wraps the read method `_file_read` of
  Odoo's base `ir.attachment` class. From then on every read in every
  database goes through the wrapper, also while `base` is loading.
- The wrapper does what the module's read does today: local file first; if
  it is missing and the database is in production or clone mode, the object
  from the bucket. The decision about the mode reads only things that exist
  from the first moment: the system parameters, `odoo.conf` and
  `database.is_neutralized`. A database without the bucket parameters is in
  mode `off`, so the wrapper is invisible there, also in a database where
  the module is not installed.
- To run at server start, the module must be listed as a server-wide
  module in `odoo.conf`: `server_wide_modules = base,web,oteny_cloud_filestore`
  (plus any module that the server already lists there). Without that line
  the hook still runs, but only when the module itself is loaded, which is
  the behaviour of today.
- The module's own `_file_read` override goes away; the wrapper is the one
  place of the read logic, so there is no second copy.
- Before the module is loaded, the alert model does not exist yet; a read
  failure in that phase is logged only. After the module is loaded, it
  alerts as today.
- A guard: in production mode, when the module is not in
  `server_wide_modules`, the module logs a warning at start and posts it
  once to the alert channel, so a missing line is visible.

**What does not change.** Writes during an update stay as they are: a file
that an early module writes goes to the local disk, and the upload job
moves it later. That is safe and needs no change. Downloads in the browser
(`_to_http_stream`) run after all modules are loaded, so they need no early
hook.

**Why.** A production update must never see an empty file where a real
file exists. Today a migration or a recompute of a module that loads before
this one could read an empty document and act on it.

**Cost.** One more line in the production `odoo.conf`, next to the write
key. A wrapper on a method of Odoo's core class: Odoo calls `post_load`
hooks for exactly this purpose, and the wrapper keeps Odoo's own behaviour
for every local file and in mode `off`.

**To check on Odoo.sh before the rollout.** Odoo.sh reads
`/home/odoo/.config/odoo/odoo.conf` (checked on test1 on 9-Oct-2026); the
check is that a `server_wide_modules` line there reaches the workers and is
not overruled by Odoo.sh's own start command. The check runs on test1 and
writes the result into this plan.

**Proof.**
- A test that calls the wrapped base method directly and reads a file that
  is only in the (fake) bucket.
- The real proof: the 61 Odoo tests run again in production mode with
  `--load base,web,oteny_cloud_filestore`; `TestPermissions` must pass, and
  the only remaining difference must be the frozen-clock test of item 1.

### Change 4 — Documentation

The skill (`SKILL.md`, `references/lab-results.md`) gets: the
`server_wide_modules` line in the setup, the result of the Odoo test runs,
the frozen-clock limit, and the `head` before upload. The rollout condition
gets the `odoo.conf` line.

## Order and review

The four changes are built in this order, each with its own tests and an
independent review: 1, 2, 3, 4. After change 3 the module's own suite and
the 61 Odoo tests run again in production mode against Google `dev/`. The
branch stays `feat/cloud-filestore`.
