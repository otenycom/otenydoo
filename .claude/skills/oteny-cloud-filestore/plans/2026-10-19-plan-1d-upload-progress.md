# Cloud Filestore Plan 1d — The upload job reports its progress to the cron

> Status: BUILT (19-Oct-2026), not committed.
> Built and tested locally, not yet measured on Odoo.sh (Radar dev team).
> Spec: this file. Context: [plan 1c](2026-10-19-plan-1c-odoosh-check.md),
> "Writes from Odoo.sh".

## Why this plan exists

On test3 the first push of a production copy (25,395 local files) moved 500
files per cron run, about 80 s of work, and then waited 10 minutes: about 8.5
hours for work of about 1 hour. Two causes in
`_cloud_filestore_upload_pending` (`oteny_cloud_filestore/models/ir_attachment.py`):

1. The job never calls Odoo 19's progress API, `ir.cron._commit_progress`.
   Odoo therefore logs every run as `fully done (#loop 1; done 0; remaining 0)`
   and waits the full interval, although thousands of files are pending.
2. A fixed count (`limit=500`) ends each call after about 80 s, far inside the
   240 s budget. No design decision gives a reason for 500.

How the progress API works (Odoo 19, `odoo/addons/base/models/ir_cron.py`):
`_run_job` calls the job in a loop while it reports `remaining > 0`: 10 calls
(`MIN_RUNS_PER_JOB`), and more only while the run is under 10 s
(`MIN_TIME_PER_JOB`). If work is left after the loop, the status is "partially
done" and `_reschedule_asap` adds a trigger for now: the next poll of a cron
worker runs the job again, after the other ready jobs. `remaining = 0` (or no
call at all) means "fully done": the normal interval. `_commit_progress`
commits the transaction; outside a cron it only commits. A `TransactionCase`
test forbids `cr.commit()`. `odoo-bin shell` rolls back on exit
(`odoo/cli/shell.py`): a run by hand needs its own `env.cr.commit()`.

## Global Constraints

- Generic module in the public repository `otenycom/otenydoo`, side clone
  `~/oteny/otenydoo`, branch `feat/cloud-filestore`. Odoo 19.
- **Do not commit.** The Radar dev team commits only when asked. Leave every
  change in the working tree. Do not stash, reset or touch other files.
- The job reports progress only when Odoo's cron runs it
  (`self.env.context.get("cron_id")`). A manual run (`odoo-bin shell`)
  reports nothing; the caller commits, as the Reverse Migration steps do.
- `remaining` counts only pending names that the current sweep did not reach
  (amended by the final review, see "Amendment: the resume point"). A name
  that a call reached and kept local on purpose (shared with a local-only
  file, refused, not confirmed, unreadable) never counts, so the cron cannot
  loop without end on it.
- After a bucket error (`failed`), `remaining` is 0, so the cron waits its
  interval during an outage.
- One call has `UPLOAD_TIME_BUDGET_SECONDS / MIN_RUNS_PER_JOB` seconds
  (240 / 10 = 24 s), imported from `odoo.addons.base.models.ir_cron`, so one
  cron run of 10 calls stays near the 240 s budget. The time is checked
  before each file, so a call can pass 24 s by one upload, and a run of 10
  calls can pass 240 s by up to ten uploads (documented, not changed).
- Code comments and skill texts follow the voice of the surrounding file.
- Test command (live tests skip without `OTENY_CLOUD_FILESTORE_TEST_CONFIG`;
  never set it):

  ```zsh
  cd ~ && ~/odoo/venv/bin/python3 ~/odoo/odoo19/odoo-bin --stop-after-init -p 8071 \
    --addons-path=$HOME/odoo/odoo19/addons,$HOME/odoo/enterprise19,$HOME/oteny/otenydoo \
    -d cf-test -u oteny_cloud_filestore --without-demo=True \
    --http-interface 127.0.0.1 --max-cron-threads 0 --test-enable \
    --test-tags=oteny_cloud_filestore --log-level=test > /tmp/cf-1d.log 2>&1
  grep -E "FAIL:|ERROR:|failed, " /tmp/cf-1d.log
  ```

  Pass: `0 failed, 0 error(s)`.

## Task 1: The upload job uses the progress API

Files: `oteny_cloud_filestore/models/ir_attachment.py`,
`oteny_cloud_filestore/tests/test_jobs.py`.

