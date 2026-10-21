# Live test stores

## Current: Google Cloud Storage, folder `dev/` (13-Oct-2026)

The live tests run against the folder `dev/` of the bucket
`cuneus-crewradar-docs` (Google Cloud project `gen-lang-client-0134060903`,
organization "Crewradar Cuneus", location EU multi-region). The same bucket
holds the production files in the folder `main/`; `dev/` is for development
and tests only and stays small.

- **S3 interface:** endpoint `https://storage.googleapis.com`, region `auto`,
  path-style addressing, HMAC keys. Bucket settings: public access blocked,
  uniform bucket-level access, soft delete 7 days (a deleted object stays
  recoverable for 7 days).
- **Folders:** `dev/` and `main/` are managed folders, so each has its own
  access rules. A key limited to `dev/` can never touch `main/`; the live
  test `test_write_key_stays_in_its_folder` proves it.
- **Keys:** two service accounts, each with one HMAC key.
  `crewradar-filestore-dev-write` has `roles/storage.objectAdmin` on `dev/`
  only. `crewradar-filestore-dev-read` has the custom project role
  `cloudFilestoreObjectGet` (only `storage.objects.get`) on `dev/` only: it
  reads a known object and cannot list (D27; live test
  `test_read_key_cannot_list`). A third, inactive HMAC key of the write
  account is left over from a retried setup run.
- **Where the secrets are:** 1Password, account `thijs@vriend.studio`, vault
  Oteny, items "CrewRadar cloud filestore dev write (GCS HMAC)" and "...
  dev read (GCS HMAC)". The test INI is built from them with `op inject`
  and the template `~/.config/oteny/cf-lab/cloud_filestore_test.ini.tpl`
  (`op://` references by item ID, because the titles hold parentheses,
  which an `op://` path does not accept). The INI sets `key_prefix = dev/`;
  each run writes under `dev/test-<uuid>/` and deletes its objects.
- **Key creation is blocked by default.** The organization enforces
  `iam.disableServiceAccountKeyCreation`, which also blocks HMAC keys. A new
  key needs a short project-level exception: switch the policy off for the
  project, create the key, then remove the exception so the project inherits
  the enforced policy again. Existing keys keep working. The first key
  passed about 3 minutes after the exception was set.
- **Production keys (21-Oct-2026):** `crewradar-filestore-main-write`
  (`roles/storage.objectAdmin` on `main/` only) and
  `crewradar-filestore-main-read` (`cloudFilestoreObjectGet` on `main/`
  only), one HMAC key each, in 1Password vault Oteny as "CrewRadar cloud
  filestore main write (GCS HMAC)" and "... main read (GCS HMAC)", with the
  same fields as the dev items. All 11 live tests passed with them and
  `key_prefix = main/`.
- **Tools:** `gcloud` and `op` come from `nix shell nixpkgs#google-cloud-sdk`
  and `NIXPKGS_ALLOW_UNFREE=1 nix shell --impure nixpkgs#_1password-cli`;
  `op` needs the 1Password app setting "Integrate with 1Password CLI" and
  `OP_ACCOUNT` set to the user ID of the vriend.studio account.

## Earlier: Garage on the homelab (disabled 13-Oct-2026)


Moved here from radar's cloud filestore plan on 12-Oct-2026: all
documentation of this module lives in otenydoo. The live tests read
their settings from the INI file named by
`OTENY_CLOUD_FILESTORE_TEST_CONFIG` (see SKILL.md, Tests).

A private S3 test store, set up on 12-Oct-2026 for the build and for later
tests of the module. It is a personal homelab system of the Radar dev team,
not a business's system; production uses its own bucket (D11).

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
- **Checked on 12-Oct-2026 with boto3:** the write key puts and deletes; the
  read key gets and heads an object, and its put and delete return
  `AccessDenied`; the `personal` key is refused on the test bucket.
- **Limit for the tests:** a Garage read right also allows a bucket list
  (`ListObjectsV2` with the read key returned the object). So this store
  cannot prove the narrow read key of D17 and D27 (get and head only, no
  list); that check must run against the Hetzner bucket policy. Garage has
  no Object Lock, which D20 does not use.
