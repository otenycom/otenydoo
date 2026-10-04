# Odoo Profiler Analysis

Analyze server-side performance using Odoo's built-in profiler and the `ir_profile` table. Profiler traces are recorded when the profiler is enabled in the debug menu and are stored in the database as `ir_profile` records, viewable at `/web/speedscope/<id>`.

## Enabling the Profiler

1. Navigate with `?debug=assets` in the URL
2. Open the debug menu (bug icon)
3. Enable "Record Profiling" to start capturing
4. Perform the action to profile (e.g., create an employee, open a form)
5. Disable profiling
6. View recorded profiles at **Settings > Technical > Database Structure > Profiling**

## Analyzing via `ir_profile` Table

Profiler data is stored in `ir_profile` with these key columns:

| Column | Content |
|--------|---------|
| `id` | Profile ID (used in `/web/speedscope/<id>` URL) |
| `name` | Request URL that was profiled |
| `duration` | Total wall-clock seconds |
| `cpu_duration` | CPU time in seconds |
| `sql_count` | Number of SQL queries executed |
| `entry_count` | Total profiler entries |
| `sql` | JSON array of all SQL queries with timing, full_query, and stack traces |
| `traces_sync` | JSON with synchronous execution traces |
| `traces_async` | JSON with asynchronous execution traces |

### Step 1: List Recent Profiles

```sql
SELECT id, name, duration, sql_count, create_date
FROM ir_profile ORDER BY id DESC LIMIT 10;
```

### Step 2: Extract and Analyze SQL Queries

The `sql` column is a JSON array. Each entry has: `query`, `full_query`, `time`, `stack`, `exec_context`, `start`.

Export to file, then analyze with Python:

```sql
-- Export to file
\copy (SELECT sql FROM ir_profile WHERE id = <ID>) TO '/tmp/profile_sql.json'
```

```python
import json
from collections import defaultdict

data = json.load(open('/tmp/profile_sql.json'))
print(f'Total SQL entries: {len(data)}')

# Group by query pattern and sum time
query_times = defaultdict(lambda: {'count': 0, 'total_time': 0.0})
for entry in data:
    key = entry.get('query', '')[:120].strip()
    query_times[key]['count'] += 1
    query_times[key]['total_time'] += entry.get('time', 0)

sorted_queries = sorted(query_times.items(), key=lambda x: -x[1]['total_time'])
print('Top 20 queries by total time:')
for q, info in sorted_queries[:20]:
    print(f'  {info["total_time"]*1000:.1f}ms ({info["count"]}x) - {q}')
```

### Step 3: Group Queries by Call Stack Origin

The `stack` field in each SQL entry is a list of `[file, line, function, code_snippet]` tuples. Filter to project-specific frames to find which code path generated the most queries:

```python
stack_sigs = {}
for entry in data:
    q = entry.get('query', '')
    if 'target_table' not in q:  # filter to queries of interest
        continue
    stack = entry.get('stack', [])
    our_lines = [l for l in stack if '/<business-repo>/' in str(l[0])]
    sig = str([(str(l[0]).split('<business-repo>/')[-1], l[1], l[2]) for l in our_lines[:3]])
    if sig not in stack_sigs:
        stack_sigs[sig] = {'count': 0, 'time': 0.0, 'full_stack': our_lines}
    stack_sigs[sig]['count'] += 1
    stack_sigs[sig]['time'] += entry.get('time', 0)

for sig, info in sorted(stack_sigs.items(), key=lambda x: -x[1]['count'])[:10]:
    print(f"\n  {info['count']:5d}x  {info['time']*1000:8.1f}ms")
    for l in info['full_stack']:
        print(f"    {str(l[0]).split('<business-repo>/')[-1]}:{l[1]} {l[2]}")
```

### Step 4: Summarize by Table

```python
from collections import defaultdict
table_stats = defaultdict(lambda: {'count': 0, 'total_time': 0.0})
for entry in data:
    q = entry.get('query', '').strip().upper()
    if q.startswith('SELECT'):
        table = q.split('FROM')[1].strip().split()[0].strip('"') if 'FROM' in q else '?'
    elif q.startswith('INSERT'):
        table = q.split('INTO')[1].strip().split()[0].strip('"') if 'INTO' in q else '?'
    else:
        continue
    table_stats[table]['count'] += 1
    table_stats[table]['total_time'] += entry.get('time', 0)

for t, info in sorted(table_stats.items(), key=lambda x: -x[1]['total_time'])[:20]:
    print(f'  {info["count"]:5d}x  {info["total_time"]*1000:8.1f}ms  {t}')
```

