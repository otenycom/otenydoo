# Cloud Filestore Plan 4 — An uninstall brings every file back

> Status: PLANNED (21-Oct-2026), not built. Interview with the Radar dev team
> of 21-Oct-2026; the decisions are in § 3. Branch `feat/cloud-filestore`
> in `~/oteny/otenydoo`.

## 1. Why this plan exists

Today an uninstall of `oteny_cloud_filestore` does not bring the files back.
The module has no `uninstall_hook`. After the uninstall, Odoo's base
`_file_read` reads a file that is only in the bucket as empty bytes and only
logs `_read_file reading …` (`odoo/addons/base/models/ir_attachment.py:148`).
The bucket keeps every object, but nothing in the database fetches them any
more, and the system parameters `endpoint_url` and `bucket` stay behind, so a
reinstall starts in clone or production mode at once.

The only safe way back today is the reverse migration of SKILL.md, by hand in
`odoo-bin shell`. The Radar dev team wants the uninstall itself to do that
work: fetch every file, and leave Odoo in off mode, as plain Odoo on its local
filestore.

## 2. A fact of Odoo 19 that decides where the check goes

An uninstall from the Apps screen (`button_immediate_uninstall`, also from the
uninstall wizard) runs `_button_immediate_function`. It first calls
`button_uninstall`, which sets the state `to remove`, and COMMITS that state
(`odoo/addons/base/models/ir_module.py:637`). Only after the commit,
`Registry.new(update_module=True)` runs the `uninstall_hook`
(`odoo/modules/loading.py:541`).

So the `uninstall_hook` must never refuse. An exception there leaves the
module in `to remove`. Odoo then refuses every later module operation
("Odoo is currently processing another module operation",
`ir_module.py:614`), and the next deploy with `-u` runs the hook again and
fails the build. The refusal goes into `button_uninstall`, which runs inside
the transaction that the commit at line 637 would write: a `UserError` there
rolls back the uninstall and the module stays installed and works.

## 3. Decisions (Radar dev team, 21-Oct-2026)

- **Q1 — The write key in `odoo.conf` does not block the uninstall.** After
  the uninstall the `ir.attachment` override is gone from the registry and the
  parameters are cleared, so the key has no effect. During the download,
  production keeps working as before: a new save goes to the bucket. The final
  check of § 4.2 catches such a file, because the uninstall only passes when
  every file is local. Accepted risk: a save in the few seconds between that
  final check and the registry reload.
- **Q2 — The uninstall answers "downloading, try again in X minutes".** The
  first click starts the download in the background and refuses with the
  count and an estimate. A later click passes once every file is local.
- **Q3 — The uninstall cleans the database of the module's settings.** Every
  system parameter with the prefix `oteny_cloud_filestore.` is deleted. The
  objects in the bucket stay.

## 4. Design

### 4.1 The download job runs as a cron

A new cron record `ir_cron_cloud_filestore_download`, inactive by default,
model `ir.attachment`, code `model._cloud_filestore_download_pending()`.

- `_cloud_filestore_download_pending()` wraps the loop of
  `_cloud_filestore_download_all`: it downloads in batches and reports through
  `ir.cron._commit_progress(done, remaining=…)`, the same pattern as the upload
  job of plan 1d. So it commits after each batch and keeps running until no
  file is left, without the HTTP time limit of a click.
- It stores its measured speed (seconds per file of the last run) in the
  system parameter `oteny_cloud_filestore.download_seconds_per_file`, for the
  estimate of § 4.2.
- `_cloud_filestore_download_all` stays the hand-run method of the reverse
  migration; both share one inner loop, so the rules (never overwrite, check
  the content hash, stop at a bucket or disk error, unmark the cache) stay in
  one place.

### 4.2 The check in `button_uninstall`

Override `button_uninstall` on `ir.module.module`. It acts only when the
recordset contains `oteny_cloud_filestore` and the mode is not off.

1. Count the names (`store_fname`) that have no local file. `cloud_local_only`
   rows count too: a local-only name with no local file is lost data, and the
   check must say so.
