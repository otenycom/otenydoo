# Odoo Shell

Run Python code snippets against a live Odoo database without going through the UI. Useful for AI agents doing quick checks, inserting records, measuring performance, or debugging data on the local dev instance.

## How It Works

Odoo's `shell` command starts the ORM, connects to a database, and provides a fully initialized `env` (Environment). When stdin is not a TTY (i.e., code is piped), it `exec()`s the piped code and exits — no interactive prompt needed.

**Available variables** in the shell context:

| Variable | Value |
|----------|-------|
| `env` | `odoo.api.Environment` (superuser, with user context) |
| `self` | `env.user` (OdooBot / admin) |
| `odoo` | The `odoo` package |

**Transaction behavior**: The shell wraps execution in a cursor context and **rolls back** when the script finishes. Nothing is committed unless the script explicitly calls `env.cr.commit()`.

## Base Command

```bash
cd <business-repo> && echo "<python code>" | \
  ../../odoo/venv/bin/python3 ../../odoo/odoo19/odoo-bin shell \
  --addons-path=../../odoo/odoo19/addons,/Users/ries/odoo/enterprise19,<business-repo> \
  -d crmain --no-http --max-cron-threads 0 --log-level=info 2>&1
```

Key flags:

- `-d crmain` — target the local dev database (change as needed)
- `--no-http` — skip starting the HTTP server (faster startup)
- `--max-cron-threads 0` — don't start cron workers
- `--log-level=info` — suppress info-level ORM chatter; use `--log-level=test` for more detail
- Pipe `2>&1` to capture both stdout and stderr

Startup takes ~1.2 seconds on a typical dev machine.

## Examples

### Read records

```bash
echo "
employees = env['hr.employee'].search([], limit=5)
for emp in employees:
    print(f'{emp.id}: {emp.name}')
print(f'Total: {env[\"hr.employee\"].search_count([])}')
" | ../../odoo/venv/bin/python3 ../../odoo/odoo19/odoo-bin shell \
  --addons-path=../../odoo/odoo19/addons,/Users/ries/odoo/enterprise19,<business-repo> \
  -d crmain --no-http --max-cron-threads 0 --log-level=info 2>&1
```

### Create a record (persistent)

By default the shell rolls back. Call `env.cr.commit()` to persist changes.

```bash
echo "
partner = env['res.partner'].create({'name': 'Test Partner from Shell'})
env.cr.commit()
print(f'Created partner id={partner.id}')
" | ../../odoo/venv/bin/python3 ../../odoo/odoo19/odoo-bin shell \
  --addons-path=../../odoo/odoo19/addons,/Users/ries/odoo/enterprise19,<business-repo> \
  -d crmain --no-http --max-cron-threads 0 --log-level=info 2>&1
```

### Measure method performance

```bash
echo "
import time
start = time.time()
entries = env['crewradar.log.entry'].search([('active', '=', True)], limit=500)
elapsed = time.time() - start
print(f'Fetched {len(entries)} entries in {elapsed:.3f}s')
" | ../../odoo/venv/bin/python3 ../../odoo/odoo19/odoo-bin shell \
  --addons-path=../../odoo/odoo19/addons,/Users/ries/odoo/enterprise19,<business-repo> \
  -d crmain --no-http --max-cron-threads 0 --log-level=info 2>&1
```

### Run with heredoc (for complex multi-line scripts)

```bash
cd <business-repo> && ../../odoo/venv/bin/python3 ../../odoo/odoo19/odoo-bin shell \
  --addons-path=../../odoo/odoo19/addons,/Users/ries/odoo/enterprise19,<business-repo> \
  -d crmain --no-http --max-cron-threads 0 --log-level=info 2>&1 <<'PYEOF'
# Complex script with imports and multi-step logic
from collections import Counter

sites = env['crewradar.site'].search([('active', '=', True)])
country_counts = Counter(s.country_id.name for s in sites if s.country_id)
for country, count in country_counts.most_common(10):
    print(f'{country}: {count} sites')
PYEOF
```

### Capture output to file for analysis

Append `| tee /tmp/shell_output.txt` to save output while still displaying it:

```bash
echo "print(env['res.company'].search([]).mapped('name'))" | \
  ../../odoo/venv/bin/python3 ../../odoo/odoo19/odoo-bin shell \
  --addons-path=../../odoo/odoo19/addons,/Users/ries/odoo/enterprise19,<business-repo> \
  -d crmain --no-http --max-cron-threads 0 --log-level=info 2>&1 | tee /tmp/shell_output.txt
```

## AI Agent Guidelines

- **Prefer shell over UI testing**: Browser-based UI testing is slow, fragile, and requires explicit user approval before each session. The shell is the preferred way for AI agents to inspect data, verify business logic, and measure performance.
- **Read-only queries are always OK**: Since the shell auto-rolls back, read-only queries (search, read, browse, mapped, filtered) against `crmain` are safe and don't need user approval. The `crmain` database is a local backup of production data, so it contains realistic data for verification.
- **Ask before writing**: Any shell command that mutates data (`create`, `write`, `unlink`, `env.cr.commit()`) requires user approval first. Explain what records will be changed and why.
- **Never run shell against production**: The shell is only for local databases (`crmain`, `cr-test`, etc.). There is no production database accessible from the dev machine.

## Tips

- **Read-only by default**: The auto-rollback makes shell safe for exploratory queries — nothing changes unless you explicitly commit.
- **Omit tail/grep on first run**: Capture the full output to a file with `| tee`, then grep or tail the file separately. This avoids losing error context.
- **Superuser context**: The shell runs as SUPERUSER_ID, so access rules don't apply. Keep this in mind when testing security-sensitive logic.
- **No HTTP server running**: The `--no-http` flag means no web controllers or assets are available. The shell is purely ORM and database.
- **Different database**: Change `-d crmain` to target another database (e.g., `-d cr-test`).
- **`--shell-file` flag**: Only works in interactive (TTY) mode as a startup script for the REPL. Not useful for piped/automated execution.