State before the task: `test_jobs.py` already holds three new tests
(`test_upload_reports_progress_to_the_cron`,
`test_upload_progress_ignores_files_that_stay_local`,
`test_upload_outage_reports_no_remaining`) and a helper `_progress_patch`.
They fail (red run: 3 failed of 23). They call the job without a cron
context; change them as below.

### 1a. Tests (replace the helper and the three tests with this code)

```python
    def _progress_patch(self):
        """Record the progress reports that the upload job gives to the cron runner."""
        return patch.object(type(self.env["ir.cron"]), "_commit_progress", return_value=0.0)

    def _as_cron(self):
        """The attachment model as Odoo's cron runner calls it."""
        cron = self.env.ref("oteny_cloud_filestore.ir_cron_cloud_filestore_upload")
        return self.attachment.with_context(cron_id=cron.id)

    def test_upload_reports_progress_to_the_cron(self):
        """With work left, the cron runner calls the job again at once; else it waits the interval."""
        for raw in (b"one", b"two", b"three"):
            self._save_local(raw)
        # A call has a tenth of the run budget, so the second file waits for the next call.
        clock = MagicMock()
        clock.monotonic.side_effect = itertools.chain([0, 0], itertools.repeat(30))
        with self._progress_patch() as progress, patch.object(ir_attachment_module, "time", clock):
            self.assertEqual(self._as_cron()._cloud_filestore_upload_pending(), 1)
        progress.assert_called_once_with(1, remaining=2)
        with self._progress_patch() as progress:
            self.assertEqual(self._as_cron()._cloud_filestore_upload_pending(limit=1), 1)
        progress.assert_called_once_with(1, remaining=1)
        # A run by hand reports nothing; the caller commits, as the Reverse Migration steps do.
        with self._progress_patch() as progress:
            self.assertEqual(self.attachment._cloud_filestore_upload_pending(), 1)
        progress.assert_not_called()
        with self._progress_patch() as progress:
            self.assertEqual(self._as_cron()._cloud_filestore_upload_pending(), 0)
        progress.assert_called_once_with(0, remaining=0)

    def test_upload_progress_ignores_files_that_stay_local(self):
        """A file kept local on purpose must not make the cron run again and again."""
        shared = self._save_local(b"shared bytes")
        self._save_local(b"shared bytes", cloud_local_only=True)
        refused = self._save_local(b"too large")
        self._save_local(b"normal")
        real_put = self.fake.put

        def refuse_one(name, data):
            if name == refused.store_fname:
                raise CloudStoreError("put_object: store refused (400 EntityTooLarge)")
            real_put(name, data)

        with self._progress_patch() as progress, patch.object(self.fake, "put", side_effect=refuse_one):
            with self.assertLogs(LOGGER, "WARNING"):
                self.assertEqual(self._as_cron()._cloud_filestore_upload_pending(), 2)
        progress.assert_called_once_with(2, remaining=0)
        self.assertTrue(self._local_exists(shared.store_fname))
        self.assertTrue(self._local_exists(refused.store_fname))

    def test_upload_outage_reports_no_remaining(self):
        """During an outage the cron waits its interval instead of calling the bucket again at once."""
        self._save_local(b"first")
        self._save_local(b"second")
        self.startPatcher(patch.object(type(self.attachment), "_cloud_filestore_alert"))
        with self._progress_patch() as progress, patch.object(
            self.fake, "put", side_effect=CloudStoreUnavailable("down")
        ):
            with self.assertLogs(LOGGER, "WARNING"):
                self.assertEqual(self._as_cron()._cloud_filestore_upload_pending(), 0)
        progress.assert_called_once_with(0, remaining=0)
```

Run the module tests: these three must fail, all others pass.

### 1b. Code

1. Import `MIN_RUNS_PER_JOB` from `odoo.addons.base.models.ir_cron`.
2. Keep `UPLOAD_TIME_BUDGET_SECONDS = 240`; let its comment say it is the
   budget of one cron run of the job. Add below it:

   ```python
   # Odoo's cron runner calls the job again at once while it reports work left
   # (ir.cron._commit_progress): 10 calls (MIN_RUNS_PER_JOB), and more only while
   # the run is under 10 s (MIN_TIME_PER_JOB). One call gets its share of the run
   # budget, so a run of 10 calls stays near it. The budget is checked before
   # each file, so a call can pass it by one upload.
   UPLOAD_CALL_BUDGET_SECONDS = UPLOAD_TIME_BUDGET_SECONDS / MIN_RUNS_PER_JOB
   ```