### Analyzing a sampling-only profile (`traces_async`)

A preload (`-i`/`-u`) profile of a large migration is captured **sampling-only** — the SQL collector crashes the row INSERT on high query volumes (see [/profile](../../../commands/profile.md)), so `sql` is empty (`sql_count = 0`) and the data lives in `traces_async`. It is a JSON array of samples, each `{"stack": [[file, line, func, code], ...], "start": <ts>, "exec_context": ...}`, where `stack[0]` is the outermost frame and `stack[-1]` the innermost. Weight each sample by the interval (or the delta between consecutive `start` values).

Two aggregations locate the bottleneck:

- **Innermost frame (self time)** — where the thread actually sits. For a DB-bound update this is overwhelmingly `sql_db.py:execute`; a high share there with `cpu_duration ≪ duration` confirms the run is *waiting on the database* (an N+1), not CPU-bound.
- **Deepest frame under the project's own path (our self time)** — the project function directly driving each sample. Because the sampler captures the calling stack while a thread blocks in `execute()`, DB-wait time is attributed to the migration/compute that issued the query — so this ranking points straight at the slow code path.

```python
import json, re
from collections import Counter

d = json.load(open('/tmp/traces_async.json'))   # psql -tA -c "SELECT traces_async FROM ir_profile WHERE id=<ID>"
W = (d[-1]['start'] - d[0]['start']) / max(len(d) - 1, 1)   # avg interval as per-sample weight
self_leaf, project_leaf, mig = Counter(), Counter(), Counter()
for s in d:
    st = s.get('stack')
    if not st:
        continue
    self_leaf[st[-1][2]] += W                                        # innermost func
    project = [f for f in st if '/<business-repo>/' in f[0]]
    if project:
        project_leaf[f"{project[-1][2]} ({project[-1][0].split('<business-repo>/')[-1]})"] += W
    for f in st:                                                     # which migration is on-stack
        m = re.search(r'migrations/(\d+\.\d+\.\d+\.\d+)/', f[0])
        if m:
            mig[m.group(1)] += W
            break
for label, c in (('SELF LEAF', self_leaf), ('DEEPEST PROJECT FRAME', project_leaf), ('BY MIGRATION', mig)):
    print(f'\n== {label} ==')
    for k, v in c.most_common(15):
        print(f'  {v:7.1f}s  {k}')
```

## Common Performance Patterns

### N+1 Query Problem

Symptom: thousands of `SELECT ... WHERE id IN %s` queries on the same table. Caused by iterating a recordset and accessing fields one at a time while the ORM cache is empty or invalidated.

Fix: prefetch fields before iteration, or use `search()` / `read()` instead of per-record access. Avoid `invalidate_all()` inside loops.

#### Recordset operations inside a per-record loop drop the prefetch set

In Odoo 19, `filtered()`, `|` / `union()`, indexing and slicing (`records[0]`, `records[:1]`) and `browse()` build a **new** recordset whose prefetch set is only its own ids (`browse(ids)` → `self.__class__(env, ids, ids)`). `sorted()`, `with_prefetch()` and plain iteration keep the parent's prefetch set. Iterating `slot.item_ids` directly keeps the batch: the x2many value prefetches across every slot being processed. `slot.item_ids.filtered("active")` does not, so every field read on those items afterwards is one query **per slot**.

