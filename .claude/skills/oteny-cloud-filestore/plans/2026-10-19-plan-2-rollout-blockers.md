# Cloud Filestore Plan 2 — The four items of Ries's review

> Status: BUILT (19-Oct-2026), not committed; the CUNEUS40 run of § 4 is
> open. The Radar dev team answered Q1–Q4 of § 7 with the recommended
> answers. The outcome, the rulings taken during the build and the open
> items are in § 10.

## 1. Why this plan exists

Ries reviewed `feat/cloud-filestore` against the design and set four items
in this order: chunked streaming (D18), the DR fill with the bucket copy
(D16, D26, D27), the read cache (D21) and bucket deletes outside the
garbage-collection lock (R13). A check of the review against the code
(19-Oct-2026) confirmed all four and found three gaps: the 404 guard of a
streamed download, the shared folder of the DR fill and the rsync, and a
file name that is used again before its bucket delete. This plan covers all
of them, and the documentation that follows from them.

The rollout condition stays: no write key in the production `odoo.conf`
before parts 2b and 2c are built and the `server_wide_modules` line is
checked on production (start command of an HTTP worker and of the cron
worker).

## 2. Global constraints

- Generic module `oteny_cloud_filestore` in `otenycom/otenydoo`, side clone
  `~/oteny/otenydoo`, branch `feat/cloud-filestore`, Odoo 19. The DR scripts
  are in radar, `riverdeploy/cuneus40/dr-sync/`.
- Plan 1d and the `X-Cloud-Filestore` header are not committed yet, and
  another session edits `ir_attachment.py` and `test_read.py`. Plan 2 code
  starts after that work is committed, so two sessions never edit the same
  file.
- Do not commit unless the Radar dev team asks. Tests run with the test
  command of SKILL.md, "Tests"; the live tests run against `dev/`.
- Odoo is the master of every rule. A job on CUNEUS40 reads the names from
  the restored database; it never lists the bucket (D27).

## 3. Part 2a — Chunked streaming of bucket downloads (D18). First.

**What changes.** `_to_http_stream` no longer reads the whole object for a
file that is only in the bucket. It opens the object (`get_object`, which
returns the headers and an open body) and returns a stream of its own, a
subclass of Odoo's `Stream` with `type="data"`:

- `get_response()` sends the body in chunks of 64 KB from the open S3 body,
  with `Content-Length` from the object. The worker holds one chunk, not the
  file.
- `read()` and the `data` attribute still give all bytes, so the callers
  that need the bytes keep working. `ir.binary` reads them to resize an
  image (`odoo/addons/base/models/ir_binary.py:241,261`).
- The 404 guard moves to the open: when the object cannot be opened, or is
  missing while the attachment has a size, the method raises `NotFound`
  before any header is sent (today `ir_attachment.py:436-439`). When the
  bucket fails during the body, the generator raises, the server breaks the
  connection, and the browser sees fewer bytes than `Content-Length`, so it
  never keeps a short file as complete.
- A `Range` request gets the whole file with status 200. Odoo's
  `send_file` only answers a range when it knows the size of a seekable
  file. The PDF viewer works without ranges. Once the cache exists (2c),
  a file that may enter the cache is sent from local disk, with ranges and
  with `X-Accel-Redirect`.
- The `X-Cloud-Filestore: bucket` header of the other session stays.

**Store change.** `CloudStore` gets `open(name)`, which returns the size and
an iterator of chunks, with the same error mapping as `get`
(`get`, `tools/cloud_store.py:127-136`). `read_timeout=10` then applies per chunk,
not per file.

**Tests.** A fake store that fails in the middle of the body; a fake store
that fails at the open (404, no header); a missing object with a size (404);
a resized `/web/image` of a bucket-only image (the bytes still arrive); a
live test that streams a 30 MB object from `dev/` and checks the SHA-1.
Measure the memory of the worker for a 29 MB download before and after.

## 4. Part 2b — The DR fill and the bucket copy on CUNEUS40, as one job

Agreed: Q1 and Q2.

**What changes on CUNEUS40.** Three folders under the DR sync folder
(recommended answer to Q1):

