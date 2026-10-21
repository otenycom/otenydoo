# Lab results: latency and the first push

Measured on 12-Oct-2026 on a developer laptop against the homelab Garage store
([test-store.md](test-store.md)), over the tailnet. Production runs from
Odoo.sh against its own bucket, with another round trip and another
bandwidth. Use these numbers for the shape of the cost, and measure once from
an Odoo.sh branch against the production bucket before the rollout.

## Latency per operation

Database `cf-demo` in production mode. Median / p95 in ms, 15 samples per
cell. "Local" is a file that stays local (written with `install_mode`).

| Size | S3 put | S3 head | S3 get | Save + upload | Save local | Read bucket | Read local | HTTP bucket | HTTP local |
|---|---|---|---|---|---|---|---|---|---|
| 10 KB | 54 / 58 | 26 / 28 | 49 / 57 | 54 / 59 | 1.1 / 1.3 | 49 / 53 | 0.6 / 0.9 | 63 / 161 | 8 / 20 |
| 200 KB | 67 / 74 | 27 / 30 | 55 / 81 | 65 / 70 | 1.1 / 1.3 | 51 / 56 | 0.3 / 0.4 | 57 / 62 | 6 / 11 |
| 1 MB | 122 / 264 | 27 / 29 | 160 / 196 | 124 / 131 | 3.5 / 5.1 | 138 / 145 | 0.5 / 0.6 | 123 / 137 | 9 / 10 |
| 5 MB | 398 / 441 | 27 / 30 | 492 / 621 | 408 / 435 | 4.8 / 7.6 | 417 / 525 | 1.8 / 3.9 | 513 / 637 | 12 / 19 |

What the numbers show:

- Every bucket call has a fixed cost of about 25 to 50 ms, whatever the size.
  Above about 1 MB the transfer counts most (about 10 to 12 MB/s on this
  connection).
- A save of a 200 KB file costs about 65 ms instead of about 1 ms. A photo
  save writes the original, four smaller copies and a thumbnail, so about 6
  uploads.
- A read from the bucket is more than 100 times slower than a local read. A
  list or a chatter with many images pays this once per image, and each read
  holds an Odoo worker. This is the measured reason for the read cache of
  plan 2 (D21).
- The p95 of 161 ms for the first 10 KB download is the start of the S3
  client, once per process.

On real data (database `cr-cloud`, a production copy), single reads took 25
to 60 ms for files up to about 80 KB, and 193 ms for a 778 KB credential PDF.
Odoo reads `raw` for a whole batch of records at once (prefetch), so one
read of a recordset can fetch several objects in one go.

## The first push of a production copy

Database `cr-cloud`, a copy of a production restore with 24,720 local files
(5.5 GB). The push is the normal upload job
(`_cloud_filestore_upload_pending`), called in a loop from `odoo-bin shell`.

- 24,207 user files (5.66 GB) went to the bucket; 439 asset bundles and
  module images (about 20 MB) stayed local, as designed.
- Speed: 9.6 files/s, 2.3 MB/s, about 104 ms per file. A batch of 500 files
  took 52 s (at most 55 s), so the time budget of 240 s per run never
  stopped a batch.
- One run for all files: about 42 minutes. Left to the cron (500 files every
  10 minutes): about 8 hours.
- A laptop reboot stopped the first run after about 10,700 files. The next
  run went on from there without loss: a file leaves the local disk only
  after the bucket confirms it.
- A check of 50 random objects (size and SHA-1 against the name) found no
  error.
- About 87 MB of old files that no attachment uses stayed on disk. The job
  skips them, and Odoo's cleanup does not see them either, because they have
  no cleanup marker.

The fixed cost per file limits the speed, not the bandwidth: an upload (about
55 to 65 ms), a size check (about 27 ms), the local delete and a database
query, one file after another.

## The first push to Google Cloud Storage (13-Oct-2026)

The same production copy `cr-cloud` (24,207 user files, 5.66 GB) was pushed
again, now to Google Cloud Storage `cuneus-crewradar-docs`, prefix
`dev/cr-cloud/` (EU multi-region), from the developer laptop over the public
internet, with the code of plan 1b and the module loaded server-wide. The
local files came back from the `crmain` filestore first, because Garage held
the only copy and was off; one generated thumbnail existed only in Garage.

- 24,207 files, 5.66 GB in 91.8 minutes: 4.4 files/s, about 1.0 MB/s, about
  230 ms per file. A batch of 500 files took 113 s (at most 123 s).
