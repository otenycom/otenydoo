# Cloud Filestore Plan 1 — Core Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** A generic Odoo 19 module `oteny_cloud_filestore` that keeps the
contents of user files in an S3 bucket instead of the local filestore, with a
read-only clone mode, a hold time before a cloud delete, and a local fallback
with an alert when the bucket cannot be reached.

**Architecture:** The module overrides the file hooks of `ir.attachment`
(`_file_write`, `_file_read`, `_mark_for_gc`, `_gc_file_store_unsafe`,
`_to_http_stream`). `ir_attachment.location` stays `file`, so the rest of Odoo
works unchanged. A thin `CloudStore` class wraps `boto3`; unit tests replace it
with an in-memory `FakeCloudStore`, and live tests run against a real S3 store
only when `OTENY_CLOUD_FILESTORE_TEST_CONFIG` is set.

**Tech Stack:** Odoo 19, Python 3.12, `boto3`/`botocore` (S3 protocol, path or
virtual addressing), Odoo `TransactionCase` tests.

**Spec:** [../references/design-decisions.md](../references/design-decisions.md)
(D1–D27).

## Scope

In this plan: D1–D8, D11, D13, D14, D17, D18 (streaming, first version), D19,
D24, D25, D2 (both batch jobs).

Plan 2, not here: the read cache and its "in bucket" mark (D21), streaming in
chunks for large files (D18), `odoo-bin cloud_dump` (D9, D26) and the
CUNEUS40-type copy of the bucket (D16, D22, D23, D27). Without the cache,
production removes its local copy right after a confirmed upload (the first
form of D14, before D21 amended it).

## Global Constraints

- Generic module: no business name or business model in code, data or help
  text (`.git_hooks/customer_template_gate.py`; add `oteny_cloud_filestore`
  to the gate's `SCOPES` and to the `files:` pattern in
  `.pre-commit-config.yaml`).
- No agent attribution in commits, code or docs (otenydoo `AGENTS.md`): no
  `Co-Authored-By` trailer.
- Secrets never reach a terminal or a log. Never log a key, never print the
  test INI.
- Manifest: `"version": "19.0.1.0"`, `"depends": ["base", "mail", "web"]`,
  `"external_dependencies": {"python": ["boto3"]}`, `"license": "OEEL-1"`,
  `"author": "Oteny"`.
- System parameters (all prefixed `oteny_cloud_filestore.`):
  `endpoint_url`, `region`, `bucket`, `addressing_style` (default `path`),
  `key_prefix` (default empty), `read_access_key_id`,
  `read_secret_access_key`, `hold_days` (default `365`),
  `alert_channel_id` (id of a `discuss.channel`), `outage_since`
  (internal: empty when the bucket works).
- `odoo.conf` options for the write key (D3): `cloud_filestore_write_access_key_id`,
  `cloud_filestore_write_secret_access_key`, read with
  `odoo.tools.config.get(...)`.
- Object key in the bucket = `key_prefix + store_fname` (for example
  `ab/ab12…`).
- Modes: `off` (no `endpoint_url` or `bucket`: plain Odoo), `production`
  (both write options set AND `database.is_neutralized` not set), `clone`
  (every other case). A clone never puts or deletes in the bucket.
- Timeouts: connect 3 s, read 10 s, `max_attempts` 2.
- Test tags: `@tagged("oteny_cloud_filestore", "post_install", "-at_install")`.
- Test command (dedicated DB, worktree addons path; add `-i` on the first
  run, `-u oteny_cloud_filestore` after a schema change):

```zsh
OTENY_CLOUD_FILESTORE_TEST_CONFIG=~/.config/oteny/cloud_filestore_test.ini \
~/odoo/venv/bin/python3 ~/odoo/odoo19/odoo-bin --stop-after-init -p 8071 \
  --addons-path=~/odoo/odoo19/addons,~/odoo/enterprise19,~/oteny/otenydoo \
  -d cf-test -i oteny_cloud_filestore --without-demo=True \
  --http-interface 127.0.0.1 --max-cron-threads 0 --test-enable \
  --test-tags=oteny_cloud_filestore --log-level=test 2>&1 | tee /tmp/cf-test.log
```

  Pass: the log ends with `0 failed, 0 error(s)`. Without the env var the
  live tests report as skipped and everything else still passes.
