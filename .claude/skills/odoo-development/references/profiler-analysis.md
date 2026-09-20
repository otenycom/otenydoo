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
