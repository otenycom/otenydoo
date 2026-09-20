# Parallel Test Runner for Odoo

*Developer reference: Odoo module that parallelizes test execution by cloning the test database and letting worker subprocesses pull test classes from a shared queue.*

**Status**: Implemented — `odoo_parallel_tests` module, installed as dependency of crewradar.

## Problem

Running all crewradar tests sequentially takes ~6 minutes (903 tests). As the test suite grows, this becomes a bottleneck for development feedback loops. Odoo's built-in test runner is single-process and sequential.

## Solution

An Odoo addon (`odoo_parallel_tests`) that monkey-patches `odoo.tests.loader.run_suite` to detect post-install test runs with multiple test classes. When found, it clones the test database and distributes test classes across parallel worker subprocesses.

## Architecture

```text
┌─────────────────────────────────────────────────────┐
│              Odoo CLI (odoo-bin)                     │
│  --test-enable --test-tags=<odoo.testTags>          │
└──────────────┬──────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────┐
│          Patched run_suite()                         │
│                                                     │
│  1. Check call stack: only parallelize post-install  │
│  2. Discover & group test methods by class           │
│  3. If count <= 1 class: run normally (fast path)    │
│  4. If count > 1: enter parallel mode                │
└──────────────┬──────────────────────────────────────┘
               │ parallel mode
               ▼
┌─────────────────────────────────────────────────────┐
│       Database Cloning (with reuse)                  │
│                                                     │
│  1. Compute schema + XML-ID + module-version fps     │
│  2. If all three match the saved pool: reuse the     │
│     first N valid clones, create only the missing    │
│  3. Otherwise: drop the pool, CREATE DATABASE ×N     │
│     (FILE_COPY on PG 15+), hardlink the filestore    │
│  4. Save the pool state for next run                 │
└──────────────┬──────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────┐
│         Worker Subprocesses                          │
│                                                     │
│        queue/ (one file per class, longest first)   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │ Worker 0 │ │ Worker 1 │ │ Worker N │            │
│  │ claims   │ │ claims   │ │ claims   │  ← rename  │
│  │ db: w-0  │ │ db: w-1  │ │ db: w-N  │            │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘            │
│       │             │             │                  │
│       ▼             ▼             ▼                  │
│  reader thread  reader thread  reader thread         │
│  → log file     → log file     → log file            │
│  → stdout[W0]   → stdout[W1]   → stdout[WN]          │
│   result.json   result.json   result.json            │
└──────────────┬──────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────┐
│           Result Aggregation                         │
│                                                     │
│  1. Wait for all workers to finish                   │
│  2. Join reader threads (all output already printed) │
│  3. Read each worker's result JSON + log output      │
│  4. Aggregate pass/fail/error counts                 │
│  5. Keep clones for reuse on next run                │
│  6. Return aggregated OdooTestResult                 │
└─────────────────────────────────────────────────────┘
```

## Module Structure

```text
odoo_parallel_tests/
├── __init__.py          # Package init, imports patch (triggers apply)
├── __manifest__.py      # Odoo module manifest (depends: base)
├── config.py            # Configuration from environment variables
├── discovery.py         # Test grouping by class, longest-first queue order from stats
├── queue.py             # The work queue: one file per class, claimed by atomic rename
├── patch.py             # Monkey-patch for loader.run_suite: master orchestration + worker pull loop
├── cloner.py            # Clone pool (FILE_COPY on PG 15+, createdb -T before), filestore replicas, cleanup
├── runner.py            # Worker subprocess spawning and result collection
└── stats.py             # Per-class duration stats: load, save (merge), extract from results
```

## How It Works

### Patch Application

The patch is applied when the module is imported during `load_modules()`. This happens before post-install tests run:

1. `__init__.py` imports `patch.py`
2. `patch._apply()` saves original `loader.run_suite` and replaces it
3. All subsequent `loader.run_suite()` calls go through the patched version

### Phase Detection

Only post-install tests should be parallelized. At-install tests run during module loading when the database is still being modified. Detection uses call stack inspection:

- **At-install**: `load_module_graph` is in the call stack above `run_suite` → skip parallel
- **Post-install**: `load_module_graph` NOT in call stack → parallel allowed

### Worker Communication

Each worker is a full `odoo-bin` subprocess with:

- `ODOO_PARALLEL_QUEUE`: the shared work-queue directory (below)
- `ODOO_PARALLEL_WORKER`: its index, which names its claim directory
- `ODOO_PARALLEL_RESULT`: path to write JSON results
- `ODOO_TEST_PARALLEL=never`: prevents recursive parallelization
- Its own cloned database and unique HTTP port

The worker's patched `run_suite` sees `ODOO_PARALLEL_QUEUE`, groups its discovered suite by class, pulls one class at a time from the queue, runs it with the original `run_suite`, and writes the summed results (plus per-class timing and the list of classes it ran) to the JSON file. Odoo calls `run_suite` exactly once for the whole post-install suite, so a worker gets one call and one loop.

### Work queue (since 2026-09-04; replaced static LPT batches)

Tests are grouped by class (preserving `setUpClass` data sharing); each class runs entirely on one worker. The master writes one empty file per class into `<tmpdir>/queue/`, named `<index>__<class key>`, in **longest-first order** from the stats file. A worker claims the next class by renaming its file into `<tmpdir>/claimed/<worker>/`. `os.rename` is atomic on POSIX, so a class is claimed exactly once; a worker that loses the race gets `FileNotFoundError` and takes the next file. A worker that finishes early simply pulls the next class.

Why a queue: with static batches the makespan depended on how good last run's per-class estimates were. On the full suite the workers finished between 55 s and 98 s, a 43 s spread, against a lower bound of about 64 s of test time per worker. With a queue the spread collapses to roughly the duration of the last class taken, and stale stats only affect the order.

**Stats file**: after each run the per-class durations go to `/tmp/odoo_parallel_test_stats.json` (merge semantics: only classes that ran are updated, so a `test_salary` run does not erase billing data). The stats now decide the queue order only. A class without stats gets the median of the known durations, which places it mid-queue.

### Clone pool and reuse

Clones are kept between runs as a **pool** and reused when the base database has not changed (the common case during development without `-u`). Staleness is detected via three fingerprints, all computed via `psql` in ~0.1s:

1. **Schema fingerprint** — md5 hash of all public table columns and types. Catches any `-u` that adds or modifies fields, even without a manifest version bump.
2. **XML-ID fingerprint** — md5 hash of all `ir_model_data` rows (`module.name=model:res_id`). Catches additions or removals of XML data records (e.g., `noupdate="1"` records) that don't alter the schema.
3. **Module-version fingerprint** — md5 hash of installed `ir_module_module.name=latest_version`. Catches a value-only migration: the schema and XML-ID set stay the same, but `-u` writes a new `latest_version`, so clones refresh.

The XML-ID fingerprint was added because schema-only fingerprinting missed a common scenario: adding `noupdate="1"` data records (like Knowledge articles) during `-u` doesn't change any table columns, so stale clones were reused — causing `env.ref()` lookups in parallel workers to fail with missing records.

The module-version fingerprint was added because a value-only migration (rewrite a field on an existing row, bump the manifest, `-u`) moved neither of the first two hashes. Symptom: the assertion passed standalone and failed under `--test-tags`. A version bump now misses the cache on its own. A `-u` that rewrites values **without** a version bump still needs `ODOO_TEST_REUSE_CLONES=false` (or `dropdb` the `{db}-worker-*` databases).