- Live test store (laptop only): Garage at a plain-http endpoint, region
  `garage`, path-style addressing, bucket used only for tests. The read key
  can list (Garage); never assert that it cannot.

## Review Focus

1. A save while the bucket is down must never fail or lose the file: the file
   stays local and one alert is posted (Task 4 test
   `test_write_falls_back_when_store_down`).
2. A clone must never put or delete, even when someone copied the write
   options into its `odoo.conf` but the database is neutralized (Task 2 test
   `test_mode_neutralized_with_key_is_clone`, Task 6 test
   `test_clone_gc_never_deletes_in_bucket`).
3. A name reused before the hold time ends keeps its object (Task 6 test
   `test_gc_keeps_object_of_reused_name`).
4. A browser download of a file that exists only in the bucket returns the
   bytes, not a 500 from `os.stat` (Task 5 test
   `test_http_stream_from_bucket`).
5. Asset bundles and module data files never go to the bucket, also not
   through the catch-up job (Task 3 and Task 7 tests).

---

### Task 1: Module skeleton and the `CloudStore` client

**Files:**
- Create: `oteny_cloud_filestore/__init__.py`, `__manifest__.py`,
  `tools/__init__.py`, `tools/cloud_store.py`, `tests/__init__.py`,
  `tests/common.py`, `tests/test_cloud_store_live.py`
- Modify: `.git_hooks/customer_template_gate.py` (`SCOPES`),
  `.pre-commit-config.yaml` (`files:` pattern)

**Interfaces:**
- Produces, in `oteny_cloud_filestore/tools/cloud_store.py`:
  - `class CloudStoreError(Exception)` — any refusal or unexpected answer.
  - `class CloudStoreUnavailable(CloudStoreError)` — connection error,
    timeout, or HTTP 5xx.
  - `class CloudStore(endpoint_url: str, region: str, bucket: str,
    access_key_id: str, secret_access_key: str, addressing_style: str =
    "path", key_prefix: str = "")` with methods
    `put(name: str, data: bytes) -> None`,
    `get(name: str) -> bytes | None` (None when the object is missing),
    `head(name: str) -> int | None` (size, None when missing),
    `delete(name: str) -> None` (no error when missing).
  - `name` is a `store_fname`; the object key is `key_prefix + name`.
- Produces, in `oteny_cloud_filestore/tests/common.py`:
  - `class FakeCloudStore` with the same four methods, a public dict
    `objects: dict[str, bytes]`, a boolean `down` (when True every method
    raises `CloudStoreUnavailable`) and lists `puts`, `deletes` that record
    names.
  - `def live_store_config() -> dict | None`: reads the INI file named by
    `OTENY_CLOUD_FILESTORE_TEST_CONFIG`, section `[s3]`, keys
    `endpoint_url`, `region`, `bucket`, `write_access_key_id`,
    `write_secret_access_key`, `read_access_key_id`,
    `read_secret_access_key`; returns None when the variable is unset or the
    file is missing.

- [ ] **Step 1: Write the live tests** in `tests/test_cloud_store_live.py`,
  class `TestCloudStoreLive(BaseCase)` (`odoo.tests.BaseCase`, no database
  needed); `setUpClass` calls `self.skipTest(...)` when
  `live_store_config()` is None, and sets `key_prefix =
  f"test-{uuid4().hex}/"`; `tearDownClass` deletes every name it put.
  - `test_write_key_roundtrip`: put `b"hello"` as `ab/abc`, `head` == 5,
    `get` == `b"hello"`, delete, `get` is None, `head` is None.
  - `test_delete_missing_is_silent`: delete of a never-written name raises
    nothing.
  - `test_read_key_reads_but_cannot_write`: put with the write key, `get`
    and `head` with the read key succeed; `put` and `delete` with the read
    key raise `CloudStoreError` and not `CloudStoreUnavailable`.
  - `test_unreachable_endpoint_is_unavailable`: a store on
    `http://127.0.0.1:9` raises `CloudStoreUnavailable` from `head` within
    15 seconds.