3. New helper on the model, used by the job for each batch and for the count
   of `remaining` (the same SQL the job runs today):

   ```python
   @api.model
   def _cloud_filestore_pending_names(self, names):
       """Return the names among `names` that a user file uses (not only local-only rows)."""
       # IS NOT TRUE, because rows that existed before the install can hold NULL.
       self.env.cr.execute(
           "SELECT DISTINCT store_fname FROM ir_attachment"
           " WHERE store_fname IN %s AND cloud_local_only IS NOT TRUE",
           [tuple(names)],
       )
       return {row[0] for row in self.env.cr.fetchall()}
   ```

4. In `_cloud_filestore_upload_pending`:
   - Walk `names` by index in batches of `self.env.cr.IN_MAX`, so the job
     knows the position where a call stops.
   - Before each pending name: if `uploaded >= limit`, or
     `time.monotonic() - started > UPLOAD_CALL_BUDGET_SECONDS` (then set
     `out_of_time = True`), record the stop index (the index of this name
     in `names`) and stop the walk. A bucket error stops the walk as today
     (`failed = True`), without a stop index.
   - After the walk: `remaining` is the number of pending names in
     `names[stop_index:]` (sum of `len(self._cloud_filestore_pending_names(batch))`
     over batches of `IN_MAX`) when a stop index is set and the job did not
     fail; else 0.
   - Add the remaining count to the summary log line, after "files checked":
     `..., %d files checked, %d left for the next call%s%s`.
   - At the end, after the `_bucket_ok` alert and before `return uploaded`:

     ```python
     if self.env.context.get("cron_id"):
         # Work left makes Odoo call the job again at once; 0 lets the cron wait its interval.
         self.env["ir.cron"]._commit_progress(uploaded + already, remaining=remaining)
     ```

   - Update the docstring: the run stops after the call budget, reports its
     progress to the cron, and `limit` caps the uploads of one call.
   - Everything else (head before put, confirm, local-only twin, refusal,
     outage alert, counters) stays as it is.

Run the module tests: `0 failed, 0 error(s)`. The existing tests
`test_upload_respects_limit` and `test_upload_stops_after_time_budget` must
still pass unchanged.

## Task 2: The progress API in the odoo-development skill

Files: `.claude/skills/odoo-development/references/coding-patterns.md`,
`.claude/skills/odoo-development/SKILL.md`.

Add a section `### A Cron With a Backlog Reports Its Progress (`_commit_progress`)`
in `coding-patterns.md`, directly after "Per-Record Cursor Isolation (Cron
Workers)". Content, all from `odoo/addons/base/models/ir_cron.py` of Odoo 19:

- The problem: a job that handles a fixed batch per run and does not report
  progress is "fully done" after each run and waits its full interval, also
  with a large backlog.
- `_commit_progress(processed=0, *, remaining=None, deactivate=False)`:
  adds `processed` to `done`; without `remaining` it subtracts `processed`
  from the last `remaining`; it commits; it returns the seconds left of the
  run's time window; outside a cron it only commits and returns `inf`.
  `_notify_progress` is the deprecated older form. The counters live in
  `ir.cron.progress`.
- The loop in `_run_job`: 10 calls (`MIN_RUNS_PER_JOB`), and more only while
  the run is under 10 s (`MIN_TIME_PER_JOB`), as long as each call reports
  `remaining > 0`. A short table of outcomes: no error and `remaining = 0`
  → fully done → normal interval; no error, progress, `remaining > 0` → call
  again; 0 done on the first call with `remaining > 0` → partially done with
  the warning "processed no record"; error with committed progress → call
  again; every other error → failed.
- After the loop with work left: partially done → `_reschedule_asap` (a
  trigger for now; the next poll of a cron worker runs it, after the other
  ready jobs). Three timeouts in a row without progress
  (`CONSECUTIVE_TIMEOUT_FOR_FAILURE`) count as failed.
