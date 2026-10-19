# Cloud Filestore Plan 1c — The check on Odoo.sh (test3)

> Status: AGREED (19-Oct-2026, Radar dev team), not run yet.
> Spec: [../references/design-decisions.md](../references/design-decisions.md).
> Follows [plan 1b](2026-10-13-plan-1b-fixes-after-odoo-tests.md).

## Why this plan exists

The Rollout Condition in [SKILL.md](../SKILL.md) has one OPEN check: Odoo.sh
reads `/home/odoo/.config/odoo/odoo.conf`, but nobody knows if a
`server_wide_modules` line in that file reaches the HTTP workers and the cron
worker, or if the start command of Odoo.sh overrules it. Without the line, a
module update can read empty bytes for a file that is only in the bucket
(Setup, step 7). So the write key must not go to production before this check
is done. Every measurement so far ran on the developer laptop
([lab-results.md](../references/lab-results.md)); the time per file from
Odoo.sh to Google Cloud Storage is not known.

## Decisions (interview, 19-Oct-2026)

1. **Branch: `test3`, not `test1`.** `test1` is the CI staging of the
   `dev -> test1 -> main` line, it accepts only `dev`, and other tests run
   there. `test3` is an ad-hoc staging slot that takes any radar branch with
   a hard reset (`branch-to-test3`), on a fresh restore of a `main` backup.
   The SKILL.md text "must be done on test1" is replaced by this decision.
2. **Branches.** otenydoo `feat/cloud-filestore` gets otenydoo `main` merged
   in and is pushed. Radar gets the paired branch `feat/cloud-filestore`
   (`pair-otenydoo`), with `boto3>=1.36` in radar `requirements.txt`, pushed
   with `commit-with-otenydoo`. Only this branch goes to `test3`; `dev` does
   not get the module or boto3 from this plan.
3. **Production mode on test3, with dev keys only.** The `database.is_neutralized`
   parameter is removed on the test3 database. `odoo.conf` on test3 gets the
   dev write key (it works only on `dev/`), and `key_prefix` is
   `dev/odoosh-test3/`. The production folder `main/` is never written. The
   removal of the parameter does not undo the neutralization itself: the
   outgoing mail servers and the crons that it switched off stay off.
4. **A full first push.** The upload job runs until every user file of the
   test3 database is in `dev/odoosh-test3/`, as a rehearsal of the first
   migration of production (about 24,000 files). This gives the time per
   file from Odoo.sh against the 230 ms per file of the laptop (roadmap item
   "Measure once from an Odoo.sh branch"). `dev/` is larger than usual until
   the clean-up.
5. **Then clone mode.** test3 is set back to a clone (the parameter
   `database.is_neutralized` set, the write key out of `odoo.conf`). The files
   now have no local copy, so every read goes to `dev/odoosh-test3/` with the
   dev read key.
6. **Clean-up.** At the end, the objects under `dev/odoosh-test3/` are deleted
   with the dev write key, and test3 is restored from a `main` backup before
   its next use.
7. **Keys.** A key is never written to the repository, a commit, a log, or a
   command line that a shell or Odoo logs. It goes from 1Password (vault
   Oteny) to the server through standard input only.

## What the check must prove

- The OPEN check: with `server_wide_modules = base,rpc,web,oteny_cloud_filestore`
  in `/home/odoo/.config/odoo/odoo.conf`, the module is server-wide in the
  HTTP workers and in the cron worker. Evidence: no "not server-wide" warning
  in `~/logs/odoo.log`, and the system parameter
  `oteny_cloud_filestore.not_server_wide_alerted` stays empty. The same holds
  after a restart and after a rebuild of test3. A control run without the line
  shows the warning, so the evidence can fail.
- Writes and the upload job in production mode: 500 files and at most 240 s
  per run, the local copy removed only after the bucket confirms it.
- Reads in clone mode from `dev/odoosh-test3/`.

## Result (19-Oct-2026)