**The worker count is not part of the key (since 2026-09-04).** A run needing N clones reuses the first N valid pool clones and creates only the missing ones; a run with fewer classes than the regular suite (fewer workers) creates nothing, and a run with more workers adds to the pool. Before, the count was in the key, so a subset run rebuilt all 19 clones (60 s under the old copy strategy) and the next regular run rebuilt them again. When the fingerprints do not match, the whole pool is dropped and rebuilt. Clone state (the three fingerprints and the pool's database names) is persisted to `/tmp/odoo_parallel_test_clones.json`; the log reports the specific reason for a miss (e.g., "Clone cache miss — module versions changed").

**Copy strategy (since 2026-09-04).** On PostgreSQL 15+ a clone is created with `CREATE DATABASE ... STRATEGY = FILE_COPY`. The default `WAL_LOG` strategy writes every block of the template through the WAL: alone a 115 MB clone took 3.1 s, six concurrent clones 9.9 s, and the 19-clone rebuild 60 s. `FILE_COPY` copies the files: 1.3 s alone, 1.5 s for six concurrent. Older servers keep `createdb -T`. Odoo.sh is unaffected either way, because the runner falls back to sequential there.

**A state file written before the module-version fingerprint existed misses once, and blames the wrong thing.** An older `/tmp/odoo_parallel_test_clones.json` has no `version_fingerprint` key, so the saved value reads as `None` and can never match. The run therefore rebuilds clones one time and logs "module versions changed" even though nothing was updated. Do not go hunting a phantom `-u`: the next run writes the key and reuse resumes.

### Worker filestore

Odoo resolves a database's filestore from the database name (`data_dir/filestore/<db>`), and `createdb -T` copies rows, not files. So a clone's `ir_attachment` rows pointed at files that existed only under the base DB's filestore, and a worker started with an empty one. Since 2026-09-04 `cloner.replicate_filestore` gives every fresh clone a replica of the base filestore: each file is **hardlinked** (metadata only, no bytes copied, so a multi-GB filestore costs nothing), with a plain copy as the fallback when a link is refused. The GC `checklist/` markers are not replicated. A complete replica carries the marker file `.hh-filestore-replica`. The replica is removed with the clone in `drop_databases`. A reused clone keeps its own filestore between runs; a reused clone without the marker (one from before replication existed, or a replica removed by hand) is topped up from the base in merge mode, which keeps the clone's own files.

Hardlinks are safe against Odoo's end-of-run filestore GC on a worker: unlinking the worker's link never touches the base DB's file. That is why the replica is hardlinks and not a symlinked directory, which would let a worker's GC delete files the base DB still references.

The practical effect: `HttpCase.start_tour` browser tests and tests that render install-time fixtures run inside the regular parallel suite. Unit coverage: `odoo_parallel_tests/tests/test_cloner_filestore.py`.

## Configuration

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `ODOO_TEST_WORKERS` | `min(32, max(2, cores))` | Number of parallel workers (capped at the number of test classes). With the queue, more workers than cores only add contention (see Performance) |
| `ODOO_TEST_PARALLEL` | `auto` | `auto` (if >1 class), `always`, `never` |
| `ODOO_TEST_CLONE_PREFIX` | `{db}-worker-` | Clone database name pattern |
| `ODOO_TEST_REUSE_CLONES` | `true` | Keep clone DBs between runs and reuse when schema + XML IDs + module versions unchanged |
| `ODOO_TEST_STATS_FILE` | `/tmp/odoo_parallel_test_stats.json` | Path to the per-class duration stats file (queue order) |

## Performance

Measured 2026-09-04 on a 16-core Apple Silicon laptop, the full regular tag set (`oteny_audit,riverflow,rivercreds,crewradar`, 4404 tests, 490 classes), clones reused from the pool (0.1 s):

| Runner | Workers | Wall | Worker finish spread | Test work (sum over workers) |
|--------|---------|------|----------------------|------------------------------|
| Static LPT batches (before) | 19 | 98.9 s | 42.8 s | 1201 s |
| Work queue | 19 | 108.8 s | 0.1 s | 1852 s |
| Work queue | 12 | 71.1 s | 0.1 s | 779 s |
| Work queue | 14 | 82.6 s | 0.1 s | 1033 s |
| Work queue | **16 (= cores, the default)** | **65.8 s and 69.7 s** (two runs) | 0.2 s | 930 s |

One 12-worker run measured 107.8 s with worker boots of 12 to 22 s, against 5 s in its repeat: something else was loading the machine, so single runs are noisy at the 10 s level and the 12 and 16 rows are the repeats. Two lessons in that table. The queue removes the imbalance completely: every worker finishes within a fraction of a second of the others. And a worker count above the core count is a net loss once the queue keeps every worker busy: with 19 workers the test work itself inflated by 54% (the biggest class, `TestSeedMfnlFixtures`, ran 92 s instead of 52 s) and boot stretched to 18 s. Static batches had masked that, because early finishers thinned the load over the last third of the run. Worker boot (registry load) is 6 to 8 s and runs in parallel.

A clone-cache miss (after `-u`) costs about 1.5 s for 16 clones with `FILE_COPY` on PostgreSQL 15+; it cost 60 s with the previous strategy and count-keyed cache.

Older numbers, 903 crewradar tests on the same class of machine: sequential ~355 s, round-robin ~57 s, LPT batches ~51 s, LPT + clone reuse ~33 s.

## Fallback Behavior

If parallel execution fails for any reason (cloning error, worker crash), the runner falls back to sequential execution with an INFO-level log message. This ensures tests always run even if the parallel infrastructure has issues.

**Odoo.sh**: On Odoo.sh, the database user lacks `createdb` privileges, so cloning always fails. The runner detects this gracefully and falls back to sequential execution without producing ERROR-level log entries — only a single INFO line is logged.

## Single-Test Fast Path

When `--test-tags=.test_specific_method` targets a single method (or single class), the suite has ≤1 class, so the parallel machinery is skipped entirely. Zero overhead for targeted test runs.

## Troubleshooting

Many failures on **one** worker with others green usually means that worker pulled a test class with functional errors—not corrupt PostgreSQL clones. If worker db is the likely problem, check whether the **template database** (`-d`) was installed with the full module list from `.vscode/settings.json` (`odoo.installModules`), not a partial `-i` of a single addon. See [Testing Guidelines — Odoo test run issues](testing-guidelines.md#odoo-test-run-issues).

**Stale clones after a field-VALUE change without a version bump.** A value-only migration that **does** bump `latest_version` now invalidates clones via the module-version fingerprint. A `-u` that rewrites a field (or a pure M2M row such as `implied_ids`) **without** a version bump still matches the reuse key. Symptom: the assertion passes in `odoo shell` on `cr-test` and fails under `--test-tags`. Force a re-clone with `ODOO_TEST_REUSE_CLONES=false`, or drop the worker DBs — `for db in $(psql -lqt | awk '{print $1}' | grep '^cr-test-worker-'); do dropdb --if-exists "$db"; done` — or delete `/tmp/odoo_parallel_test_clones.json`. Hit first on a value-only SLA rewrite (`19.0.6.39`) and on `hr.group_hr_user → oteny_bot.group_oteny_bot_operator` (2026-08).

### `FileNotFoundError` reading an install-time fixture on a worker (empty worker filestore)

**Fixed 2026-09-04.** Every fresh clone now receives a replica of the base DB's filestore (see *Worker filestore* above), so an installed fixture PDF, an asset bundle, and a browser test all work on a worker. The symptom before the fix: a test rendering an install-time fixture failed on a worker with `FileNotFoundError: .../filestore/cr-test-worker-N/<xx>/<checksum>` while passing on the main `cr-test` DB, and an `HttpCase.start_tour` test never became ready ("The ready code was always falsy") because `/web/assets/...` 500'd. The per-test guard from that era (`crewradar_cuneus_sign/tests/test_wp_ab_visit_documents.py::_bundle_fixture_files_present`, which `skipTest`s when the fixture bytes are absent) is still there and now simply never skips; it is harmless and can go when that file is next touched.

If the symptom comes back, the replica is missing: `ls "$(python3 -c 'import odoo.tools; print(odoo.tools.config.filestore("cr-test-worker-0"))')"` should list the same `xx/` directories as `cr-test`. A replica removed by hand is rebuilt on the next run even when the clone databases are reused.

## Planned: extraction into a shared repository

This module is being extracted into its own repository,
`otenycom/oteny_odoo_parallel_test`, so HermesHost can run the same
clone-per-worker mechanism for its own Odoo test suite instead of a second,
divergent copy. HermesHost adopts the extracted module first. Once its own
suite is green on it, crewradar migrates from this in-tree module to the
same shared repository as a git submodule, and this directory is removed.

The one code change the extraction needs here: `runner.py`'s hardcoded
`base_port = 9000` becomes a `config.py` setting, alongside the existing
`ODOO_TEST_*` variables above, because two independent products can now run
this module at once on the same machine and need different port bands.
`ODOO_TEST_CLONE_PREFIX` already keys off `{db}`, so clone names need no
change — crewradar's and HermesHost's base database names already differ.

Full plan, with the exact command sequence for both repos, lives with the
platform's own planning docs.

## Related

- [Testing Guidelines](testing-guidelines.md) — sequential test commands and patterns
- `.cursorrules` — test command templates