- A check of 50 random objects (size and SHA-1 against the name) found no
  error.
- Five user files stayed local: each shares its name with a local-only file
  (partner images equal to menu icons or the company logo). They are in the
  bucket, and their local copy stays (D24).

Half the speed of the Garage push, for two reasons: each request crosses the
public internet to Google's EU region instead of the tailnet, and each new
file now costs three requests (a `head` before the upload, new in plan 1b,
the upload, and the confirming `head`). For a first push the first `head`
always answers "not there". A plan 2 candidate: skip it during a first push,
or upload several files at the same time. A push from Odoo.sh, close to
Google, is expected to be faster; it is not measured yet.

## Odoo's own attachment tests

On 13-Oct-2026, 61 standard Odoo tests that work with attachments ran against
Google Cloud Storage (`dev/odoo-base-tests/`). The classes are
`TestIrAttachment`, `TestPermissions`, `TestMergePartner`, `TestImage`,
`TestLogsAssetsGenerateTime`, `TestPregenerateTime`, `TestAssetsGenerateTime`,
`TestLoad`, `TestWebAssetsCursors`, `TestDiscussAttachmentController`,
`TestMessageController`, `TestMessageLinks` and `TestAvatarAcl`.

- Plain Odoo (module not installed, `-i base,web,mail`): 61 of 61 pass.
- Module installed, mode off: 61 of 61 pass.
- Production mode before plan 1b (`-u base,web,mail`): 59 of 61 pass.
  `test_field_read_permission` failed (a local delete of a name that
  another attachment shared, and the load order of the modules).
  `test_05_web_image_access_token` failed (frozen clock). The log held
  `429 SlowDown` warnings.
- Production mode after plan 1b, with `--load=base,web,oteny_cloud_filestore`
  (`-u base,web,mail`): 60 of 61 pass. Only `test_05_web_image_access_token`
  fails: it freezes the clock, so the S3 signature is refused. The log has
  no `429`.

Note: Odoo's at_install tests, started without `-i` or `-u`, give order
errors (`null value in column "notification_type"`). They do not come from
this module. Compare only runs that were started in the same way.

## Lab setup on the developer laptop

The Odoo config files of the lab hold the write key, so they are kept with
mode 0600 in `~/.config/oteny/cf-lab/` (not in `/private/tmp`, which a
reboot clears): `cf-demo.conf` (port 8072, database `cf-demo`, prefix
`demo/`) and `cr-cloud.conf` (port 8073, database `cr-cloud`, prefix
`cr-cloud/`, with the radar addons). A copy of a neutralized restore must
lose its `database.is_neutralized` flag, or the module stays in clone mode
and uploads nothing.

## Plan 2 facts (19-Oct-2026)

- **`DeleteObjects` on Google Cloud Storage.** A `delete_objects` request
  with two keys under `dev/probe-<id>/`, `Quiet: True`, with the dev write
  key: answer 200, no `Errors`, and a `head` of both keys found nothing. The
  delete job of plan 2 therefore deletes up to 1,000 objects per request.
- **Memory of a streamed download.** A 29 MB object, measured with
  `tracemalloc` on the developer laptop: peak 0.15 MB through
  `CloudStore.open().iter_chunks()` (64 KB chunks), 44.07 MB through
  `CloudStore.get()`.
- **A restore of the test3 backup on the developer laptop (19-Oct-2026).**
  Backup `cuneus-test3-40107605_2026-10-19_142934_exact_fs.zip`, taken after
  the full push of plan 1c: zip 591 MB, `dump.sql` 3.1 GB, `filestore/` 518
  files, 106 MB (510 local-only files 91 MB, 2 user files that share their
  name with a local-only file, 6 files no row uses 14 MB), 24,883 user files
  (5.4 GB) only in the bucket. Restore on `crmain` with the radar restore
  script, full (not `--lite`): audit filter 39 s, restore and module upgrade
  67 s, total 106 s; `-u oteny_cloud_filestore` 13 s. In clone mode against
  `dev/odoosh-test3/`, 20 single reads of files of 4 KB to 500 KB: median
  155 ms from the bucket (87 to 303 ms), median 0.6 ms from the cache copy
  that the first read left.
- **Prefetch reads a whole batch from the bucket.** Reading `raw` of one
  attachment out of a `search()` of 400 records computes `raw` for the whole
  prefetch set: about 190 bucket-only files came from the bucket in one
  access, in 29 s. Code that reads `raw` in a loop over a large recordset
  pays every file at once; `browse(id)` of a single record does not.
