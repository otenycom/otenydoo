# Cloud Filestore Plan 3 — Go live on production (Wednesday 21-Oct-2026, night)

> Status: AGREED (20-Oct-2026, Thijs; Q1 changes Ries's rollout condition and
> needs his OK). Nothing in this plan has run yet. Answers in § 6.
> Builds on: [plan 2](2026-10-19-plan-2-rollout-blockers.md) (built, on test3),
> the Rollout Condition and Setup in [SKILL.md](../SKILL.md).

## 1. Goal and why Wednesday night

Production (`rivermen.cuneuscrew.eu`) starts to move its user files into the
bucket folder `main/` on Wednesday evening, so the first push of about
25,000 files (about 5.5 GB) runs overnight, when nobody saves files. The
first push takes at least about 70 minutes from Odoo.sh (161 ms per file,
plan 1c); the night gives margin for retries and an outage.

In the first phase production keeps every file local as well:
`cache_limit_mb = 10240` (10 GB, above the 6 GB filestore, Q3 of plan 2).
So on the go-live night nothing disappears from the local filestore, from the
Odoo.sh backups or from the CUNEUS40 standby. The files only gain a second
copy in the bucket. The local copies start to go only when the limit is
lowered later (§ 5).

## 2. Facts that shape the plan (checked 20-Oct-2026)

- Production does not have the module: `oteny_cloud_filestore` is not in its
  module list. Odoo.sh upgrades installed modules after a deploy, but it
  installs no new module by itself. The install is a manual step (§ 4, W3).
- The module joined radar's install and deploy lists on 19-Oct-2026, so a
  restore and the deploy's test gate install it, and every deploy raises its
  version.
- `boto3>=1.36` is in radar `requirements.txt` on the branch (commit
  `9beba6f2e`); it reaches production with the merge.
- Production has 25,742 attachments with a file.
- The keys for `main/` do not exist yet. The Google organization enforces
  `iam.disableServiceAccountKeyCreation`; a key needs a short project-level
  exception (test-store.md).
- The new CUNEUS40 script logs "skipped: cloud filestore not in use" (exit 2)
  while production has the module off or not installed, so it can go to
  CUNEUS40 before the go-live.

## 3. Tuesday 20-Oct (today)

| # | Task | Who | Done when |
|---|---|---|---|
| T1 | Answer § 6 | Radar dev team | answers written into § 6 |
| T2 | Merge `feat/cloud-filestore` into otenydoo `main` and radar `dev` (`merge-branches`), then `dev-to-test1` | Claude, after a yes | test1 green, the module on test1's code |
| T3 | Rehearse the install on test1: install the module (mode off), check that nothing changes (a save, a download, the crons do nothing) | Claude | written into § 7 |
| T4 | Make the `main/` keys: a write key (`roles/storage.objectAdmin` on `main/`) and a read key (custom role `cloudFilestoreObjectGet` on `main/`), each the HMAC key of its own service account, with the short policy exception; store both in 1Password | Thijs (gcloud, the policy exception) | two 1Password items; `test_write_key_stays_in_its_folder` and `test_read_key_cannot_list` pass with the `main/` keys and `key_prefix = main/test-…` |
| T6 | Log noise: the cache writes of the read path pass `log_exceptions=False`, so a 1 s lock timeout logs only the module's warning, not an Odoo "bad query" ERROR (§ 6 Q4) | Claude | tests green, on `feat/cloud-filestore` before T2 |
| T5 | CUNEUS40: reach it again (one tailnet), read the last weeks of `dr_sync.log` for rsync exit codes 23/24, then deploy the new `dr_sync.py`, `docker-compose.yml`, `odoo-entrypoint.sh` | Thijs + Claude | Tuesday's 22:00 UTC night ends with the three folders, `cloud_copy` "skipped", the standby up |

## 4. Wednesday 21-Oct

