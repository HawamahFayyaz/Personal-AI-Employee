# Skill: Audit Logging

## Purpose

Every action the AI Employee takes is written as a structured JSON entry to
the daily log file at `Logs/YYYY-MM-DD.json`.  These logs form the audit
trail for compliance, debugging, and the CEO briefing.

Use this skill when you need to:
- Record that an action happened (email sent, task processed, post published)
- Time a block of work automatically
- Query what happened on a given day or week
- Investigate errors across a date range
- Archive or prune old log files

---

## Trigger phrases

- "Log this action"
- "Record that [X] happened"
- "What did the AI do today / this week?"
- "Search logs for [query]"
- "Archive old logs"
- Any watcher, dispatcher, or scheduler completing an operation

---

## Import

```python
from scripts.audit_logger import log_action, audit
from scripts.audit_logger import get_daily_summary, get_weekly_summary, search_logs
from scripts.audit_logger import rotate_logs, prune_archive
```

---

## Log schema

Every entry is one JSON object appended to `Logs/YYYY-MM-DD.json`:

```json
{
  "timestamp":       "2026-04-10T13:00:00.000000+00:00",
  "action_type":     "gmail_check",
  "actor":           "watcher",
  "target":          "inbox",
  "parameters":      {"query": "is:unread is:important"},
  "approval_status": "auto",
  "approved_by":     "auto",
  "result":          "success",
  "error_message":   null,
  "duration_ms":     142
}
```

| Field             | Values                                                    |
|-------------------|-----------------------------------------------------------|
| `actor`           | `claude_code` `watcher` `mcp_server` `scheduler` `watchdog` |
| `result`          | `success` `failure` `error` `dry_run` `skipped` `pending` |
| `approval_status` | `auto` `approved` `pending` `rejected`                    |
| `approved_by`     | `human` `auto` `null`                                     |

Extra fields from older log entries (e.g. `summary`, `task_id`) are preserved
when reading; new entries add only the fields above.

---

## Writing entries

### Fire-and-forget

```python
log_action(
    action_type="gmail_check",
    actor="watcher",
    target="inbox",
    params={"query": "is:unread is:important"},
    result="success",
)
```

### Auto-timed context manager

Measures wall-clock time; sets `result="error"` automatically if the block
raises an unhandled exception.

```python
with audit("email_send", actor="mcp_server", target="alice@example.com") as a:
    send_email(payload)
    a.set_result("success")
# entry written with measured duration_ms
```

If the block raises:

```python
with audit("odoo_write", actor="watcher", target="sale.order") as a:
    client.create(vals)           # raises on timeout
    a.set_result("success")
# entry written: result="error", error_message="<exc>", duration_ms=<ms>
```

### Approval-gated action

```python
with audit(
    "linkedin_post",
    actor="mcp_server",
    target="company_page",
    approval_status="approved",
    approved_by="human",
) as a:
    linkedin_api.post(content)
    a.set_result("success")
```

### Extra fields

Any `**kwargs` are merged into the entry verbatim:

```python
log_action(
    action_type="ralph_loop",
    actor="claude_code",
    target="task_queue",
    result="success",
    task_id="20260410_130013_DC0092",
    iterations_used=3,
)
```

---

## Querying logs

### Daily summary

```python
from datetime import date

summary = get_daily_summary()                       # today
summary = get_daily_summary(date(2026, 4, 10))      # specific date
```

Returns:
```json
{
  "date":            "2026-04-10",
  "total_actions":   42,
  "by_actor":        {"watcher": 30, "claude_code": 12},
  "by_result":       {"success": 38, "error": 4},
  "by_type":         {"gmail_check": 24, "email_send": 8, ...},
  "error_rate":      0.0952,
  "errors":          [...],
  "avg_duration_ms": 312.4
}
```

### Weekly summary

```python
summary = get_weekly_summary()                           # last 7 days
summary = get_weekly_summary(date(2026,4,7), date(2026,4,13))
```

Returns totals + `"daily_counts"` and `"busiest_day"` in addition to the
daily fields.

### Search

```python
# Free-text across all string fields
hits = search_logs(query="gmail")

# Structured filters (can combine)
hits = search_logs(
    action_type = "email_send",
    actor       = "mcp_server",
    result      = "error",
    start_date  = date(2026, 4, 1),
    end_date    = date(2026, 4, 10),
    limit       = 50,
)
```

Returns a list of matching entries, newest-first, capped at `limit`.

---

## Retention and rotation

### Archive (after 90 days)

```python
archived = rotate_logs()           # default 90-day retention
archived = rotate_logs(days=30)    # custom retention
```

Files older than the cutoff are moved from `Logs/` to `Logs/Archive/`.

### Prune archive (after 365 days)

```python
deleted = prune_archive(max_age_days=365)
```

Permanently deletes archived files beyond the max age.

### Scheduled rotation

Add to the scheduler (in `Config/schedule.md`):

```yaml
- id: log_rotation
  schedule: "0 3 * * 0"           # every Sunday at 03:00 UTC
  command: python scripts/audit_logger.py rotate --days 90
```

---

## CLI inspector

```bash
# Daily summary (today)
python scripts/audit_logger.py summary

# Specific date
python scripts/audit_logger.py summary --date 2026-04-10

# Last 7-day summary
python scripts/audit_logger.py summary --week

# Free-text search
python scripts/audit_logger.py search gmail

# Structured search
python scripts/audit_logger.py search --action-type email_send --result error --limit 10

# Archive logs older than 90 days
python scripts/audit_logger.py rotate --days 90
```

---

## Thread and process safety

`audit_logger.py` uses two layers of locking:

1. **`threading.Lock` per calendar date** — serialises concurrent watcher
   threads within the same Python process.
2. **`fcntl.flock(LOCK_EX)`** — serialises concurrent OS processes
   (watchdog, scheduler, watchers) writing to the same file.

Both are released automatically; callers need no special handling.

---

## File layout

```
Logs/
  2026-04-10.json        ← today's entries (JSON array)
  2026-04-09.json
  ...
  Archive/
    2026-01-10.json      ← rotated files (> 90 days old)
    ...
  alerts.json            ← error_handler alerts (separate)
  health_status.json     ← watchdog health (separate)
```

---

## Integration pattern for new scripts

```python
from scripts.audit_logger import log_action, audit

# At the start of any non-trivial operation:
with audit("my_operation", actor="watcher", target="some_resource") as a:
    result = do_the_work()
    a.set_result("success", )
    a.set_extra(items_processed=len(result))
```

This single pattern covers timing, error capture, and audit trail in one call.