- [ ] **Step 2: Run** the test command; expected: import error (module
  missing).
- [ ] **Step 3: Implement** the manifest (Global Constraints values),
  `CloudStore` with one `boto3.client("s3", ...)` per instance, built with
  `botocore.config.Config(connect_timeout=3, read_timeout=10,
  retries={"max_attempts": 2}, s3={"addressing_style": addressing_style},
  signature_version="s3v4")`. Map `botocore.exceptions.EndpointConnectionError`,
  `ConnectTimeoutError`, `ReadTimeoutError`, `ConnectionClosedError` and a
  `ClientError` with HTTP status ≥ 500 to `CloudStoreUnavailable`; a
  `ClientError` with 404 / `NoSuchKey` to the None result; every other
  `ClientError` to `CloudStoreError`. `FakeCloudStore` and
  `live_store_config` in `tests/common.py`. Add the module to the gate.
- [ ] **Step 4: Run** the test command with the env var; expected: 4 live
  tests pass. Run once without it; expected: skipped, 0 failed.
- [ ] **Step 5: Commit** `oteny_cloud_filestore: S3 client and live store tests`.

### Task 2: Settings, mode and the store factory

**Files:**
- Create: `oteny_cloud_filestore/models/__init__.py`,
  `models/ir_attachment.py`, `tests/test_mode.py`
- Modify: `oteny_cloud_filestore/__init__.py`

**Interfaces:**
- Consumes: `CloudStore`, `CloudStoreError` (Task 1).
- Produces on `ir.attachment` (model methods, `@api.model`):
  - `_cloud_filestore_mode(self) -> str` — `"off"`, `"production"` or
    `"clone"` per Global Constraints.
  - `_cloud_filestore_store(self) -> CloudStore | None` — None in `off`;
    the write key in `production`; the read key in `clone`. The instance is
    cached at module level, keyed by the tuple of all settings, so a
    settings change builds a new one and a read does not build a boto3
    client every time.
  - `_cloud_filestore_param(self, key: str, default: str = "") -> str` —
    reads `oteny_cloud_filestore.<key>` with `sudo()`.

- [ ] **Step 1: Write tests** in `tests/test_mode.py`, class
  `TestCloudFilestoreMode(TransactionCase)`; patch
  `odoo.tools.config` options with `patch.dict(config.options, {...})`:
  - `test_mode_off_without_bucket`: no params → `"off"`, store is None.
  - `test_mode_production`: params set, both write options set, not
    neutralized → `"production"`.
  - `test_mode_clone_without_key`: params set, no write options → `"clone"`.
  - `test_mode_neutralized_with_key_is_clone`: write options set and
    `database.is_neutralized = "True"` → `"clone"`.
  - `test_store_uses_read_key_in_clone`: in clone the returned store was
    built with `read_access_key_id` (assert on the constructor arguments
    through `patch` of `CloudStore`, never on the secret value in a
    message).
- [ ] **Step 2: Run**; expected: FAIL (methods missing).
- [ ] **Step 3: Implement** the three methods in `models/ir_attachment.py`.
- [ ] **Step 4: Run**; expected: PASS.
- [ ] **Step 5: Commit** `oteny_cloud_filestore: settings and production/clone mode`.

### Task 3: The local-only rule (D24, D25)

**Files:**
- Modify: `models/ir_attachment.py`, `__init__.py` (post-init hook),
  `__manifest__.py` (`"post_init_hook"`)
