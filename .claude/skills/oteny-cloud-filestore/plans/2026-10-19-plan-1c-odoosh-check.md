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

- The OPEN check: with `server_wide_modules = base,web,oteny_cloud_filestore`
  in `/home/odoo/.config/odoo/odoo.conf`, the module is server-wide in the
  HTTP workers and in the cron worker. Evidence: no "not server-wide" warning
  in `~/logs/odoo.log`, and the system parameter
  `oteny_cloud_filestore.not_server_wide_alerted` stays empty. The same holds
  after a restart and after a rebuild of test3. A control run without the line
  shows the warning, so the evidence can fail.
- Writes and the upload job in production mode: 500 files and at most 240 s
  per run, the local copy removed only after the bucket confirms it.
- Reads in clone mode from `dev/odoosh-test3/`.

## Result

Not run yet.