| Folder | Filled by | Rule |
|---|---|---|
| `odoosh-filestore/` | the nightly rsync from the Odoo.sh backup | `rsync --delete` stays: an exact copy of what Odoo.sh holds (local-only files, files waiting for upload, the `checklist/` markers) |
| `bucket-copy/` | the copy job | every name the restored database uses plus every name in a `checklist/` marker (D27); a name that leaves both lists is removed 30 days later (D23) |
| `filestore/` | rebuilt every night | the union of the two folders above, as hard links (`cp -al`), mounted in the standby container |

Why three folders: the rsync keeps its exact mirror, so its `--delete` is
harmless again; the bucket copy never takes a file the standby wrote itself
(D27); and the hard links cost no second copy on disk. Odoo never changes a
file in place (the name is the hash of the content), so a write of the
standby makes a new file in `filestore/` only. `filestore/` is rebuilt
every night from production's state, as the database is.

**The copy job.** The recommended answer to Q2 is a module command,
`odoo-bin cloud_dump -d <db> --into <folder>` (D9, D26), run in the standby
container after the restore. It reuses the work of the reverse migration
(`_cloud_filestore_download_all`, `ir_attachment.py:755-811`): it skips a
name that is in the folder already, checks the SHA-1 of each download, and
stops at the first bucket error. It adds the `checklist/` names, and it
keeps the D23 dates (first night a name is missing) in a small file in the
folder, because the database is replaced every night. It uses the read key
of the restored database (D27).

**Changes to `dr_sync.py`.**
- The rsync target becomes `odoosh-filestore/`; `--delete` stays there.
- A failed filestore rsync fails the step `backup_download`, and the night
  then skips the restore, so the standby keeps last night's matching pair of
  database and files. Today the rsync only logs a warning
  (`dr_sync.py:124-125`) and the restore runs on old files.
- New steps after `db_restore`: `cloud_copy` (the command above) and
  `filestore_link` (rebuild `filestore/`). A failed copy keeps yesterday's
  `filestore/`.
- `docker-compose.yml:38` mounts `filestore/`.

**Tests.** Module tests for the command with the fake store: the names from
the database and from the markers, the skip of a present file, the SHA-1
check, the D23 wait and removal, a stop at a bucket error. A run on
CUNEUS40 against `dev/` with a restored production copy, then a restart of
the standby with internet off: every file of a sample of 50 opens.

## 5. Part 2c — The read cache (D21)

Agreed: Q3. Before the cache is sized: measure the reads in clone mode
from `test3` (the open step of plan 1c).

**What changes** (D21 as agreed, all of it):

- A table `oteny.cloud.filestore.cache` (file name, size, recent reads,
  last read, "in bucket" mark). A local file with the mark is a cache copy
  and may be evicted.
- **Write-through.** After a confirmed upload the upload job and
  `_file_write` keep the local file and set the mark, instead of removing it
  (today `ir_attachment.py:365-372` and the upload job). A file larger than
  a quarter of the limit is removed as today.
- **Fill on read.** A normal read from the bucket of a file that may enter
  the cache writes it to Odoo's own path and sets the mark. Then 2a sends it
  from local disk, with `X-Accel-Redirect` where Odoo.sh has `x_sendfile`
  on. Batch work (the copy job of 2b, the reverse migration) never fills the
  cache.
- **Read counts.** Each worker counts in memory and writes the counts at
  most once a minute in one short transaction of its own; the count halves
  every day.
- **Eviction.** A cron: when the marked files are above the limit, delete
  the files with the lowest GDSF keep value until 90% of the limit; a
  `head` confirms the object first; no eviction while the bucket cannot be
  reached; a file read in the last 5 minutes stays.
- **Limit.** System parameter `oteny_cloud_filestore.cache_limit_mb`,
  default 100. For the rollout in steps (recommended answer to Q3): start at
  10240 (10 GB, above the 6 GB filestore), so production keeps every file
  local while the bucket fills; then lower it in steps, each after a look at
  the read times.

**Tests.** Write-through keeps and marks; a large file is not kept; a read
fills; a batch read does not; the counts reach the table; eviction order
(the D21 example: the 10 MB PDF goes first); no eviction without a
confirming `head` or during an outage; a cache copy is sent with
`X-Accel-Redirect` when `x_sendfile` is on.

## 6. Part 2d — Bucket deletes outside the garbage-collection lock (R13)

Agreed: Q4. Due before the rollout + about 300 days.