- Create: `tests/test_local_only.py`

**Interfaces:**
- Produces:
  - field `cloud_local_only = fields.Boolean(readonly=True, copy=False)` on
    `ir.attachment`: True for a file that never goes to the bucket.
  - `create` override sets it from each vals: True when `url` starts with
    `/web/assets/` or the context has `install_mode`; and calls `super()`
    with context `cloud_filestore_local_only=True` only when every vals in
    the batch is local-only.
  - `_cloud_filestore_is_local_only(self) -> bool` (model method) — True
    when the context has `install_mode` or `cloud_filestore_local_only`.
    Task 4 calls it in `_file_write`.
  - `post_init_hook(env)`: sets the flag on existing rows whose `url` is
    like `/web/assets/%`, or whose `res_field` is set and whose
    `(res_model, res_id)` has an `ir.model.data` row with a module other
    than `__import__` and `__export__`. One SQL `UPDATE` per rule.

- [ ] **Step 1: Write tests** in `tests/test_local_only.py`:
  - `test_asset_bundle_is_local_only`: create with `url =
    "/web/assets/1/web.assets_web.min.js"`, `res_model = "ir.ui.view"` →
    flag True.
  - `test_install_mode_is_local_only`: create under
    `with_context(install_mode=True)` → flag True.
  - `test_user_file_is_not_local_only`: plain create on `res.partner` →
    flag False.
  - `test_post_init_hook_marks_existing_module_images`: call
    `post_init_hook(self.env)` and assert a `ir.ui.menu` `web_icon_data`
    attachment of a menu with an xmlid is flagged, and an attachment of a
    partner without xmlid is not.
- [ ] **Step 2: Run**; FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** with `-u oteny_cloud_filestore`; PASS.
- [ ] **Step 5: Commit** `oteny_cloud_filestore: asset bundles and module files stay local`.

### Task 4: Write path and the alert (D13, D14, D19)

**Files:**
- Modify: `models/ir_attachment.py`
- Create: `models/alert.py` (an `AbstractModel` `oteny.cloud.filestore.alert`),
  `tests/test_write.py`

**Interfaces:**
- Consumes: Task 2 mode/store, Task 3 `_cloud_filestore_is_local_only`.
- Produces:
  - `_file_write(bin_value, checksum)` override: always `super()` first (the
    file lands on local disk as in Odoo). Then, only in `production` and
    not local-only: `store.put(fname, bin_value)`; on success remove the
    local file and call `alert._bucket_ok()`; on `CloudStoreError` keep the
    local file and call `alert._bucket_failed(error_text)`. Returns `fname`.
  - `oteny.cloud.filestore.alert._bucket_failed(self, reason: str) -> None`:
    when `outage_since` is empty, set it to now and post one message to the
    channel in `alert_channel_id`; when already set, do nothing. Posts in a
    separate cursor (`self.env.registry.cursor()`) so a rollback of the
    user's transaction does not drop the alert. The message never contains
    a key.
  - `oteny.cloud.filestore.alert._bucket_ok(self) -> None`: when
    `outage_since` is set, clear it and post one "the bucket works again"
    message; else do nothing.
  - Test note: a separate cursor in a `TransactionCase` cannot see the
    test's uncommitted channel; the tests run the alert inside the
    registry's test mode (`self.enter_registry_test_mode()` or
    `self.registry.enter_test_mode(self.cr)`, whichever Odoo 19 offers), so
    the separate cursor shares the test transaction.

- [ ] **Step 1: Write tests** in `tests/test_write.py` (patch
  `_cloud_filestore_store` to return a `FakeCloudStore`, mode
  `production`, an alert channel created in `setUpClass`):
  - `test_write_uploads_and_removes_local`: create an attachment with raw
    `b"user file"` → `fake.objects[store_fname] == b"user file"`, the
    local path does not exist.
  - `test_write_falls_back_when_store_down`: `fake.down = True` → the save
    succeeds, the local file exists, `outage_since` is set, the channel has
    exactly one new message; a second failing save posts no second
    message.
  - `test_write_recovers_and_posts_ok`: after a failure, a successful save
    clears `outage_since` and posts one recovery message.
  - `test_clone_never_puts`: mode `clone` → no put, local file kept.
  - `test_local_only_never_puts`: create under `install_mode` → no put.
