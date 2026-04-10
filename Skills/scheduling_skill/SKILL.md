# Skill: Scheduler

## What it does

`scheduler.py` reads `Config/schedule.md`, registers each enabled task with
the Python `schedule` library, and runs a 30-second poll loop. Each task fires
in its own daemon thread; a per-task semaphore prevents overlap if a previous
run is still in progress.

Two task types are supported:

| `command` | What runs | Example use |
|---|---|---|
| `claude` | Claude Code CLI (`claude --print …`) | Dashboard refresh, inbox processing |
| `python` | Python module (`python -m <module> <args>`) | LinkedIn post generation |

---

## File locations

```
scheduler.py           ← main scheduler script (vault root)
Config/schedule.md     ← human-editable task definitions (YAML frontmatter)
Logs/watcher-YYYY-MM-DD.log  ← scheduler events (shared with watchers)
Logs/YYYY-MM-DD.json         ← JSON activity log (shared with orchestrator)
```

---

## Usage

### Start the scheduler (blocking process)

```bash
# System Python with schedule installed:
python scheduler.py

# Vault venv:
.venv/bin/python3 scheduler.py

# Dry-run — logs what would happen, no real API calls:
python scheduler.py --dry-run
```

### List the schedule and next run times

```bash
python scheduler.py --list
```

Output:
```
Schedule as of 2026-04-08 09:00:00 UTC
Config : /vault/Config/schedule.md

  NAME                              SCHEDULE           TIME   CMD         STATUS
  ──────────────────────────────────────────────────────────────────────────────
  daily_dashboard_update            daily              08:00  claude      enabled
  linkedin_post_generation          monday,wednesday,friday  09:00  python  enabled

  Next scheduled runs:
    daily_dashboard_update          →  2026-04-09 08:00 UTC
    linkedin_post_generation        →  2026-04-09 09:00 UTC
```

### Run a task immediately (one-shot, foreground)

```bash
python scheduler.py --run-now daily_dashboard_update
python scheduler.py --run-now linkedin_post_generation
```

This is the recommended way to invoke tasks from **cron** — the scheduler
process starts, runs the task, logs it, and exits.

---

## Config/schedule.md — task definition format

Tasks are defined in the YAML frontmatter block at the top of
`Config/schedule.md`. The rest of the file is human documentation.

### Full task schema

```yaml
tasks:
  - name: my_task              # unique identifier (used in --run-now and logs)
    description: What it does  # human label (not parsed)
    schedule: daily            # see Schedule syntax below
    time: "09:00"              # 24-hour UTC, quoted string
    command: claude            # "claude" or "python"
    prompt_key: dashboard_update  # for command: claude — see Prompt keys
    module: Watchers.linkedin_poster  # for command: python
    args: generate             # for command: python — string or list
    enabled: true              # set false to disable without deleting
```

### Schedule syntax

| `schedule` value | Registered as |
|---|---|
| `daily` | Every day |
| `monday` | Every Monday |
| `tuesday` – `sunday` | The named weekday |
| `monday,wednesday,friday` | Three separate jobs |
| `weekdays` | Mon–Fri (5 jobs) |
| `weekends` | Sat–Sun (2 jobs) |

All times are UTC. The `time` field must be `"HH:MM"`.

### Prompt keys (for `command: claude`)

| Key | What Claude does |
|---|---|
| `dashboard_update` | Runs the `update_dashboard` skill — rewrites `Dashboard.md` |
| `process_inbox` | Runs the `reasoning_loop` skill — processes everything in `Needs_Action/` |

To add a new prompt key, add a function to the `_PROMPTS` dict in `scheduler.py`.

---

## Default tasks

### `daily_dashboard_update` — 08:00 UTC daily

Invokes Claude Code with the `update_dashboard` skill prompt. Claude:
1. Counts files in all vault folders
2. Reads active plans and recent log entries
3. Determines watcher status from today's log
4. Rewrites `Dashboard.md` in the canonical format

### `linkedin_post_generation` — 09:00 UTC Mon/Wed/Fri

Runs `python -m Watchers.linkedin_poster generate`, which:
1. Reads `Business_Goals.md` for context
2. Calls the Claude API (Anthropic SDK) to generate a post
3. Writes the draft to `Pending_Approval/LINKEDIN_<date>.md`
4. Does **not** post to LinkedIn — human approval is required

The `ApprovalWatcher` dispatches the post once you move the file to `Approved/`.

---

## Running the scheduler persistently

### Option A — foreground (development / WSL)

```bash
python scheduler.py
# or with venv:
.venv/bin/python3 scheduler.py
```

Keep the terminal open, or run in a `tmux`/`screen` session:

```bash
tmux new-session -d -s scheduler '.venv/bin/python3 scheduler.py'
tmux attach -t scheduler   # view logs
```

### Option B — systemd service (Linux)