**What changes.** `_gc_file_store_unsafe` no longer calls the bucket. It
removes local files as today and keeps every marker that is past the hold
time (the markers stay the master list, as today). A new cron deletes those
objects in small batches. For each batch it takes Odoo's share lock on
`ir_attachment` in a short transaction of its own, checks each name again
(no row uses it, the marker still exists and is still past the hold time),
deletes the objects, removes the markers, and commits (recommended answer
to Q4). The lock then holds back saves for the time of one batch, not of
the whole run.

Why the check needs the lock: the name is the SHA-1 of the content. When a
user saves the same content again, `_file_write` finds the object with
`head`, puts nothing and removes the local copy
(`ir_attachment.py:340-367`). Without the lock, that save can commit between
the check and the delete, and the delete removes the only copy of a live
file.

**First a fact.** A live test against `dev/` checks whether Google's S3
interface accepts `DeleteObjects`. If it does, a batch is one request of up
to 1,000 names. If it does not, a batch is single deletes, and its size is
set so that one batch holds the lock for at most about 2 seconds (about 40
deletes at about 50 ms).

**Tests.** A name in use again between two runs is not deleted; a name past
the hold time is deleted and its marker removed; a bucket error keeps the
markers and posts the alert; the GC run itself makes no bucket call.

## 7. Questions, answered by the Radar dev team (19-Oct-2026)

**Q1 — Folders on CUNEUS40.** *Situation:* the standby uses the folder that
the nightly rsync fills, and the rsync removes every file that Odoo.sh no
longer holds. After the rollout Odoo.sh holds few files, so the standby
would lose them. D27 also says the standby must not write its own files
into the bucket copy. *Question:* how do the rsync, the bucket copy and the
standby share the disk? *Recommended:* three folders, with the standby's
folder rebuilt every night as hard links of the other two (§ 4). Each folder
has one owner and one rule, and no file is stored twice.
*Answer:* three folders, as recommended.

**Q2 — Where the copy job lives.** *Situation:* the job needs the names
from the restored database, the read key, the SHA-1 check and the D23
dates. *Question:* a command of the module, or a script in
`riverdeploy/cuneus40`? *Recommended:* the module command
`cloud_dump --into` (D9, D26). It reuses the reverse migration, other users
of the module get it too, and the CUNEUS40 script only calls it.
*Answer:* the module command, as recommended.

**Q3 — How much of the cache, and the first limit.** *Situation:* with a
first limit of 10 GB, no file is evicted during the first step, so the
eviction rules (GDSF) only work when the limit is lowered. *Question:* build
all of D21 now, or first write-through, fill on read and the limit, and the
eviction later? *Recommended:* all of D21 now, because Ries made the cache a
blocker of the rollout and the limit must be lowered soon after it; a first
limit of 10 GB.
*Answer:* all of D21 now, first limit 10 GB, as recommended.

**Q4 — The name used again before its delete.** *Situation:* see § 6.
*Question:* how does the delete job stay safe without the long lock?
*Recommended:* a short share lock per batch, with the check of each name
inside it.
*Answer:* a short share lock per batch. Odoo's own cleanup
(`_gc_file_store`, `odoo/addons/base/models/ir_attachment.py:191-221`) uses
the same protection: it sets `lock_timeout` to 10 s, takes
`LOCK ir_attachment IN SHARE MODE`, checks every name against
`store_fname` under the lock, and skips the run when it cannot get the lock.
Inside Odoo's lock the work is local file removals of microseconds; the
module put network deletes of about 50 ms each in the same lock. The batch
cron keeps Odoo's lock, its 10 s `lock_timeout` and its check, and holds the
lock for one batch at a time.

## 8. Documentation in the same build

- SKILL.md, "Rollout Condition": replace "one of these two conditions" with
  the condition of § 1, so it matches Ries's review.
- SKILL.md, "Known Limits": remove the limits that plan 2 removes.
- `cuneus-infrastructure` (radar), `references/disaster-recovery.md`: the
  three folders and the new night steps.
- design-decisions.md: D16, D26 and D27 point to this plan for the folder
  layout.

## 9. Done means

All tests tagged `oteny_cloud_filestore` pass (`0 failed, 0 error(s)`),
including the live tests against `dev/`; the CUNEUS40 run of § 4 opens
every sampled file with internet off; the reads in clone mode from `test3`
are measured and written into `lab-results.md`.