- [ ] **Step 2: Run**; FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run**; PASS.
- [ ] **Step 5: Commit** `oteny_cloud_filestore: upload on write, local fallback and one alert per outage`.

### Task 5: Read path and HTTP streaming (D4, D18 first version)

**Files:**
- Modify: `models/ir_attachment.py`
- Create: `tests/test_read.py`

**Interfaces:**
- Produces:
  - `_file_read(fname, size=None)` override: when the local file exists or
    mode is `off`, `super()`. Else `store.get(fname)`; return
    `data[:size]` when `size` is set; return `b""` when missing; on
    `CloudStoreError` log a warning without secrets, call
    `alert._bucket_failed` in `production`, return `b""`.
  - `_to_http_stream()` override: when `store_fname` is set, the mode is not
    `off` and the local file does not exist, return a `Stream` of
    `type="data"` with `data = self._file_read(self.store_fname)`,
    `mimetype`, `download_name = self.name`, `etag = self.checksum`,
    `public = self.public`, `last_modified = self.write_date`,
    `size = len(data)`. Otherwise `super()`. The whole file is in memory in
    this version; chunked streaming is plan 2.

- [ ] **Step 1: Write tests** in `tests/test_read.py`:
  - `test_read_from_bucket_when_not_local`: object only in the fake →
    `attachment.raw` equals it.
  - `test_read_prefers_local`: local file present and a different fake
    object → `raw` equals the local bytes and the fake was not called.
  - `test_read_size_limit`: `_file_read(fname, size=3)` returns 3 bytes.
  - `test_read_missing_returns_empty`: nothing anywhere → `b""`.
  - `test_http_stream_from_bucket`: `HttpCase`, `url_open` of
    `/web/content/<id>?download=true` for an attachment that is only in the
    fake returns 200 and the bytes (patch the store on the model class so
    the HTTP worker sees it).
- [ ] **Step 2: Run**; FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run**; PASS.
- [ ] **Step 5: Commit** `oteny_cloud_filestore: read from the bucket and stream through Odoo`.

### Task 6: Delete with a hold time (D6, D7, D8)

**Files:**
- Modify: `models/ir_attachment.py`
- Create: `tests/test_gc.py`

**Interfaces:**
- Produces:
  - `_mark_for_gc(fname)` override: `super()`, then in `production`
    `os.utime` the marker so its mtime is now (the hold time counts from the
    last mark).
  - `_gc_file_store_unsafe()` override: in `off` and `clone`, `super()`.
    In `production` the same checklist walk and whitelist query as Odoo,
    with this change per name: in use → remove the marker; not in use →
    remove the local file if any, and when the marker mtime is older than
    `hold_days` days call `store.delete(name)` and remove the marker,
    otherwise keep the marker. A `CloudStoreError` on delete keeps the
    marker for the next run. Log the counts, never a key.