- Rules: (1) `remaining` counts only work that a new call can do — items
  kept back on purpose (refused, skipped) never count, or the cron runs
  without end, and items kept back before the stop point need a resume
  point; (2) a call is short: one cron run is 10 calls, so the
  per-call budget is the run budget divided by `MIN_RUNS_PER_JOB`; (3) on an
  outage report `remaining = 0`, so the cron waits its interval; (4) report
  only inside a cron (`self.env.context.get("cron_id")`), because
  `TransactionCase` forbids `cr.commit()`; a manual run reports nothing and
  the caller commits (`odoo-bin shell` rolls back on exit); in a test, patch `ir.cron._commit_progress` and call the method
  with `with_context(cron_id=…)`.
- A short code example, and "Found in otenydoo `oteny_cloud_filestore`
  (19-Oct-2026): the upload cron moved 500 files per 10 minutes; first push
  8.5 h for 1 h of work."

In `SKILL.md`, add one bullet in the list of coding rules that link to
`coding-patterns.md`, in the style of its neighbours, with a link to the new
section.

## Task 3: The cloud filestore skill

Files: `.claude/skills/oteny-cloud-filestore/SKILL.md`, this plan file.

- SKILL.md, "When the Bucket Is Down", the cron paragraph: the job runs every
  10 minutes; while files are pending it reports its progress, so Odoo calls
  it again at once (10 calls of about 24 s in one cron run, each able to pass
  24 s by one upload, then the next poll) until no pending file is left; 500 files at most per call; it
  stops at the first outage error and then waits the 10 minutes.
- SKILL.md, Known Limits, the "first push is slow" bullet: replace the
  "about 8 hours through the cron" estimate with the cron now working without
  the 10-minute pause (measured: about 161 ms per file from Odoo.sh, plan
  1c), and note that a file that stays local on purpose costs one `head` (or
  one refused put) per sweep (amended: the resume point; it was "per call").
- SKILL.md, Roadmap: the item "Plan 2 candidate: a faster first push" gets
  the note that the cron pause is removed by plan 1d; parallel uploads and the
  confirm-from-put remain candidates.
- SKILL.md, References: add plan 1d.
- This file: Status `BUILT (19-Oct-2026), not committed`, and a short
  "Result" section with the test result of Task 1.

## Amendment: the resume point (final review, 19-Oct-2026)

The final review found an endless loop. Every call walked the sorted names
from the start, so names kept local on purpose before the stop point were
checked again in every call. When they used up the 24 s, every call stopped
at the first new pending name with `remaining > 0` and little or no upload,
and Odoo ran the job again as soon as possible without end.

The fix, in `_cloud_filestore_upload_pending`:

- A module dict `_UPLOAD_RESUME_AFTER`, keyed by `self.env.cr.dbname`, holds
  the last name the walk reached. It lives in process memory, not in a system
  parameter, because each write of a parameter clears the caches of every
  worker.
- A call starts at the first name after that point (`bisect_right` on the
  sorted names), so a name that is gone just moves the start on. A sweep
  visits each local name once, over as many calls and runs as it needs.
- `remaining` counts the pending names of the sweep that no call has reached
  yet: from the stop name to the last name.
- A call that stops (time or `limit`) stores the name before the stop name.
  A call that reaches the last name clears the point and reports 0. After a
  bucket error the point stays as it is and `remaining` is 0.
- The next sweep starts at the first name. It tries the files kept local
  again, and it finds a new file that sorts before the old point. A call does
  not wrap from the last name to the first: the names before the point were
  reached in this sweep already, so a wrap would count them in `remaining`
  again and could bring the endless loop back.
- A lost point (a restart, another worker) only means that the walk starts at
  the first name: extra heads, never a wrong result.
- Test: `test_upload_resumes_after_files_that_stay_local`. The tests clear
  the dict in `setUp`.

## Result

- Task 1, the upload job: red run `3 failed, 0 error(s) of 100 tests` (the
  three new progress tests); green run `0 failed, 0 error(s) of 100 tests`
  on the database cf-test. `test_upload_respects_limit` and
  `test_upload_stops_after_time_budget` pass unchanged.
- Not yet measured on Odoo.sh. The speed of the new cron there is unknown;
  the measured numbers are those of plan 1c (about 161 ms per file, 25,395
  local files, 500 files per 10-minute run before the fix).
- Task 3: the cloud filestore skill describes the new cron (cron paragraph,
  first-push limit, roadmap, references).
- Final review fix: the resume point. Red run `1 failed, 0 error(s) of 101
  tests` (the new test: the second call put the refused file again); green
  run `0 failed, 0 error(s) of 101 tests`. The other upload tests pass
  unchanged.