2. **None left:** clear every "in bucket" mark (the cache unmark of D2), so no
   eviction can run between this commit and the registry reload, then call
   `super()`.
3. **Some left, and the bucket has them:** in a separate cursor
   (`self.env.registry.cursor()`), activate the download cron and call
   `_trigger()`, then commit that cursor. Raise a `UserError`:
   "Downloading files back to the local filestore: N of M files remain.
   Try again in about X minutes." X = N × the measured seconds per file,
   rounded up to whole minutes; before the first batch, 0.243 s per file (the
   clone read of lab-results.md).
4. **Some left that can never download** (the last download run logged them
   as missing in the bucket or damaged): raise a `UserError` with the count,
   the first names and a pointer to the log. "Try again" would never pass.
   The job writes these names to a system parameter
   `oteny_cloud_filestore.download_failed` so the check can tell case 3 from
   case 4 without reading the log.

The separate cursor is needed because the `UserError` rolls back the
transaction of the click; the cron activation must survive it.

### 4.3 The `uninstall_hook` only cleans up

`uninstall_hook(env)` in `__init__.py`, named in `__manifest__.py`. It deletes
every `ir.config_parameter` whose key starts with `oteny_cloud_filestore.`
(endpoint, bucket, read keys, `outage_since`, `cache_limit_mb`,
`download_seconds_per_file`, `download_failed`). It never raises (§ 2). The
module's own records (crons, cache table, `cloud_local_only` column) go with
the normal uninstall.

### 4.4 What stays manual

- Remove `oteny_cloud_filestore` from `server_wide_modules` in `odoo.conf`.
  Without the parameters the read wrap is in off mode and does nothing, but
  Odoo would still import an uninstalled module at every start. The success
  path cannot show a message after the reload, so SKILL.md and the text of the
  refusal in § 4.2 step 3 name this step.
- The bucket and its objects stay. Deleting them is a separate decision.

## 5. Tasks

1. **Shared download loop + cron job** —
   `models/ir_attachment.py`, `data/ir_cron.xml`. Test in `tests/test_jobs.py`:
   the job downloads all files over several `_commit_progress` calls, records
   the speed, and records missing and damaged names.
2. **`button_uninstall` override** — new `models/ir_module.py`, add to
   `models/__init__.py`. Tests in a new `tests/test_uninstall.py`
   (`@tagged("post_install", "-at_install", "oteny_cloud_filestore")`):
   - off mode: `button_uninstall` passes at once;
   - files left: `UserError` with N, M and X; the download cron is active and
     triggered;
   - a missing object: `UserError` with the count and the log hint, no
     "try again";
   - every file local: passes and clears the cache marks;
   - another module in the recordset alone: no check.
   Odoo forbids module operations in tests (`current_test`,
   `ir_module.py:603`), so the tests call `button_uninstall` directly, which
   only writes the state, and never run `button_immediate_uninstall`. The
   separate cursor of § 4.2 step 3 needs a patch in the test
   (`TransactionCase` forbids a commit).
3. **`uninstall_hook`** — `__init__.py`, `__manifest__.py`. Test: the hook
   removes every `oteny_cloud_filestore.*` parameter and no other parameter.
4. **Skill** — SKILL.md: a section "Uninstall" beside The Reverse Migration,
   the manual `server_wide_modules` step, a roadmap line for plan 4, and the
   link under References. D2 in design-decisions.md: the reverse migration
   can now also start from the uninstall.
5. **Check on a clone** — on `cf-test` in clone mode against `dev/`: delete
   some local files, click Uninstall, see the refusal, wait for the cron, click
   again, and check that the module is gone, the files open, and no
   `oteny_cloud_filestore.*` parameter is left.

## 6. Open items

- None from the interview. The other session's uncommitted work on
  `__manifest__.py`, `tests/__init__.py` and SKILL.md (Discuss alert channel)
  must be committed before tasks 2–4 start, so two sessions never edit the
  same file.
