# Live test store (homelab, not production)

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
