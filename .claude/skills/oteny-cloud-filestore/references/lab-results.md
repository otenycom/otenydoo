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

## Lab setup on the developer laptop

The Odoo config files of the lab hold the write key, so they are kept with
mode 0600 in `~/.config/oteny/cf-lab/` (not in `/private/tmp`, which a
reboot clears): `cf-demo.conf` (port 8072, database `cf-demo`, prefix
`demo/`) and `cr-cloud.conf` (port 8073, database `cr-cloud`, prefix
`cr-cloud/`, with the radar addons). A copy of a neutralized restore must
lose its `database.is_neutralized` flag, or the module stays in clone mode
and uploads nothing.