test3 build `cuneus-test3-40107605`, radar `2d5eba9f`, otenydoo `06bd4fe`,
boto3 and botocore 1.43.114 from `requirements.txt`. The module was installed
with a second `odoo-bin -i oteny_cloud_filestore --stop-after-init --no-http`
next to the running server: 53,990 rows became user files and 637 stayed
local-only; the upload cron was created active although the restore had
neutralized the other crons.

**The OPEN check: the line in `odoo.conf` is used.**

- The start command of Odoo.sh (`/proc/<pid>/cmdline`) passes
  `--config /home/odoo/.config/odoo/odoo.conf` and has no `--load`. In Odoo a
  command-line option overrules the file only for an option that is on the
  command line, so `server_wide_modules` comes from the file.
- `odoo.conf` (dated 29-Jan-2023) survived the restore of 19-Oct-2026 and the
  deploy push that followed it.
- Control in production mode without the line: the restart logged
  `oteny_cloud_filestore is not a server-wide module`, and the first upload
  run set `not_server_wide_alerted` (08:48:07; no `alert_channel_id`, so the
  alert went to the log only).
- With `server_wide_modules = base,web,oteny_cloud_filestore` and
  `odoosh-restart http`: no warning at the registry load, and the next upload
  run (cron thread, 08:59:57) removed `not_server_wide_alerted`.
- test3 runs one process (`--workers=0 --max-cron-threads=2`), so HTTP and
  cron are threads of that process; `odoosh-restart cron` answers "no cron
  workers are alive". Production runs worker processes (its log shows
  "Worker process ... was killed"), so its start command is not proven by
  test3.
- The skill's line leaves out `rpc`, which is in the Odoo 19 default
  (`base,rpc,web`). On test3 `/xmlrpc/2/common`, `/jsonrpc`, `/web/version`
  and `/json/version` still answered, because `rpc` is installed in the one
  database.
- Decision (Radar dev team, 19-Oct-2026): the line keeps `rpc`, so the
  skill's line is `server_wide_modules = base,rpc,web,oteny_cloud_filestore`.
  Checked on test3 after `odoosh-restart http` (09:01): no "not server-wide"
  warning, and `/xmlrpc/2/common`, `/jsonrpc`, `/web/version` and
  `/json/version` answered.

**Writes from Odoo.sh.** Two upload runs: 500 files in 80.64 s and 500 in
83.04 s, so about 161 to 166 ms per file, 0 refused, 0 not confirmed; the local
copy is removed after the confirming `head`. The database has 25,395 local
files (the 53,990 rows share them), so the first push needs about 51 runs, or
about 8.5 hours through the cron.

**The line after a rebuild.** The deploy of plan 1d (otenydoo `be06dee`,
radar `d6da477`) rebuilt test3 at 10:09: a new Odoo process, `odoo.conf`
unchanged (with `server_wide_modules = base,rpc,web,oteny_cloud_filestore`),
the start command again with `--config` and without `--load`, and no
"not a server-wide module" warning at the registry load.

**The first push.** The old cron moved 500 files per 10-minute run (8:48 to
10:00, 4,024 files). From 10:10 the cron of plan 1d ran: calls of about 24 s
with about 150 uploads each (about 162 ms per upload), 10 calls per cron run
(241 to 245 s), 3 to 58 s between runs, about 330 files per minute. It
logged `fully done` at 11:11:30 with 0 refused and 0 not confirmed. 518 local
files stayed: local-only files and names they share. In all, 24,877 files
went to `dev/odoosh-test3/`.

**Reads in clone mode.** At 11:15 test3 was a clone again
(`database.is_neutralized` set, the write key out of `odoo.conf`, the
"unknown option" warnings gone). 200 of the newest user files that are only
in the bucket were read with the read key (`odoo-bin shell`, one after the
other): 200 matched their SHA-1 checksum, 0 differed, 0 were empty; 94.2 MB in
48.6 s, about 243 ms per file (about 470 KB per file on average).

Still open: the clean-up (delete `dev/odoosh-test3/`, restore test3 from a
`main` backup) and the check of the production start command (SKILL.md,
Rollout Condition).