| # | Task | Who | Done when |
|---|---|---|---|
| W1 | Morning: check Tuesday's CUNEUS40 night (log, the three folders, the standby opens files) | Claude | written into § 7 |
| W2 | Start of the evening window: `test1-to-main` (production gets the code; the module is still not installed), then wait for the build (§ 6 Q2) | Thijs runs it, Claude watches | production green after the build |
| W2b | Make the private Discuss channel "Cloud Filestore" with the Radar dev team as members; its id goes into `alert_channel_id` (§ 6 Q3) | Thijs | channel exists |
| W3 | Evening, start of the go-live: install `oteny_cloud_filestore` on production (Apps, update the module list, install). Without bucket settings it is in mode off, so nothing changes. | Thijs | module installed |
| W4 | System parameters on production: `endpoint_url`, `region` (`auto`), `bucket`, `addressing_style` (`path`), `key_prefix` (`main/`), `read_access_key_id`, `read_secret_access_key` (the `main/` read key), `alert_channel_id` (§ 6 Q3), `cache_limit_mb = 10240`, `hold_days` stays 365 | Thijs | parameters set; mode is now clone (reads only, nothing uploaded) |
| W5 | SSH to production (Allow my IP), add to `/home/odoo/.config/odoo/odoo.conf`: `server_wide_modules = base,rpc,web,oteny_cloud_filestore`, `cloud_filestore_write_access_key_id`, `cloud_filestore_write_secret_access_key`; restart (`odoosh-restart http` and `cron`) | Thijs | mode is production |
| W6 | Check the start: the start command of an HTTP worker and of the cron worker (`/proc/<pid>/cmdline`) passes this `odoo.conf`; no "not a server-wide module" warning in the log; a test upload of one new attachment lands in `main/` and stays local (marked) | Claude + Thijs | written into § 7 |
| W7 | The first push runs overnight by the upload cron (every 10 minutes, with progress reporting) | — | — |

## 5. Thursday 22-Oct and after

| # | Task | Done when |
|---|---|---|
| R1 | Morning: the upload job reports nothing remaining; the count of user files with a confirmed object equals the count of user files; no alert in the alert channel; production works as before | written into § 7 |
| R2 | The first CUNEUS40 night with the module on (Thursday 22:00 UTC, or Wednesday's if the go-live ends before 22:00 UTC): `cloud_copy` copies by `--seed` links from the mirror (almost no downloads, because production still holds every file); then the § 4 check of plan 2: 50 files open with internet off | written into § 7 |
| R3 | Clean-up of plan 1c: delete the objects under `dev/odoosh-test3/`, restore test3 from a `main` backup | done |
| R4 | Only after R2 and a week of read times: lower `cache_limit_mb` in steps (Q3 of plan 2); each step shrinks the local filestore and the Odoo.sh backups | later plan |

**Rollback at any point:** remove the write key from `odoo.conf` and restart:
production is a clone again, writes stay local, and every file is still local
(10 GB limit). The reverse migration (SKILL.md) is only needed after the limit
was lowered.

## 6. Questions for the Radar dev team

- **Q1 — What holds back what.** *Situation:* Ries's rollout condition put
  the CUNEUS40 copy night before the write key. With `cache_limit_mb =
  10240` production keeps every file local, so the Odoo.sh backups and the
  standby still get every file the old way, and the real copy can only run
  after production is on. *Answer (Thijs):* go live at 10 GB; the CUNEUS40
  copy night (R2) holds back the first lowering of the limit (R4), not the
  write key. Tuesday's night still proves the folders and the script with
  the copy step skipped. **Ries must agree** before W5.
- **Q2 — Timing.** *Answer:* Wednesday evening, all in one window:
  `test1-to-main`, then W3–W6 right after the build. A failed production
  build moves the go-live, not the other way round.
- **Q3 — Alerts.** *Answer:* a new private Discuss channel "Cloud
  Filestore" with the Radar dev team only.
- **Q4 — Leftovers.** *Answer:* fix only the "bad query" log noise (T6)
  before the merge; the D25 gap, the faster first push and the other review
  minors wait.

## 7. Results

Filled in while the plan runs.

- **T6 (20-Oct-2026):** the read path's cache writes run with a context flag
  that makes `cache.py` pass `log_exceptions=False`; a 1 s lock timeout now
  logs only the module's warning. `test_cache_write_never_waits_for_the_request_transaction`
  asserts no `odoo.sql_db` ERROR. 183 tests pass with the live tests.
