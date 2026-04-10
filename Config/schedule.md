---
version: 1
timezone: UTC
tasks:
  - name: daily_dashboard_update
    description: Refresh Dashboard.md with current vault state
    schedule: daily
    time: "08:00"
    command: claude
    prompt_key: dashboard_update
    enabled: true

  - name: linkedin_post_generation
    description: Generate LinkedIn post draft for human approval (Mon/Wed/Fri)
    schedule: "monday,wednesday,friday"
    time: "09:00"
    command: python
    module: Watchers.linkedin_poster
    args: generate
    enabled: true

  - name: ceo_briefing
    description: Monday CEO briefing — gathers week data and generates briefing via Claude
    schedule: sunday
    time: "20:00"
    command: python
    module: scripts.generate_briefing
    args: ""
    enabled: true
---

# Schedule Configuration

This file is read by `scheduler.py` at startup and whenever `--reload` is passed.
Edit the YAML frontmatter above to add, remove, or adjust tasks.
Restart the scheduler (or send SIGHUP) for changes to take effect.

---

## Active Tasks

| Name | Schedule | Time (UTC) | Command | Status |
|---|---|---|---|---|
| `daily_dashboard_update` | Every day | 08:00 | Claude → update_dashboard skill | ✓ enabled |
| `linkedin_post_generation` | Mon / Wed / Fri | 09:00 | `linkedin_poster generate` | ✓ enabled |
| `ceo_briefing` | Every Sunday | 20:00 | `scripts.generate_briefing` | ✓ enabled |

---

## Schedule syntax

The `schedule` field accepts:

| Value | Meaning |
|---|---|
| `daily` | Every day at the specified `time` |
| `monday` | Every Monday |
| `tuesday` | Every Tuesday |
| `wednesday` | Every Wednesday |
| `thursday` | Every Thursday |
| `friday` | Every Friday |
| `saturday` | Every Saturday |
| `sunday` | Every Sunday |
| `monday,wednesday,friday` | Comma-separated list — registered as three separate jobs |
| `weekdays` | Alias for `monday,tuesday,wednesday,thursday,friday` |
| `weekends` | Alias for `saturday,sunday` |

The `time` field must be `"HH:MM"` in 24-hour UTC.

---

## Command types

### `command: claude` — invoke Claude Code CLI
Requires `prompt_key` which maps to a built-in prompt in `scheduler.py`.
Built-in keys: `dashboard_update`, `process_inbox`.

### `command: python` — run a Python module
Requires `module` (e.g. `Watchers.linkedin_poster`) and `args` (string or list).
Runs as: `python -m <module> <args>`

---

## Crontab equivalents

If you prefer system cron over the Python scheduler, use these entries.
Run `crontab -e` and add:

```crontab
# AI Employee Vault — scheduled tasks
# Adjust /path/to/vault and /path/to/python to match your system.

VAULT=/mnt/d/HACKATHON_00/AI_Employee_Vault
PYTHON=/mnt/d/HACKATHON_00/AI_Employee_Vault/.venv/bin/python3

# Daily dashboard update at 08:00 UTC
0 8 * * *       cd $VAULT && $PYTHON scheduler.py --run-now daily_dashboard_update >> Logs/cron.log 2>&1

# LinkedIn post generation — Monday, Wednesday, Friday at 09:00 UTC
0 9 * * 1,3,5   cd $VAULT && $PYTHON -m Watchers.linkedin_poster generate >> Logs/cron.log 2>&1

# CEO Briefing — every Sunday at 20:00 UTC (generates Monday morning briefing)
0 20 * * 0      cd $VAULT && $PYTHON scripts/generate_briefing.py >> Logs/cron.log 2>&1
```

> **Note:** System cron uses local time by default. Prefix commands with
> `TZ=UTC` or adjust the times to your local timezone offset if needed.

---

## Adding a new task

1. Add a new entry under `tasks:` in the frontmatter.
2. Give it a unique `name`.
3. Set `enabled: false` to define it without activating it.
4. Restart the scheduler.

Example — weekly business goals review every Monday at 07:00:

```yaml
- name: weekly_goals_review
  description: Review Business_Goals.md and update metrics
  schedule: monday
  time: "07:00"
  command: claude
  prompt_key: process_inbox
  enabled: false
```