## 10. Outcome of the build (19-Oct-2026)

Built with one implementer and one review per part, then one review of the
whole change and one fix wave. Nothing is committed. The whole
`oteny_cloud_filestore` tag passes with the live tests against `dev/`:
`0 failed, 0 error(s) of 182 tests`.

**What was built**
- 2a: `CloudStore.open()` and `CloudObjectStream`. A 29 MB object streams
  with a peak of 0.15 MB (tracemalloc) instead of 44 MB with `get`. A HEAD
  request and a 304 close the object without reading the body.
- 2b: `cli/cloud_dump.py` and `models/cloud_copy.py` (`--into`,
  `--checklist`, `--seed`, the state file `.cloud_copy_state.json`, exit
  codes 0/1/2/3/4); radar `dr_sync.py`, `docker-compose.yml` and
  `odoo-entrypoint.sh` with the three folders of § 4.
- 2c: model `oteny.cloud.filestore.cache` (`models/cache.py`), parameter
  `cache_limit_mb` (default 100), hourly cron "Cloud filestore: evict old
  cache copies".
- 2d: `CloudStore.delete_many()`, daily cron "Cloud filestore: delete
  expired objects from the bucket"; the garbage collection makes no bucket
  call.

**Facts found during the build**
- Google's S3 interface accepts `DeleteObjects` (probe against `dev/`: 200,
  both objects gone).
- `odoo-bin` finds a command that lives in an addon only when
  `--addons-path=` is the first argument and the command the second
  (`odoo/cli/command.py`, `main()`).
- The standby's addons path did not list `/opt/src/user/otenydoo`, the
  folder of the generic modules since they left radar; fixed in
  `odoo-entrypoint.sh` and in the copy step.
- A read cache write in its own cursor can wait on a row lock that the same
  request holds; every such write now uses a 1 s `lock_timeout`.

**Rulings taken during the build** (the controller's, for the Radar dev
team to check)
- Plan code was built on top of another session's staged change of
  `ir_attachment.py` and `test_read.py` (the `X-Cloud-Filestore` header),
  instead of waiting for its commit. The plan's edits are unstaged; stage
  by hunk when committing.