Create `/etc/systemd/system/ai-vault-scheduler.service`:

```ini
[Unit]
Description=AI Employee Vault Scheduler
After=network.target

[Service]
Type=simple
User=<your-user>
WorkingDirectory=/mnt/d/HACKATHON_00/AI_Employee_Vault
ExecStart=/mnt/d/HACKATHON_00/AI_Employee_Vault/.venv/bin/python3 scheduler.py
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable ai-vault-scheduler
sudo systemctl start ai-vault-scheduler
sudo systemctl status ai-vault-scheduler
```

### Option C — crontab (system cron, no persistent process)

Use `--run-now` so each cron invocation starts, runs one task, and exits.
Run `crontab -e` and add:

```crontab
# AI Employee Vault — scheduled tasks
VAULT=/mnt/d/HACKATHON_00/AI_Employee_Vault
PYTHON=/mnt/d/HACKATHON_00/AI_Employee_Vault/.venv/bin/python3

# Daily dashboard update at 08:00 UTC
0 8 * * *       cd $VAULT && $PYTHON scheduler.py --run-now daily_dashboard_update >> Logs/cron.log 2>&1

# LinkedIn post generation — Monday, Wednesday, Friday at 09:00 UTC
0 9 * * 1,3,5   cd $VAULT && $PYTHON -m Watchers.linkedin_poster generate >> Logs/cron.log 2>&1
```

> **WSL note:** WSL cron requires the cron service to be running.
> Start it with `sudo service cron start`.
> To auto-start at WSL launch, add that command to `~/.bashrc` or use
> `/etc/wsl.conf` with `[boot] command = service cron start`.

### Crontab expression reference

| Expression | Meaning |
|---|---|
| `0 8 * * *` | 08:00 every day |
| `0 9 * * 1,3,5` | 09:00 Mon/Wed/Fri |
| `*/15 * * * *` | Every 15 minutes |
| `0 */4 * * *` | Every 4 hours |
| `0 8 * * 1` | Every Monday at 08:00 |

---

## Adding a custom task

### New Claude task

1. Add a prompt builder function in `scheduler.py`:

```python
def _prompt_my_task() -> str:
    return (
        f"You are the AI Employee for this vault. Your vault root is: {VAULT}\n\n"
        "... your instructions ..."
    )
```

2. Register it in `_PROMPTS`:

```python
_PROMPTS["my_task"] = _prompt_my_task
```

3. Add the task to `Config/schedule.md` frontmatter:

```yaml
- name: my_task
  schedule: friday
  time: "17:00"
  command: claude
  prompt_key: my_task
  enabled: true
```

4. Restart the scheduler.

### New Python task

No code changes needed — just add to `Config/schedule.md`:

```yaml
- name: weekly_report
  schedule: monday
  time: "07:30"
  command: python
  module: my_scripts.weekly_report
  args: "--format markdown"
  enabled: true
```

---

## Overlap protection

Each task runs in its own daemon thread. A `threading.Semaphore(1)` per task
ensures that if a previous run is still executing when the next trigger fires,
the new trigger is **skipped** (not queued) and a WARNING is logged:

```
2026-04-08 09:00:31  WARNING  scheduler  Task 'daily_dashboard_update' is still
                              running from the previous trigger — skipping.
```

This prevents runaway subprocess stacking if Claude takes longer than expected.

---

## Logs

All scheduler events are written to two places:

### `Logs/watcher-YYYY-MM-DD.log` (text)

```
2026-04-08 08:00:00  INFO     scheduler  Task 'daily_dashboard_update' starting — invoking Claude Code…
2026-04-08 08:02:31  INFO     scheduler  Task 'daily_dashboard_update' finished — success
2026-04-08 09:00:00  INFO     scheduler  Task 'linkedin_post_generation' starting — .venv/bin/python3 -m Watchers.linkedin_poster generate
2026-04-08 09:00:12  INFO     scheduler  Task 'linkedin_post_generation' finished — success
```

### `Logs/YYYY-MM-DD.json` (structured)

```json
[
  {
    "timestamp": "2026-04-08T08:00:00+00:00",
    "action_type": "scheduler_task",
    "task": "daily_dashboard_update",
    "result": "success",
    "summary": "Claude completed task 'daily_dashboard_update'."
  }
]
```

---

## Error handling

| Scenario | Behaviour |
|---|---|
| `claude` CLI not on PATH | ERROR logged; task recorded as `error` in JSON log |
| Unknown `prompt_key` | ERROR logged; task skipped |
| Unknown `command` type | ERROR at registration; job not created |
| Invalid `time` format | ERROR at registration; task skipped |
| Task still running at next trigger | WARNING logged; trigger skipped |
| `Config/schedule.md` missing | ERROR on startup; process exits |
| Malformed YAML frontmatter | ERROR on startup; process exits |
| Task exits with non-zero code | ERROR logged; next run still scheduled |