The trap is easy to fall into because the workspace rule requires an explicit `active` check (see [SKILL.md — Active Records and Archiving](../SKILL.md#active-records-and-archiving)). Inside a loop over many records, check `active` in the loop body instead of calling `filtered()` per record:

```python
# N+1: filtered() gives each slot's items a prefetch set of their own
for slot in self:
    for item in slot.item_ids.filtered("active"):
        services |= item.credential_service_ids

# Batched: one query per relation for all slots
for slot in self:
    for item in slot.item_ids:
        if not item.active:
            continue
        services |= item.credential_service_ids
```

When the loop builds a per-record recordset with `|=` and a helper then reads fields on it, collect the sets first and give them one shared prefetch set: `services.with_prefetch(all_services._prefetch_ids)`, where `all_services = Model.union(*sets)`.

Workspace case (2026-10-04): the non-stored `rivercreds.plan.slot.service_infos_json` ran 1,304 queries for 559 ship slots on a prod copy, so the Credential Planning page waited about 1 s on one `search_read`. With both fixes it runs 7 queries; the full `search_read` went from 0.384 s and 1,312 queries to 0.053 s and 15 queries. Find the repeated query by wrapping `odoo.sql_db.Cursor.execute` in an `odoo shell` and counting the normalized SQL text with its first call stack; print the length of the id list in the params to see whether each query loads one record or a batch.

### Calling a compute method directly writes every assignment

Inside Odoo's own recompute, assigning a stored field its current value costs nothing. A compute method called **directly** (`records._compute_x()`, often after `invalidate_recordset`) runs outside the compute framework: each assignment is a full `write()` that marks the dependents and recomputes them, even when the value does not change. After an invalidation the ORM cannot even see that the value is unchanged. Compare first and assign only on change.

Workspace case (2026-10-04): `crewradar.log.entry._compute_most_fitting_marker_trigger` re-resolves the employee's marker services with `_compute_subject()`. Each re-resolve rewrote `res_model`/`res_id` and recomputed state records, check results, the HR info service and `display_order`: about 0.5 s and 500 queries per marker, while 0 of 257 markers on `crmain` changed target. Assigning only on change in `_apply_custom_subject_from` took all 257 markers from 164.5 s to 0.08 s. The same pattern shows in a compute that writes **another** record unconditionally (`info.write(vals)`, `pax.info_service_id.write(vals)`); measure it before changing it, because a small, rare batch costs little.

### A many2one used in a dependency path needs an index

When field B on model M depends on `x_id.field`, every write to `field` makes Odoo search `M` for `x_id in (...)` to find the records to recompute. A many2one with `ondelete` also gets a reference check on every delete. Without an index on `x_id`, both are sequential scans. A many2one in `_order` adds a join to every `search()` without an explicit `order`. Find these in a measured workload by logging queries slower than 3 ms with their caller, then test the index inside the shell transaction (`CREATE INDEX` is transactional in PostgreSQL, so a rollback removes it) before declaring it. Choose `index="btree_not_null"` when most rows are empty. Workspace case: `riverflow_service.root_id`, `(res_model, res_id)` and `wp_info_for_id` on 35k services, see [riverflow SKILL — Indexes](../../riverflow/SKILL.md#services).

### Scanning every stored compute for N+1

To find N+1 computes across all modules, compute each stored field once for 1 record and once for a batch (`field.compute_value(records)` with a cold cache, `env.cr.rollback()` after each) and compare the query counts. Growth per record flags a candidate; it does not prove a problem. Then measure the realistic trigger **with `env.flush_all()`**, because the follow-on recomputes run at flush and were 5–10 times the compute's own cost in the workspace case. Rank by real user actions and real batch triggers (a dependency on a shared record recomputes every record at once).

**Block the network first, and know that a Python block is not enough.** A stored compute may call an external service. `crewradar.site.marinetraffic_url` runs a web metasearch through `ddgs`, which makes its requests in Rust (`primp`), so patching `requests`, `httpx` or `socket` does not stop it. A 2026-10-04 scan on `crmain` sent real MMSI searches before it was stopped. Disable such clients at module level (`crewradar_marinetraffic.models.crewradar_site.DDGS = None`) and skip their fields, and watch the scan for a stall: an idle database connection with a waiting Python process means an outside call.

### `invalidate_all(flush=True)` in Loops

Wipes the entire ORM cache, forcing every subsequent field access to re-query. Measured impact: 18x more queries and 39x slower than letting the ORM batch naturally.

### Cascading Stored Compute Dependencies

When a stored computed field has broad dependencies (e.g., `company_ids.employee_ids.field`), changing one record triggers recomputation for all records in the dependency path. Fix: layer an intermediate stored field closer to the source record, so only the changed record recomputes at the first level.

## Measuring with Odoo Shell

For quick before/after comparisons without the UI profiler, use `env.cr.sql_log_count`:

```python
cr = env.cr
start_count = cr.sql_log_count
start_time = time.time()

# ... operation to measure ...

elapsed = time.time() - start_time
query_count = cr.sql_log_count - start_count
print(f"Time: {elapsed:.3f}s, SQL queries: {query_count}")
```

See [Odoo Shell](odoo-shell.md) for the full shell command template.
