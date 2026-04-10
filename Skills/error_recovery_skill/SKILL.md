# Skill: Error Recovery

## Purpose

This skill governs how the AI Employee handles, retries, and recovers from
errors across all external integrations. It ensures that failures in one
service never cascade into silent data loss or stuck queues.

Use this skill when:
- An external API call fails and you need to decide whether to retry or degrade
- A process has crashed and needs to be restarted or escalated
- Data arrives malformed and must be quarantined
- A service is unavailable and operations must continue in degraded mode

---

## Trigger phrases

- "Handle this error"
- "API call failed — what now?"
- "Gmail / Odoo / Twitter is down"
- "Process crashed"
- "Retry with backoff"
- "Queue for human review"
- "Quarantine this file"
- Any watcher or dispatcher catching an exception

---

## Error categories

| Category  | Trigger                                | Response                                          |
|-----------|----------------------------------------|---------------------------------------------------|
| TRANSIENT | Network error, timeout, rate limit     | Retry up to 3× with exponential backoff (2s, 4s, 8s) |
| AUTH      | 401 / 403, credential expired          | Alert human immediately, pause that integration   |
| LOGIC     | Unexpected code exception, bad state   | Write to `Review_Queue/`, alert human             |
| DATA      | Malformed input, parse failure         | Move file to `Quarantine/` with reason sidecar    |

---

## Retry logic (`scripts/retry_handler.py`)

### Exponential backoff (TRANSIENT)

```python
from scripts.retry_handler import call_with_retry, ErrorCategory

result = call_with_retry(
    fn=my_api_call,
    category=ErrorCategory.TRANSIENT,
    operation="gmail_send",
    max_attempts=3,   # delays: 2s, 4s, 8s
)
```

Or as a decorator:

```python
from scripts.retry_handler import with_retry, ErrorCategory

@with_retry(category=ErrorCategory.TRANSIENT, max_attempts=3)
def poll_twitter_mentions(): ...
```

After 3 failures a `RetryExhausted` exception is raised and an entry is
appended to `Logs/alerts.json`.

### Auth errors

```python
@with_retry(category=ErrorCategory.AUTH)
def refresh_linkedin_token(): ...
```

Any exception is wrapped as `AuthError`, an alert is written, and the caller
must pause that integration until a human resolves it.

### Logic errors

```python
@with_retry(category=ErrorCategory.LOGIC)
def process_task_file(path): ...
```

Exception is written to `Review_Queue/<timestamp>_<operation>.json` and
wrapped as `LogicError`.

### Data errors

```python
from pathlib import Path
from scripts.retry_handler import call_with_retry, ErrorCategory

call_with_retry(
    fn=parse_email,
    category=ErrorCategory.DATA,
    quarantine_path=Path("Inbox/malformed.md"),
)
```

The file is moved to `Quarantine/<timestamp>_<name>` with a `.reason` sidecar,
and a `DataError` is raised.

---

## Graceful degradation rules

### Gmail API unavailable
```python
from scripts.error_handler import gmail_send

# Returns False and queues email in Logs/email_queue/ if API fails
success = gmail_send(service=svc, payload=msg_payload, fallback_to_queue=True)
```
- Queued emails are stored in `Logs/email_queue/*.json`
- Re-send them manually once Gmail recovers: iterate the queue and call
  `gmail_send` again

### Odoo unavailable
```python
from scripts.error_handler import odoo_write

# Returns Path to markdown file in Logs/odoo_queue/ if Odoo fails
odoo_write(client=client, model="sale.order", method="create", args=[vals])
```
- Queued transactions are in `Logs/odoo_queue/*.md`
- Sync them once Odoo recovers

### Claude Code unavailable
- Watchers continue running; events accumulate in `Needs_Action/`
- No auto-processing occurs — the queue grows
- On recovery, run the reasoning loop: "Run the reasoning loop"

### Obsidian vault locked
```python
from scripts.error_handler import vault_write

# Writes to /tmp/ai_employee_vault_buffer/<filename> if vault is locked
path = vault_write("Briefings/today.md", content)
```
- Files land in `/tmp/ai_employee_vault_buffer/`
- Copy them back when the vault is accessible again

---

## Watchdog (`scripts/watchdog.py`)

The watchdog runs as a **separate process** from the watchers so it can
restart the entire watcher suite if it crashes.

### Start the watchdog

```bash
python scripts/watchdog.py
python scripts/watchdog.py --dry-run   # log restarts without launching
```

### What it manages

| Process       | Command                              |
|---------------|--------------------------------------|
| WatcherRunner | `Watchers/run_watchers.py`           |
| Scheduler     | `scheduler.py`                       |

### Crash/restart behavior

1. Detects crash (process exit code ≠ None)
2. Logs to `Logs/restart_log.json`
3. Waits 5 seconds (cooldown)
4. Restarts the process
5. If crash count reaches **3 consecutive crashes**: writes `HUMAN_ALERT` to
   `Logs/alerts.json` and logs a CRITICAL message — but **continues restarting**
6. Crash counter resets on a successful restart

### Health status file

`Logs/health_status.json` is written every **60 seconds**:

```json
{
  "updated_at": "2026-04-10T13:00:00Z",
  "processes": {
    "WatcherRunner": {
      "status": "running",
      "pid": 12345,
      "crash_count": 0,
      "error_count": 0,
      "alerted": false,
      "last_start": "2026-04-10T12:00:00Z",
      "last_crash": null,
      "last_activity": "2026-04-10T12:59:55Z"
    }
  },
  "summary": {
    "total": 2,
    "running": 2,
    "crashed": 0
  }
}
```

---

## Logs produced by this skill

| File                       | Contents                                          |
|----------------------------|---------------------------------------------------|
| `Logs/health_status.json`  | Live process states, updated every 60s            |
| `Logs/alerts.json`         | All alerts (auth failures, repeated crashes, etc) |
| `Logs/restart_log.json`    | Timestamped log of every process restart          |
| `Logs/error_counts.json`   | Per-operation error tallies                       |
| `Logs/email_queue/`        | Emails queued when Gmail is down                  |
| `Logs/odoo_queue/`         | Odoo transactions queued when Odoo is down        |
| `Quarantine/`              | Files moved here due to DATA errors               |
| `Review_Queue/`            | LOGIC errors queued for human review              |

---

## Human escalation checklist

Check `Logs/alerts.json` for any of these `kind` values:

| kind                | Action required                                           |
|---------------------|-----------------------------------------------------------|
| `auth_failure`      | Rotate/refresh credentials for the named integration      |
| `transient_exhausted` | Investigate API or network issue; re-run queued items   |
| `repeated_crashes`  | Read `Logs/restart_log.json`; check process stderr log    |
| `logic_error`       | Read `Review_Queue/` files; fix the underlying code bug   |
| `data_error`        | Inspect `Quarantine/`; fix/resubmit or discard the file   |

---

## Module layout

```
scripts/
  error_handler.py   — public facade (import this in watchers/dispatchers)
  retry_handler.py   — retry logic, backoff, quarantine, queue helpers
  watchdog.py        — process watchdog + health status writer
```

Import pattern in watchers:

```python
from scripts.error_handler import handle_error, ErrorCategory, gmail_send
```