- After a failed copy the link step still rebuilds `filestore/` (§ 4 said
  keep yesterday's): the bucket copy only grows from night to night.
- Odoo on the standby stays stopped while the copy and the link run;
  `odoo_start` runs every night, also after a failed download.
- A cache row is marked only after a confirmed upload or a verified
  download; an unconfirmed save and the reverse migration clear marks; the
  delete job deletes the row of a deleted object; a name shared with a
  local-only file is never marked.
- A limit of 0 turns the cache off, and the next eviction removes the
  cache copies after a `head` check.
- The code default of `cache_limit_mb` stays 100 (D21); the rollout sets
  10240 (Rollout Condition in SKILL.md).
- The race of a save during a delete batch is kept and documented (Known
  Limits): Odoo's own cleanup has the same window.

**Open**
- N1 (fixed after the build, 19-Oct-2026): `cloud_dump` exits 2 only for
  "module not installed" and "mode off"; a mode that is on without a usable
  store is exit 4, which the DR script logs as a failed step. The texts that
  said rsync runs as `bot` now say root (`cron.d/dr-sync`).
- The § 4 run on CUNEUS40 (the laptop could not reach it).
- Read times in clone mode from `test3`: measured by the plan 1c session
  (about 243 ms per file); write them into `lab-results.md` before the limit
  is lowered.

## 11. Ries's review, as it stood in the roadmap before the build

Moved here from SKILL.md after the build, unchanged below its heading.


Ries set the order after the review of `feat/cloud-filestore` (otenydoo tip
`4b19cbf`) against [design-decisions.md](../references/design-decisions.md)
(19-Oct-2026). The rollout condition does not change: no write key in the
production `odoo.conf` before items 2 and 3 are built and the
`server_wide_modules` line is in place. Test3 runs one process, so it does
not prove that the worker processes of production read the line.
The build plan, with the answers of the Radar dev team:
this plan.

1. [ ] **Chunked streaming of bucket downloads (D18). First.**
   `_to_http_stream` reads the whole object into memory and returns
   `Stream(type="data")` (`ir_attachment.py:435-449` at `4b19cbf`), so a
   large file can crash a worker. Because the stream is `type="data"`,
   nginx's `X-Accel-Redirect` is not used either. The data in `crmain`: the
   largest stored file is 29 MB, 3 files are larger than 25 MB, and 62 are
   larger than 10 MB. All of them are PDFs, on credentials, employees and
   services.
   - *Keep the 404 guard.* Today the bytes are read before the response
     starts, so an empty read of a file with a size becomes a 404
     (`ir_attachment.py:435-439` at `4b19cbf`). That stops a browser from
     caching an empty 200 under the real etag. A chunked stream sends the
     headers before the body is read, so a bucket error in the middle can no
     longer become a 404. The chunked version must open the object first
     and answer 404 when that fails, before it sends a header. When the
     bucket fails during the body, it must break the connection, so the
     browser never keeps a short file as complete.
   - Chunks do not bring back `X-Accel-Redirect`: nginx can only send a
     local file, and a file that is only in the bucket has no local path.
     nginx sends a file again once it is a cache copy (D21).
2. [ ] **The DR fill on CUNEUS40 (D26) and the bucket copy (D16, D27), built
   as one job. Blocks the rollout.** Both read the names from the restored
   database and the `checklist/` markers, and then download each file that
   is missing, so one job does both. In the same build, fix the filestore
   rsync of the standby: `riverdeploy/cuneus40/dr-sync/dr_sync.py:119`
   (radar) syncs the Odoo.sh filestore with `rsync -az --delete`. In
   production mode the upload job removes each local file after the bucket
   confirms it. So the first night after production starts to upload, the
   standby deletes its copies of those files. From then on the standby
   depends on the bucket and on internet, which goes against D15.
   - *The fill and the rsync write the same folder.* The standby uses the
     rsync target as its filestore:
     `riverdeploy/cuneus40/dr-sync/docker-compose.yml:38` (radar) mounts
     `./backup/filestore` at `/var/lib/odoo/filestore/cuneus`. Without
     `--delete`, the rsync keeps every file forever, so the removals must
     come from the job, with the 30-day wait of D23. D27 leaves open
     whether the bucket copy (D16) and the filestore of the standby are one
     folder or two; the standby must not write its own new files into the
     backup copy. Interview question for plan 2: one folder or two.
   - A failed filestore rsync only logs a warning, and the night goes on to
     restore the database (`dr_sync.py:124-125`). The standby then runs on
     a new database with old files, and nobody is told.
3. [ ] **The read cache (D21). Blocks the rollout.** Without it, every read
   of a file that is only in the bucket holds a worker for about 50 ms
   (10–200 KB) to 140 ms (1 MB) in the lab. Thumbnails go to the bucket
   under D25, so a list or a chatter with many images pays that once per
   image. The cache size limit is the tool for a rollout in steps: start
   with a limit above the whole filestore, so production keeps every file
   local while the bucket fills and clones already read from it; then lower
   the limit in steps. Before the cache is sized, measure the reads in clone
   mode from `test3` (the open step of
   [plan 1c](2026-10-19-plan-1c-odoosh-check.md)).
4. [ ] **Bucket deletes outside the garbage-collection lock of Odoo (R13).
   Due before the rollout + about 300 days.** `_gc_file_store_unsafe` calls
   `store.delete` one file at a time while Odoo holds the share lock on
   `ir_attachment` (`ir_attachment.py:513-543` at `4b19cbf`). That stops
   every attachment write for the time of the run. Collect the names that
   are past the hold time inside the run, and delete them in a separate job
   after the commit. Not verified: whether the S3 interface of Google Cloud
   Storage accepts the multi-object delete (`DeleteObjects`).
   - *A name can be used again before the delete.* The name of an object is
     the SHA-1 of its content. When a user saves the same content again,
     Odoo's `_file_write` writes the local file and a fresh `checklist/`
     marker, and the module's `head` finds the object in the bucket, so it
     puts nothing and removes the local copy (`ir_attachment.py:340-367` at
     `4b19cbf`). The object in the bucket is then the only copy of a live
     file again. Today the share lock makes this safe: no save can run
     between the check of a name and its delete. A separate job loses that
     protection. It must check each name again just before the delete (no
     `ir_attachment` row uses it, and its marker is still past the hold
     time), and no save may use the name between that check and the
     delete. How to close that gap is an interview question for plan 2.