- [ ] **Step 1: Write tests** in `tests/test_gc.py` (production, fake
  store; set a marker's mtime with `os.utime` to simulate age):
  - `test_gc_keeps_young_object`: unlink an attachment, run
    `_gc_file_store_unsafe()` → object still in the fake, marker kept.
  - `test_gc_deletes_after_hold_time`: marker mtime 366 days ago → object
    deleted, marker removed.
  - `test_gc_keeps_object_of_reused_name`: old marker but a second
    attachment with the same content still exists → no delete, marker
    removed.
  - `test_mark_resets_marker_mtime`: an old marker is touched again by a
    new `_file_delete` → mtime is now.
  - `test_clone_gc_never_deletes_in_bucket`: mode `clone`, old marker →
    `fake.deletes` stays empty.
- [ ] **Step 2: Run**; FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run**; PASS.
- [ ] **Step 5: Commit** `oteny_cloud_filestore: delete from the bucket only after the hold time`.

### Task 7: Upload and download batch jobs (D2, D14 catch-up)

**Files:**
- Modify: `models/ir_attachment.py`, `__manifest__.py` (`data`)
- Create: `data/ir_cron.xml`, `tests/test_jobs.py`

**Interfaces:**
- Produces:
  - `_cloud_filestore_upload_pending(self, limit: int = 500) -> int`
    (`@api.model`, production only, else returns 0): walks the local
    filestore except `checklist/`; a file is pending when at least one
    `ir.attachment` with that `store_fname` has `cloud_local_only` False;
    puts it, confirms with `head` == local size, then removes the local
    file; stops at `limit` files; returns the count uploaded. Calls
    `alert._bucket_ok()` after a run with at least one upload and
    `alert._bucket_failed()` on `CloudStoreError` (and stops the run).
  - `_cloud_filestore_download_all(self, limit: int = 500) -> int`
    (any mode except `off`): for each distinct `store_fname` of
    attachments whose local file is missing, `get` and write it to its
    local path; returns the count. This is the reverse migration.
  - Cron `ir_cron_cloud_filestore_upload` (every 10 minutes, active)
    calling `model._cloud_filestore_upload_pending()`. No cron for the
    download; it is run by hand from `odoo-bin shell` or a server action.

- [ ] **Step 1: Write tests** in `tests/test_jobs.py`:
  - `test_upload_pending_moves_local_files`: create two attachments while
    `fake.down`, then `fake.down = False`, run → returns 2, objects in the
    fake, local files gone, `outage_since` cleared.
  - `test_upload_skips_local_only`: an attachment with
    `cloud_local_only` True and its local file → not uploaded.
  - `test_upload_respects_limit`: three pending, `limit=2` → returns 2.
  - `test_download_all_restores_local_files`: objects only in the fake →
    after the run the local files exist with the same bytes.
- [ ] **Step 2: Run**; FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** with `-u oteny_cloud_filestore`; PASS.
- [ ] **Step 5: Commit** `oteny_cloud_filestore: upload and download batch jobs`.

### Task 8: Live end-to-end test and the skill

**Files:**
- Create: `tests/test_end_to_end_live.py`, `oteny_cloud_filestore/README.md`
- Create: `.claude/skills/oteny-cloud-filestore/SKILL.md`
- Modify: `.claude/skills/SKILL.MD` (index row), `README.md` (test module
  list)

**Interfaces:**
- Consumes: everything above, with a real `CloudStore` from
  `live_store_config()`.

- [ ] **Step 1: Write** `TestEndToEndLive(TransactionCase)`, skipped
  without the env var; sets the system parameters from the live config
  with a unique `key_prefix`, patches the write options into
  `config.options`, and deletes its objects in `tearDown`:
  - `test_production_roundtrip`: create an attachment → object exists in
    the bucket (`head`), local file gone, `raw` reads it back.
  - `test_clone_reads_with_read_key`: switch to clone (remove the write
    options) → `raw` still reads it; creating a new attachment keeps it
    local and puts nothing (`head` of its name is None).
- [ ] **Step 2: Run** with the env var; PASS.
- [ ] **Step 3: Write** the skill `SKILL.md` (frontmatter `name:
  oteny-cloud-filestore`; what it does and when to use it; the two modes;
  setup steps: bucket, read key in system parameters, write key in
  `odoo.conf`; the live test env var and INI keys; a roadmap with plan 2
  items) and the module `README.md`. Wrap at 80 columns, Simplified
  Technical English.
- [ ] **Step 4: Run** the full test command; `0 failed, 0 error(s)`.
- [ ] **Step 5: Commit** `oteny_cloud_filestore: live end-to-end test and skill`.
