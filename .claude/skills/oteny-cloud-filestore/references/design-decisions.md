# Cloud filestore — design decisions (the spec)

> **Status: IN DESIGN (9-Oct-2026).** Interview with Thijs finished: D1–D20
> agreed (8-Oct-2026). On 9-Oct-2026 Ries added a local read cache (D21),
> answered Q16–Q19, and replaced the signed links of D18 with streaming by
> Odoo, which also answers Q20, and answered Q21 (production is the server
> whose `odoo.conf` holds the write key). The full review of 12-Oct-2026
> (Thijs) fixed sentences that later decisions had overtaken and opened
> Q22–Q27, all answered by D22–D27 ([§ 4](#4-questions)). The
> decisions are a draft spec: a new decision revises an older one where the
> whole design gets better (Ries, 9-Oct-2026). Next: design review, then a
> written spec in an otenydoo skill (D12). No code exists yet. The
> problem this answers is described in radar's
> `rivercreds-docs/references/filestore-cloning.md` (business repository).
> Moved here from radar's `rivercreds-docs/plans/cloud-filestore.md` on
> 12-Oct-2026 (Thijs): all documentation of this module lives in otenydoo.

## 1. Business reason

The NAS import is done. The production filestore of `main` is about 6 GB
(Thijs, 8-Oct-2026), not the 50 GB that
radar's `filestore-cloning.md` projected before
the import. Every clone of production (Odoo.sh staging, `crmain`, a laptop,
the DR standby) today needs a full copy of that filestore, and every backup
carries it. The module puts the file contents in a cloud object store (an
S3-type bucket, for example Hetzner Object Storage) so that:

- production no longer keeps the files on Odoo.sh, so its storage and its
  backups shrink;
- a clone can open every file without a full filestore copy;
- the files live off Odoo.sh, in a bucket that we control.

Odoo's own `cloud_storage` module does not fit: it only moves large browser
uploads, skips most of our models, and supports only Google and Azure (see
radar's `filestore-cloning.md`, "Why NOT the Odoo cloud_storage Module").

## 2. Agreed decisions

**D1 — On production the cloud replaces the local filestore.** Every file
operation that Odoo does on its filestore (write, read, delete) is done on the
cloud instead. After the migration the local filestore of production is empty
and the cloud holds every file. That is the purpose of the move: production
and its Odoo.sh backups no longer carry the files (Thijs, 8-Oct-2026; an
earlier reading of "mirror" as "keep a full local copy" was wrong).
Amended by D21: "empty" means no files beyond the `checklist/` folder, the
files waiting for upload (D14) and the read cache (at most its limit).

**D2 — Migration both ways, at runtime, in batches.** A batch operation moves
existing local files to the cloud (many files per run, resumable, while users
keep working). The reverse batch operation downloads or streams cloud files
back to the local filestore. It clears every "in bucket" mark (D21), so the
cache never evicts a file that came back, as `cloud_dump --with-cloud-files`
does (D9).

**D3 — Only the server with the write key in its `odoo.conf` writes and
deletes (revised by Q21).** A copy that is not neutralized is also a clone
(Thijs, 9-Oct-2026), and the delete risk of such a copy is too high (Ries,
9-Oct-2026). So the write key does not live in the database at all: it lives
in the `odoo.conf` of the production server, as a module option that Odoo
returns with `odoo.tools.config.get(...)` (Ries, 9-Oct-2026). A database
copy never carries it, however it is made: Odoo.sh staging, riverdeploy
clones, the DR standby, a backup loaded by hand without neutralizing it.
- *The rule:* Odoo acts as production only when its `odoo.conf` holds the
  write key and the database is not neutralized
  (`database.is_neutralized`). Every other database is a clone (D4). The
  second condition guards a server whose `odoo.conf` holds the key but
  whose database is a neutralized copy.
- *Why the server and not the database:* Odoo.sh can move a database to
  another PostgreSQL server without notice, and every restore or Odoo
  upgrade loads a dump into a new database. Every identifier of the
  database itself changes then (§ 3), and production would stop being
  production at random moments. The `odoo.conf` belongs to the Odoo.sh
  branch and stays when the database changes.
- *Setup:* an admin adds the write key once to the `odoo.conf` of the
  production branch on Odoo.sh (`/home/odoo/.config/odoo/odoo.conf`, § 3)
  and restarts Odoo.
  On a failover, the DR standby gets the key in its own `odoo.conf`.
- *Not used:* the database uuid (a `pg_restore` or `psql` load keeps it),
  a token in the PostgreSQL database comment (Odoo's database role on
  Odoo.sh is not the owner and may not set it), and the pair of server
  identifier and database OID (it changes when Odoo.sh moves the database).
  The measurements are in § 3.

**D4 — Copy on write in a clone.** A clone reads a file from its local
filestore first and from the cloud second. Every file that the clone creates
goes to its regular local filestore. A clone never writes to or deletes from
the cloud.

**D5 — Backup option for cloud files.** The backup gets an extra argument.
With it, the backup stores the cloud files exactly as if they were local
files, so a restore of that backup has only local files and needs no cloud.
Without it, the backup leaves out the files that are in the cloud, which keeps
backups small.

**D6 — The cloud keeps a file as long as the longest backup can need it.**
When production removes a file, the cloud object is not deleted at once. It
gets a "to be deleted after" date: the removal date plus a hold time. The hold
time equals the retention of the longest-kept backup and is a setting in Odoo
(a system parameter). Only after that date is the object deleted. Reason: a
clone restored from any backup that still exists then always finds its files.

**D7 — Odoo's checklist stays local; the file contents go to the cloud.** On
production the local filestore keeps Odoo's own `checklist/` folder of
empty marker files; every file content lives in the cloud (since D21 the
local filestore also holds the files waiting for upload and the read cache). The daily cleanup
keeps reading that checklist to find names that no record uses any more. The
checklist is part of every backup (see § 3).

**D8 — The checklist marker carries the delete date (answers Q1).** No new
table and no object tag. The marker files in Odoo's `checklist/` folder are
the master list of pending cloud deletes. The daily cleanup gets one extra
condition: on production it deletes the cloud object of an unused name only
when the marker is older than the hold time, and it leaves a younger marker in
place for the next run. The hold time is a system parameter, default 365
days (Thijs, 8-Oct-2026), and never shorter than the oldest Odoo.sh backup
(Q2). Production updates the marker's modification time
(`os.utime`) every time it marks a name, so the hold time always counts from
the last delete, also when a name was reused and removed again. The cleanup
uses the modification time, not the creation date, because Linux does not
reliably report a file's creation date. The existing check against
`ir_attachment.store_fname` still runs first, so a reused name keeps its
object. Failure directions are safe: a restore sets every marker to the
restore date (`zipfile.extractall` does not restore modification times), and
a lost checklist means no cloud delete at all. Both only delay a delete; the
cost is orphan objects, never a missing file.

**D9 — The backup is a module command: `odoo-bin cloud_dump` (answers Q3).**
Odoo.sh makes its own backups and takes no extra argument, so the module adds
its own command (Thijs, 8-Oct-2026): `odoo-bin cloud_dump -d <db>
[--with-cloud-files]`. Odoo only accepts letters, digits and `_` in a command
name (`COMMAND_NAME_RE` in `odoo/cli/command.py`), so the name is
`cloud_dump`, not `cloud-dump`. It writes the same zip format as Odoo's own
dump, so a normal restore reads it. Without the argument the zip holds the
database plus the local filestore as it is, as Odoo's own dump does: the
files that exist only on local disk (for example a file that a clone
created) and the cache copies (D21, at most the cache limit). With
`--with-cloud-files` it also downloads every file that the database uses
from the bucket into `filestore/`, so the restore is fully local and needs
no bucket. That dump clears every "in bucket" mark (D21), so the restored
database keeps every file and never evicts one. riverdeploy calls it for a
full off-site copy.

**D10 — A normal clone keeps no full copy; it reads through the cache
(answers Q4, revised by Q18).** A clone does not build a local copy of the
files that it reads from the bucket (Thijs, 8-Oct-2026). Like production, it
keeps recently and often read files in the read cache with its size limit
(D21; Ries, 9-Oct-2026); every other read goes to the bucket. The clone's
local filestore holds only the files that the clone created itself (D4). A
clone that must work without
internet is restored from a `cloud_dump --with-cloud-files` backup (D9)
instead. This applies to normal clones (staging, `crmain`, laptops); a high
availability setup such as the DR standby follows D15. Accepted
consequence: a clone older than the hold time cannot open files that
production has deleted from the bucket since.

**D11 — S3 protocol; read key in a system parameter, write key in
`odoo.conf` (answers Q5, revised by Q21).** The module
speaks the S3 protocol, which nearly every object store offers (Hetzner, AWS,
Backblaze, Wasabi, Cloudflare R2, MinIO), so it is not tied to one provider.
The read key lives in a system parameter (Thijs, 8-Oct-2026), so it travels
with every database copy. The write key lives in the `odoo.conf` of the
production server and never in the database (D3, Ries, 9-Oct-2026). Hetzner facts
([S3 credentials FAQ](https://docs.hetzner.com/storage/object-storage/faq/s3-credentials/)):
a key pair is valid by default for every bucket in the same Hetzner project,
with read and write rights; a bucket policy can grant a single key access to
one bucket and limit it to specific actions (for read only: `s3:GetObject`,
plus `s3:GetObjectVersion` when versioning is on).

**D12 — The module is a generic otenydoo module (answers Q6).** It holds
nothing specific to Cuneus or crew (Thijs, 8-Oct-2026). It is built in
`~/oteny/otenydoo` on a `feat/` branch, never in the radar `otenydoo/` pin.
The technical design moves to an otenydoo skill when the spec is written;
this file keeps the Cuneus decisions (hold time, rollout, DR).

**D13 — Production uploads during the transaction (answers Q7).** The module
keeps Odoo's own order (Thijs, 8-Oct-2026): the `ir_attachment` row is
inserted or flushed first, then `_file_write` adds the checklist marker, then
the module writes the file to local disk and uploads it to the bucket, then
the transaction commits. The local file stays as a cache copy with the "in
bucket" mark (write-through, D21). A rollback leaves an orphan object whose marker deletes
it after the hold time (D8). The user waits for the upload; credential PDFs
are under 1 MB. If the bucket cannot be reached the save does not fail; the
file goes to local disk instead (D14). Odoo's
"skip the write when the file exists" is a local disk check today; in the
bucket the module either asks first (`HEAD`) or uploads again, which is
harmless because a name always has the same content (spec detail).

**D14 — When the bucket cannot be reached, production falls back to local
disk and alerts the admins loudly (answers Q10).** (Thijs, 8-Oct-2026: "go to
local, send an email to an admin user, make it visible, scream for it; this is
important for all parts of Cuneus".)
- *Writes:* a failed upload never fails the user's save. The module writes the
  file to the local filestore, exactly as standard Odoo does.
- *Reads:* production reads local disk first, then the bucket, as a clone
  does (D4). A file that exists only in the bucket cannot be read while the
  bucket is down; that failure also raises the alert.
- *Catching up:* a file in the local filestore of production (outside
  `checklist/`) without the "in bucket" mark of D21 is "waiting for upload".
  A frequent cron uploads those files and sets the mark only after the
  bucket confirms the upload. The local copy then stays as a cache copy, and
  the cache rules of D21 decide when it goes. While files wait locally, they
  are also in the Odoo.sh backups.
- *Alert:* at the first failure the admins get one message, not one per
  file, and a second message when the bucket works again. How the alert reaches the admins is D19.

**D15 — The DR standby keeps a full local copy of all files (answers Q8).**
How it fills that copy is revised by D26.
The standby on CUNEUS40 is a high-availability setup, so D10 does not apply
to it. Every night it restores from `cloud_dump --with-cloud-files` (D9)
instead of the plain Odoo.sh backup, and downloads only the files that are
new since the last night. It then works when Odoo.sh fails, when the bucket
fails, and without internet; it also holds a second copy of every file on
hardware Cuneus owns (input for Q11). Thijs agreed after the traffic check
(8-Oct-2026): Hetzner bills egress per TB per month, and the measured growth
(§ 3) means the nightly copy moves a few hundred MB per month, far inside the
included 1 TB. The standby is reference-only for end users today
(radar's `cuneus-infrastructure` skill), so
a failover writes few new files; how they return to the bucket after a
failover follows the reverse migration (D2) and is a spec detail. The
standby's own `odoo.conf` holds no write key, so it stays a clone, even
though its database is not neutralized (D3, § 3). On a failover an admin
adds the write key to its `odoo.conf`.

**D16 — The bucket backup is a simple copy script; CUNEUS40 is the backup
(answers Q11).** Hetzner keeps a bucket in one Ceph cluster in one location:
that survives a broken disk, but its docs describe no backup and no second
location, and a delete through our own key is carried out like any request.
So the second copy is ours (Thijs, 8-Oct-2026): a simple script that copies
every object from the bucket into a local folder. (How the script finds the
names without listing the bucket: D27.) That folder can then be
backed up by any tool. For now the script runs on CUNEUS40, and CUNEUS40
serves as the backup. Revised by D22: the script also removes its copy of an object that is no
longer in the bucket, so a file deleted in Odoo disappears from the backup
too (first draft: add-only, never remove). The DR standby
(D15) can read its files from the same folder instead of downloading them a
second time.

**D17 — Two keys: a write key for production, a read-only key for clones
(answers Q13).** (Thijs, 8-Oct-2026.) The write key is used only by
production and lives only in the `odoo.conf` of the production server (D3),
so no database copy carries it. The read key is limited by a Hetzner bucket policy to `s3:GetObject` on
this one bucket (plus `s3:GetObjectVersion` if versioning is on) and stays in
every clone. The bucket gets its own Hetzner project, because a Hetzner key is
valid for every bucket in its project by default; the write key then reaches
no other bucket. A leaked laptop database exposes read access, as a leaked
full backup does today, but never delete rights.

**D18 — Odoo streams every file; the bucket is invisible to clients
(answers Q12).** (Ries, 9-Oct-2026. This replaces the signed links that Thijs
chose on 8-Oct-2026 for speed.) A browser, the portal or any other client
never gets a bucket address or a bucket link. Reasons: security (no link to
the bucket leaves the server, and nobody can pass one on) and the browser's
cross-origin rules (no CORS rule on the bucket; every file comes from the
Odoo address, so the PDF viewer and the addresses inside Odoo's CSS bundles
work as today, Q20). Odoo runs its normal access check and then sends the
file from the first source that has it:
1. the file in the local filestore, at Odoo's own path: a clone's own file
   (D4), a file waiting for upload (D14) or a cache copy (D21);
2. the object in the bucket. A file that may enter the cache (D21, Q16) is
   first downloaded into the local filestore as a cache copy and then sent
   from there, so every file that reaches a browser is sent from local disk
   in the same way. A file that may not enter the cache (larger than a
   quarter of the limit) is passed on to the client in chunks while it
   downloads, so the worker never holds the whole file in memory.
Source 1 is Odoo's own filestore: with `x_sendfile` on, Odoo answers with
`X-Accel-Redirect` and nginx sends the file, so the worker is free at once
(§ 3; to check on Odoo.sh). The same order serves the server-side reads
(reports, Wilma, mail, the search index, resized `/web/image`).
*Cost:* every read from the bucket holds an Odoo worker for the time of the
download, and a slow bucket can hold several workers at once and slow down
Odoo for everyone. Limits: a short timeout to connect to the bucket and to
wait for data, the alert of D14 and D19 when the bucket fails, and the read
cache, which takes the repeated reads off the bucket. The traffic cost is the
same as with signed links.

**D19 — The alert is a message in a Discuss channel (answers Q14).** (Thijs,
8-Oct-2026: "we should know about it; a shared Discuss channel is enough, or
a health check URL; just something to get an email or message out to us".)
No banner and no separate alert framework. When the bucket cannot be reached,
the module posts one message to a Discuss channel that the admins follow, and
a second message when the bucket works again; one message per outage, not one
per file. Members get the channel's normal Odoo notification, by email or in
Odoo, as each person has set it. The module is generic (D12), so the channel
is a setting, not a fixed name (spec detail). A health check URL (an address
that answers "OK" or "error", for an outside monitor to poll) was named as an
equal option and stays possible later; it would also catch the case where
Odoo itself is down, which a Discuss message cannot report.

**D20 — No Object Lock on the bucket (answers Q15).** (Thijs, 8-Oct-2026.)
Hetzner's Object Lock would block every delete for a fixed period after the
upload, but it can only be switched on when the bucket is created. It is not
used. Protection against a wrong delete rests on three other measures: the
module never deletes an object before the hold time (D8), only production
holds the write key (D17), and the copy on CUNEUS40 keeps every file that is
in the bucket (D16). Thijs's reasoning: a wrong delete is bad
luck, the same as for users in Odoo with the local filestore today. Checked:
for a delete by a user it is better than today; the local file is gone at the
next daily cleanup and survives only in the Odoo.sh backups (3 to 4 months),
while the cloud object stays 365 days (D8). Since D22 the CUNEUS40 copy no
longer keeps it after that.

**D21 — A local read cache with a size limit (Ries, 9-Oct-2026).** The
module keeps a copy of files that it read from the bucket on local disk, so
that a repeated read does not go to the bucket again. Two cases drive it: a
large PDF on the chatter of a record that a user opens a few times in a row,
and small files that all users read all the time (for example the app icons
of the main menu). The small, often-read files get priority. Production and
every clone use the cache with the same rules (Q18, revises D10).
- *Storage (Q17, merged by Ries on 9-Oct-2026):* a cache copy is a normal
  file in the local filestore, at Odoo's own path
  (`filestore/<db>/ab/ab12…`). There is no separate cache folder. Odoo's own
  read path, its daily cleanup and nginx (`x_sendfile`) all work on cache
  copies without change. Cache copies are in the Odoo.sh backups (at most
  the limit; Odoo.sh copies the whole `filestore/<db>`), and a restored
  database starts with the cache of the backup.
- *Which local files are cache copies:* the folder no longer tells, so the
  read-tracking table (below) has an "in bucket" mark per file. A local file
  with the mark is a cache copy and may be evicted; a local file without it
  is a file that must stay (a clone's own file, D4; a file waiting for
  upload, D14). The module sets the mark when the bucket confirmed an upload
  (D13, D14) and when it downloaded the file from the bucket. Only marked
  files count toward the size limit.
- *Write-through on production:* after a successful upload (D13, D14) the
  local copy stays as a cache copy, because a new file is often read soon. A
  file that may not enter the cache (Q16) is deleted locally after the
  upload. This amends D1: the local filestore of production holds the
  `checklist/` folder, the files waiting for upload and the cache copies.
- *Safety check before every eviction:* the module deletes a local file only
  after the bucket confirms that it holds the object with the same size (an
  S3 `HEAD` request, which the read key of clones also allows). If the bucket
  does not confirm, the module clears the mark and keeps the file; on
  production that file is then waiting for upload again. While the bucket
  cannot be reached, no file is evicted, and the cache can grow above the
  limit until the bucket is back. So a wrong mark can never delete the only
  copy of a file.
- *Size limit:* a system parameter, default 100 MB. Every other size in the
  cache rules is a fraction of this limit, never a fixed size (Ries,
  9-Oct-2026), so the rules scale when the limit changes.
- *Read count:* the module counts how often each file was read in the last
  3 days.
- *Which reads fill the cache (Q16):* every normal read of a file from the
  bucket adds the file, so the second view of the chatter PDF is already a
  hit; GDSF (below) removes a file that is read only once first. Batch work
  never uses the cache: the nightly `cloud_dump --with-cloud-files` (D9,
  D15) and the reverse migration (D2) read thousands of files once and would
  push out everything else. A file larger than a quarter of the limit never
  enters, so a single file cannot empty the cache.
- *Eviction method (chosen as best practice, at Ries's request):* GDSF,
  Greedy-Dual-Size-Frequency. It is the method that web proxies use to keep
  small, popular files (Squid `cache_replacement_policy heap GDSF`; Cherkasova,
  HP Labs 1998). Each cached file gets a keep value:
  `keep value = recent reads × (1 + (limit / 100) / file size)`.
  - *Recent reads* is a count that fades: every read adds 1, and the count
    halves every day. A read of 3 days ago weighs 1/8, so the count
    approximates "reads in the last 3 days" with one number per file and no
    daily job to roll counters over.
  - *The size factor* expresses what a cache hit saves. Every fetch from the
    bucket costs a fixed round trip, whatever the size, plus the transfer
    time. Per MB of cache space, a small file therefore saves much more
    waiting time per read. A file of 1% of the limit or smaller counts as
    small. At the default limit of 100 MB the factor is ×51 for a 20 KB
    icon, ×6 for a 200 KB credential PDF and ×1.1 for a 10 MB PDF. A large
    file never drops to zero: every read still adds to its value. The 1% is
    a constant in the code, not a setting.
  - When the cache is larger than the limit, the module deletes the files
    with the lowest keep value first, until the cache is at 90% of the limit.
    The 10% room stops an eviction run on every new file.
  - Example at the default limit: a 10 MB PDF read 3 times today has keep
    value 3.3; a 200 KB PDF read once today has 6; an icon read once 3 days
    ago has 6.4. So the large PDF is the first to go when the cache is full,
    but it stays until the cache is full, which is what the "open it a few
    times" case needs.
- *Serving (Q19, D18):* Odoo sends a cached file from local disk; see the
  source order in D18.
- *Read tracking (proposal for the spec):* the download routes run on a
  read-only database cursor (§ 3), and one database write per file read would
  load the database and conflict on hot rows. Each Odoo worker therefore
  counts reads in memory and writes the counts in one short, separate
  transaction at most once a minute, to a small table (file name, size, read
  count, last read, "in bucket" mark). The table and the filestore travel
  together in every backup, so a restore keeps them in step. A cron evicts.
  A file read in the last 5 minutes is never evicted, so a download that is
  in progress keeps its file.

**D22 — A deleted file disappears from every copy once no Odoo.sh backup can
need it (answers Q25).** (Thijs, 12-Oct-2026.) The bucket holds personal
documents (passports, work permits, A1 with BSN), so a file that Odoo deletes
must not live on forever. The Odoo.sh backups hold no file contents (D1), but
a backup restored for testing or verification must still open its files;
that is why the hold time exists. The object therefore stays in the bucket
until the last Odoo.sh backup that can refer to it has expired; the hold time
of 365 days (D8) covers the 3 to 4 months of Odoo.sh backups with a wide
margin. After that the object is deleted from the bucket, and the CUNEUS40
copy (D16) follows: it removes its copy of an object that is no longer in the
bucket. This revises the "add-only" detail of D16.

**D23 — The CUNEUS40 copy waits 30 days before it follows a delete (answers
Q26).** (Thijs, 12-Oct-2026.) The bucket normally deletes an object only 365
days after Odoo removed the file (D8). If an object disappears earlier, for
example through a bug in the module or a misused write key, a copy that
follows at once would lose the file too. So the script records the date on
which it first finds an object missing from the bucket and removes its copy
only 30 days later. A wrong delete stays recoverable for a month; a deleted
file still disappears from every copy at most 365 + 30 days after the delete
in Odoo (D22).

**D24 — Only user files go to the bucket (answers Q22).** (Thijs,
12-Oct-2026: "keep bundles local, only user files should be uploaded".) Files
that Odoo generates or that come with a module stay in the local filestore,
are never uploaded and are never evicted, as in Odoo today. The first case is
the compiled JavaScript and CSS bundles (`res_model = 'ir.ui.view'`, URL
`/web/assets/…`; in `crmain` 9 files, 15.1 MB, the largest
`web.assets_web.min.js` at 8.7 MB): every new browser session reads them and
every deploy makes new ones, so in the bucket they would cost an upload per
deploy, 365 days of dead copies and cache space. Which other files count as
"not a user file" is D25. Consequences:
- D14's catch-up rule needs a third kind of local file: a file without the
  "in bucket" mark is "waiting for upload" only when a user file uses it; a
  local-only file is never uploaded (spec detail).
- `_file_write` gets only the content and its hash, so the module decides
  "user file or not" one step earlier, where the attachment's model, field
  and URL are known (`create`, and a content change through `write`).
- The CSS reason of D18 (Q20) no longer applies, because no bundle comes
  from the bucket; D18 stands on its other reasons (security, the cache).
- If a user file and a local-only file have the same content, they share one
  name: the object is in the bucket and the local copy stays, because a
  local-only file is never evicted.

**D25 — Only the asset bundles and the module images stay local (answers
Q27).** (Thijs, 12-Oct-2026: "only JavaScript and module images are not user
files; the rest should be on the filestore", meaning the bucket.) The rule is
a short "stays local" list; every other file is a user file and goes to the
bucket, so a new module or field defaults to the bucket. The list:
- the compiled JavaScript and CSS bundles (`res_model = 'ir.ui.view'`, URL
  `/web/assets/…`; 9 files, 15.1 MB in `crmain`);
- module images that a module installs, such as the menu icons
  (`ir.ui.menu.web_icon_data`, 28 files, 0.8 MB) and the payment method
  logos (`payment.method`, about 360 files, 0.7 MB).
Thumbnails (`ir.attachment.thumbnail`, 9,219 files, 280 MB; Documents
thumbnails, 12 MB) and the smaller photo copies (`image_1024` … `image_128`,
about 120 MB) go to the bucket like every user file. Accepted cost: these are
small files that list views and the chatter show often, so the first view
after a restart or eviction fetches them from the bucket, one worker request
each; the read cache (D21, GDSF favours small, often-read files) then keeps
the hot ones local. Whether 100 MB is enough for them is checked after the
migration. The exact way to recognise a module image is a spec detail.

**D26 — The DR standby runs `cloud_dump` itself, on CUNEUS40, for new files
only (answers Q23, revises D15).** (Thijs, 12-Oct-2026: "the cloud dump on
CUNEUS40 is done via the API key and just the new files".) `cloud_dump` never
runs on Odoo.sh, so Odoo.sh never builds a zip of all files. Every night the
standby restores the plain Odoo.sh backup (the database and the small local
filestore), then `cloud_dump` on CUNEUS40 uses the read key of that database
(D11, D17) to download from the bucket only the files that the database uses
and that the standby does not hold yet. So the nightly traffic is only the
new files. The downloaded files carry no "in bucket" mark, so the cache never
evicts them (as in D2 and D9). Detail for the spec: for the standby,
`cloud_dump` fills the local filestore in place instead of writing a zip; the
work is the same as the reverse migration (D2). Read as: "the API key" is the
S3 read key of D17 (to confirm).

**D27 — CUNEUS40 uses the same read key as a laptop clone (answers Q24).**
(Thijs, 12-Oct-2026.) No separate key and no right to list the bucket. So the
copy script of D16 cannot ask the bucket which objects exist; it takes the
names from the production database that the standby restores every night:
the names that `ir_attachment.store_fname` uses, plus the names in the
`checklist/` markers of the restored filestore, which are the objects that
production removed but still holds (D8). A name that leaves both lists is
"missing" from that night on, and the script removes its copy 30 days later
(D23). Every clone keeps the narrow key: a leaked database can read a file
whose name it knows, but it cannot list or bulk-download the bucket. Detail
for the spec: the D16 copy and the standby's filestore (D26) may then be one
folder, or two; the standby must not write its own new files into the
backup copy.

## 3. Research facts that shape the design

- **File contents never change in place.** Odoo names each stored file after
  the SHA-1 hash of its content (`store_fname`, for example `ab/ab12…`). A
  content change in `_set_attachment_data` writes a new file under the new
  hash and puts the old name on the delete checklist. So one cloud object per
  `store_fname` is enough: the object for a name never changes, and "copy on
  write" in a clone needs no extra version logic (`odoo/addons/base/models/ir_attachment.py`).
- **Deletes are delayed and shared.** The checklist is a folder of empty
  marker files, `<filestore>/checklist/<store_fname>` (`_mark_for_gc`).
  `unlink` and a content change add the old name (`_file_delete` deletes
  nothing itself). `_file_write` also adds the new name before it writes, so a
  file left behind by a rolled-back transaction is cleaned up too; a marker
  therefore means "check this name", not "delete it". The daily cron "Base:
  Auto-vacuum internal data" runs `_gc_file_store` (only when
  `ir_attachment.location` is `file`): it takes a share lock on
  `ir_attachment` (10 s timeout, else retry next day), deletes each listed
  file that no `ir_attachment.store_fname` uses, and removes every marker.
  Several attachments can share one file (same content, same hash), so a name
  can come back into use after it was removed. This cleanup step is the hook
  for D6: on production the module records the "to be deleted after" date
  where Odoo would delete the file.
- **Backups contain the checklist.** Odoo's `dump_db` copies the whole
  filestore folder, `checklist/` included, and `restore_db` moves it back
  (`odoo/service/db.py`). The Odoo.sh production backup of 6-Oct-2026
  (`~/Downloads/cr.zip`, 5.5 GB) also holds `filestore/checklist/`, with one
  marker in it. The folder is nearly empty in practice: it holds only the
  names added since the last daily cleanup.
- **Downloads bypass `_file_read`.** `_to_http_stream` builds a path into the
  local filestore and calls `os.stat` on it. A clone that lacks the file
  locally fails on a download unless the module also overrides that method.
  Reads through `raw`/`datas` and the Documents module go through `_file_read`.
- **A module can add an `odoo-bin` command** through a `cli/<name>.py` file
  (`odoo/cli/command.py`, `load_addons_commands`). Odoo.sh makes its own
  backups; we cannot add an argument to that command.

- **Hetzner price model (third-party figures, check on hetzner.com before
  the decision).** Base price about €6.49/month excl. VAT, which includes
  1 TB of storage and 1 TB of egress (data downloaded from the bucket). Extra
  storage about €8.70 per TB per month, extra egress about €1 per TB. Ingress
  (uploads), S3 API calls and internal eu-central traffic are free
  ([Hetzner Object Storage](https://www.hetzner.com/storage/object-storage/),
  [sliplane comparison 2026](https://sliplane.io/blog/cheap-object-storage-providers-europe)).
- **Measured size and growth of the production files (8-Oct-2026).** From the
  Odoo.sh backup of 6-Oct-2026 (`~/Downloads/cr.zip`), by file modification
  month: 24,715 files, 5.75 GB, average 0.23 MB per file. Growth over the last
  12 months is about 0.25–0.8 GB per month, with a peak of 1.46 GB in
  September 2026. So a nightly copy of only the new files moves a few hundred
  MB per month; even a full nightly download (5.75 GB × 30 ≈ 173 GB/month)
  fits inside the included 1 TB. Production reads after D1 (every document
  that users, reports and Wilma open) are not measured yet. Rough upper
  limit: 10 users × 100 documents a day × 0.23 MB ≈ 7.5 GB per month. The
  planned count of `/web/content` and `/web/image` requests in the Odoo.sh
  log failed on 8-Oct-2026: SSH to `cuneus.odoo.com` (alias `odoosh`) timed
  out on port 22 from Thijs's laptop, so the network blocks it there.
- **Facts for the read cache (D21), checked in Odoo 19 on 9-Oct-2026.**
  - `/web/content` and `/web/assets` are `readonly=True` routes
    (`addons/web/controllers/binary.py`). With a read replica they get a
    read-only cursor, so a read cannot write its count in the same request.
  - Odoo serves a file on local disk without holding the worker when
    `x_sendfile` is on: it answers with `X-Accel-Redirect` and nginx sends
    the file (`odoo/http.py`, `Stream.get_response`). This works only for a
    path below `<data_dir>/filestore` (the root of all databases'
    filestores, served by the nginx location `/web/filestore`); for any
    other path Odoo sends the file through the worker. Whether Odoo.sh runs
    with `x_sendfile` is not checked.
  - Odoo writes the font and image addresses inside its CSS bundles without
    a server name (`url(/web/static/…)`, `rx_url` in `assetsbundle.py`). A
    browser completes them with the server that sent the CSS file, so a
    bundle that came from another server (a bucket) would point the browser
    to fonts on that server.
  - The main-menu icons (`ir.ui.menu.web_icon_data`) are read on the server
    by `load_menus`, which is cached per user, language and debug mode
    (`ormcache`). The server reads them again only after a cache reset
    (deploy, module update, menu change), not on every page.
  - Odoo stores its compiled JavaScript and CSS bundles as attachments
    (`res_model = 'ir.ui.view'`, `url` like `/web/assets/...`) and serves
    them through `/web/assets` with `_get_stream_from`. Every new browser
    session and every deploy reads them. They are the files that all users
    read all the time, more than the icons.
  - Backups copy only `filestore/<db>`: Odoo's `dump_db` copies that folder
    (`odoo/service/db.py`), and the Odoo.sh backup of 6-Oct-2026
    (`~/Downloads/cr.zip`) holds only `dump.sql` and `filestore/`. So the
    cache copies in `filestore/<db>` are in every backup.
- **Facts for "is this production?" (Q21), checked on 9-Oct-2026.**
  - Odoo makes a new `database.uuid` only when it duplicates a database or
    restores one with "This database is a copy" (`copy=True` in
    `odoo/service/db.py`). A restore with "moved", a `pg_restore` and a
    `psql` load keep the uuid of production.
  - riverdeploy restores production with `pg_restore` and then neutralizes
    (`riverdeploy/crewradar_db_restore.py`): a laptop clone has the uuid of
    production, and `database.is_neutralized` is true.
  - The DR standby on CUNEUS40 loads the production SQL dump with `psql`
    every night and does not neutralize it
    (`riverdeploy/cuneus40/dr-sync/restore-backup.sh`); it only starts Odoo
    with `--max-cron-threads=0`. So the standby has the uuid of production
    and `is_neutralized` is not set. Under D3 of 8-Oct-2026 it would hold
    the write key and count as production; the revised D3 and D15 close
    this.
  - A database comment does not travel with a dump (tested on PostgreSQL
    18.3, 9-Oct-2026): `pg_dump --no-owner`, which Odoo's `dump_db` runs,
    leaves it out, and the restored copy has none.
  - On Odoo.sh the database role of Odoo may not set it (tested on test1,
    `cuneus-test1-39470302`, PostgreSQL 16.15, 9-Oct-2026, in a
    transaction that was rolled back): the Odoo HTTP worker and the SSH
    shell both connect as `p_cuneus_test1_39470302`; `COMMENT ON DATABASE`
    fails with `ERROR:  must be owner of database cuneus-test1-39470302`;
    the role cannot read `pg_database` or `pg_roles` and has no `CREATE`
    right on the database. Port 5432 of the branch address is closed from
    outside and from the container.
  - What that role can read: its own database OID through
    `pg_stat_activity.datid` (test1: `209448235`) and the server identifier
    through `pg_control_system().system_identifier` (test1:
    `7540305758774956757`, server created 2025-08-19). Both change when
    Odoo.sh moves the database to another server or loads a dump into a new
    database.
  - `odoo.conf` on Odoo.sh (test1, 9-Oct-2026): the file is
    `/home/odoo/.config/odoo/odoo.conf`, writable by the `odoo` user, and
    its own header says that the Odoo.sh workers load it, except the worker
    settings (time limit, memory limit). The file on test1 dates from
    29-Jan-2023, so it survives the rebuilds of the branch. The Odoo
    process starts with `--config /home/odoo/.config/odoo/odoo.conf`;
    without that flag Odoo 19 reads `~/.odoorc`, which does not exist
    there. Tested on test1 (9-Oct-2026): a dummy option
    `cloud_filestore_probe = dummy-9oct2026` under `[options]` came back
    from `odoo.tools.config.get("cloud_filestore_probe")` through the
    config loader with that flag and through `odoo-bin shell` (the Odoo.sh
    wrapper `/opt/odoo.sh/odoosh/bin/odoo-bin`); the file was restored
    afterwards. Odoo logs "unknown option … stored as-is, without parsing"
    at every start for such an option. Odoo reads the file only at start,
    so a change needs a restart (`odoosh-restart`); the restart of the HTTP
    worker was not tested.
  - SSH from Ries's laptop to the Odoo.sh branch works (9-Oct-2026).

## 4. Questions

Each open question has the same shape: the situation, the question, and the
recommended answer with its reason.

### Answered

- **Q1 — Where does the "to be deleted after" date live?** Answered by D8:
  the modification time of the checklist marker. No table, no object tag.
- **Q2 — How long is the hold time?** It depends on Odoo.sh (Thijs,
  8-Oct-2026). Odoo.sh keeps 7 daily, 4 weekly and 3 monthly production
  backups; the monthly ones sit on separate backup servers, so the oldest
  backup that can be restored is about 3 to 4 months old
  ([Odoo.sh docs](https://www.odoo.com/documentation/19.0/administration/odoo_sh/getting_started/branches.html)).
  The hold time must never be shorter than that. The default of 365 days
  (D8) gives a wide margin and also covers backups that we download and keep
  ourselves. If Odoo.sh changes its retention, the system parameter changes
  with it.
- **Q3 — Which backup command gets the "with cloud files" argument?**
  Answered by D9: a module command `odoo-bin cloud_dump`.
- **Q4 — Does a clone keep a local copy of a fetched file?** Answered by D10:
  no full copy; since Q18 a clone keeps often read files in the read cache.
- **Q5 — Which cloud protocol, and where are the keys?** Answered by D11: S3;
  the read key in a system parameter, the write key in the `odoo.conf` of
  the production server (D3, since Q21).
- **Q6 — Where does the module live?** Answered by D12: otenydoo.
- **Q7 — When does production upload a new file?** Answered by D13: during
  the transaction.
- **Q8 — How does the DR standby on CUNEUS40 work?** Answered by D15: a
  full local copy, refreshed nightly with only the new files.
- **Q9 — Is the module worth it at 6 GB?** Answered by D1: the goal is to
  move the files off Odoo.sh into a bucket that we control.
- **Q10 — What happens when the bucket cannot be reached?** Answered by D14:
  fall back to local disk and alert the admins loudly.
- **Q11 — What backs up the bucket?** Answered by D16: a simple script copies
  every object to a local folder; CUNEUS40 serves as the backup.
- **Q12 — How does the browser get a cloud file?** Answered by D18: Odoo
  streams it; the bucket is invisible to clients (Ries, 9-Oct-2026, replaces
  the signed link of 8-Oct-2026).
- **Q13 — One key or two?** Answered by D17: two keys.
- **Q14 — How are the admins alerted?** Answered by D19: a Discuss channel
  message.
- **Q15 — Do we create the bucket with Object Lock?** Answered by D20: no.
- **Q16 — When does a file enter the cache?** Answered in D21 (Ries,
  9-Oct-2026): every normal read; batch work never; a file larger than a
  quarter of the limit never. Every size rule is a fraction of the limit.
- **Q17 — Which folder holds the cache?** Answered in D21 (Ries,
  9-Oct-2026): first a separate folder outside every backup; then merged
  into the local filestore, with an "in bucket" mark per file and a bucket
  check before every eviction.
- **Q18 — Does a clone also use the cache?** Answered in D21 and D10 (Ries,
  9-Oct-2026): yes, with the same rules.
- **Q19 — Does a cache hit in the browser skip the signed link?** Answered
  by D18 (Ries, 9-Oct-2026): there are no signed links; Odoo sends a cached
  file from local disk.
- **Q20 — How does the browser get Odoo's JavaScript and CSS bundles?**
  Answered by D18 (Ries, 9-Oct-2026): like every other file, Odoo streams
  them. The question came from the signed links, which would have broken
  the fonts and icons that the CSS bundles point to (§ 3).
- **Q22 — Do the compiled JavaScript and CSS bundles go to the bucket?**
  Answered by D24: no, they stay local.
- **Q27 — Which files are "user files"?** Answered by D25: everything
  except the asset bundles and the module images.
- **Q23 — Does the DR standby need `cloud_dump` at all?** Answered by D26:
  yes, but it runs on CUNEUS40 with the read key and fetches only new files.
- **Q24 — Which rights does the CUNEUS40 copy script need?** Answered by
  D27: the same read key as a laptop clone.
- **Q25 — How do deleted personal documents really disappear?** Answered
  by D22 (Thijs, 12-Oct-2026).
- **Q26 — Does the CUNEUS40 copy wait before it follows a delete?** Answered
  by D23: 30 days.
- **Q21 — How does the module know that it runs on production?** (Thijs,
  9-Oct-2026: a copy that is not neutralized is also a clone; use the
  database uuid of production, although that uuid can change when Odoo
  upgrades production.) Answered by D3 (Ries, 9-Oct-2026): the write key
  lives in the `odoo.conf` of the production server, never in the
  database. A database comment cannot be set by Odoo on Odoo.sh, and every
  database identifier changes when Odoo.sh moves the database (§ 3).

### Open

Raised in the full review of 12-Oct-2026.

None (12-Oct-2026).

## 5. Test bucket (homelab, not production)

> **Removed on 12-Oct-2026.** The container, its data (a 5.7 GB copy of the
> production files that a test session had loaded) and the pve mount were
> deleted the same day. The facts below stay as a record of the setup and of
> the Garage limit for the narrow read key; the store does not exist now.

A private S3 test store, set up on 12-Oct-2026 for the build and for later
tests of the module. It is a personal homelab system of the Radar dev team,
not a Cuneus system; production stays on Hetzner (D11).

- **Server:** Garage v2.4.1 (S3-compatible, single node), in the unprivileged
  LXC container 140 `garage` on the Proxmox host `pve`. Endpoint
  `http://10.0.10.140:3900`, region `garage`, path-style addressing.
- **Reach:** only from the developer's laptop, over the tailnet route that
  `pve` advertises for `10.0.10.0/24`. Odoo.sh (test1, staging) cannot reach
  it, so a test on Odoo.sh needs the real Hetzner bucket.
- **Storage:** the Garage metadata is on the container's own disk, because
  Garage advises against metadata on NFS. The data blocks are on the ZFS pool
  of the TrueNAS VM, in the folder `garage` of the NFS export
  `10.0.10.130:/mnt/store/internal`. `pve` mounts that export at
  `/mnt/truenas-store-internal` and binds the folder into the container with
  a raw `lxc.mount.entry`. A normal `mp0` mount point does not work: the
  export maps root to an unprivileged user (root squash), the folder has
  mode `750` for the container's `garage` user (`100999:100991` on the
  host), and the pre-start hook of `pve` cannot open it (exit status 13).
- **Buckets and keys:** `cloud-filestore-test` with the key
  `cloud-filestore-write` (read, write, owner), as the production write key
  of D17, and the key `cloud-filestore-read` (read), as the clone read key.
  The bucket `personal` has its own key and is not used for tests.
- **Credentials:** only on the developer's laptop, in
  `~/.config/garage-pve/config` and `~/.config/garage-pve/credentials` (AWS
  profile format, one profile per key); never in the repository or in a
  database that leaves the laptop.
- **Browse the files:** `~/.config/garage-pve/webui.sh` starts `garage-webui`
  (nixpkgs) on `http://127.0.0.1:3909`, through an SSH tunnel to the Garage
  admin API inside the container. The UI has admin rights and no login, so
  it listens on localhost only (`HOST=127.0.0.1`).
- **Checked on 12-Oct-2026 with boto3:** the write key puts and deletes; the
  read key gets and heads an object, and its put and delete return
  `AccessDenied`; the `personal` key is refused on the test bucket.
- **Limit for the tests:** a Garage read right also allows a bucket list
  (`ListObjectsV2` with the read key returned the object). So this store
  cannot prove the narrow read key of D17 and D27 (get and head only, no
  list); that check must run against the Hetzner bucket policy. Garage has
  no Object Lock, which D20 does not use.
