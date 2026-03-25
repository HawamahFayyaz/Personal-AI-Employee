# Skill: Update Dashboard

## Purpose

This skill instructs Claude Code to perform a full refresh of `Dashboard.md`.
It counts files across all vault folders, reads active plan statuses, parses
recent log entries, checks which watchers are running, and rewrites the
dashboard in a canonical format — all in a single pass.

Run this skill any time you want an accurate, up-to-date snapshot of the vault.

---

## Trigger phrases

Invoke this skill when the user says any of the following (or similar):

- "Update the dashboard"
- "Refresh the dashboard"
- "What's the current status?"
- "Show me the vault status"
- "Dashboard update"
- "Sync the dashboard"

---

## Step-by-step instructions

Work through all phases in order. Do not write `Dashboard.md` until Phase 5.

---

### Phase 1 — Count files in each folder

Count the number of **files** (not directories) in each of these folders.
For `.md`-only folders, count only `.md` files. For mixed folders, count all
non-hidden files.

| Folder | Count what |
|---|---|
| `Needs_Action/` | All `.md` files with `status: pending` in frontmatter |
| `Plans/` | All `.md` files — split by `status` field (`in_progress` vs `done`) |
| `Pending_Approval/` | All `.md` files — split by `status` (`awaiting_approval` vs `blocked`) |
| `Done/` | Total file count (any type) |
| `Inbox/` | Total file count (unprocessed drops) |

If a folder does not exist, treat its count as 0.

Produce this internal summary before moving to Phase 2:

```
Needs_Action : N pending
Plans        : N in_progress, N done
Pending_Approval : N awaiting_approval, N blocked
Done         : N total
Inbox        : N total
```

---

### Phase 2 — Read active plan statuses

Read every `.md` file in `Plans/` whose frontmatter `status` is `in_progress`.

For each plan extract:
- `source_file` — the originating file
- `priority` — critical / high / medium / low
- `type` — the action type
- `created` — ISO timestamp
- The first `## Objective` line

Keep only the 5 most recent by `created` date for the dashboard display.

---

### Phase 3 — Read recent log entries

1. Identify today's log file: `Logs/watcher_YYYY-MM-DD.log`
   (use today's date; if missing, try the most recently modified file in `Logs/`).
2. Read the **last 50 lines** of the log file.
3. Extract up to the **5 most recent meaningful events** — lines that contain
   any of these keywords (case-insensitive):
   - `Queued`, `Started`, `Stopped`, `Error`, `Warning`, `processed`,
     `created`, `moved`, `approved`, `rejected`
4. For each extracted line, parse:
   - Timestamp (first field: `YYYY-MM-DD HH:MM:SS`)
   - Logger name (third field)
   - Message (everything after the logger name)

If no log file exists, note "No log data available."

---

### Phase 4 — Determine watcher status

A watcher is **Online** (🟢) if the log contains a "started" line for that
watcher with **no subsequent "stopped" or "error" line** from the same session.

A watcher is **Offline** (🔴) if:
- No log entry exists for it, OR
- Its last log entry is "stopped", "shutdown", or contains `ERROR`/`WARNING`

A watcher is **Degraded** (🟡) if it has logged a `WARNING` but is still running.

Watchers to check:

| Watcher | Log identifier string |
|---|---|
| File Watcher | `FilesystemWatcher` |
| Gmail Watcher | `GmailWatcher` |
| WhatsApp Watcher | `WhatsAppWatcher` |

If a watcher has never appeared in any log, mark it as 🔴 Not configured.

---

### Phase 5 — Check for alerts and warnings

Scan all sources for conditions that require a human alert:

**From `Needs_Action/`:**
- Any file with `priority: critical` → ALERT
- Any file with `priority: high` older than 1 hour → WARNING

**From `Pending_Approval/`:**
- Any file with `status: blocked` → ALERT
- Any file with `status: awaiting_approval` older than 24 hours → WARNING

**From logs:**
- Any `ERROR` line in today's log → WARNING
- Any watcher that failed to start → ALERT

**From `Business_Goals.md`:**
- If `Tasks Pending > 10` → WARNING (queue backlog)
- If `Approvals Waiting > 5` → WARNING (approval backlog)

Collect all alerts into a list for the Alerts section of the dashboard.

---

### Phase 6 — Write Dashboard.md

Rewrite `Dashboard.md` **in full** using the exact format below. Do not
preserve any old content except as historical data already reflected in the
new counts.

#### Exact Dashboard.md format

```markdown
---
last_updated: <YYYY-MM-DD>
last_updated_time: <HH:MM UTC>
---

# AI Employee Dashboard

## System Status
- **AI Employee**: 🟢 Online
- **File Watcher**: <🟢 Online | 🟡 Degraded | 🔴 Offline | 🔴 Not configured>
- **Gmail Watcher**: <🟢 Online | 🟡 Degraded | 🔴 Offline | 🔴 Not configured>
- **WhatsApp Watcher**: <🟢 Online | 🟡 Degraded | 🔴 Offline | 🔴 Not configured>

## Alerts
<If no alerts: _No alerts_>
<If alerts exist, list each on its own line:>
- ⚠️ WARNING — <description>
- 🚨 ALERT — <description>

## Pending Actions
<If none: _No pending actions_>
<If items exist:>
- [<PRIORITY>] `<filename>` — <one-line objective from its Plan.md>
<repeat for each item in Needs_Action/ with status: pending, sorted critical→low>

## Awaiting Approval
<If none: _No items awaiting approval_>
<If items exist:>
- [<PRIORITY>] `<filename>` — <one-line objective> <[BLOCKED] if status: blocked>
<repeat for each item in Pending_Approval/, blocked items first>

## Active Plans
<If none: _No active plans_>
<If items exist (up to 5 most recent):>
- `<PLAN filename>` — <objective> _(type: <type>, priority: <priority>)_
<repeat>

## Recent Activity
<Up to 5 most recent log events, most recent first:>
- `<YYYY-MM-DD HH:MM>` — [<LoggerName>] <message>
<If no log data: _No recent activity_>

## Quick Stats
- Tasks Completed Today: <count of files in Done/ modified today>
- Tasks Pending: <count of pending .md files in Needs_Action/>
- Plans In Progress: <count of Plans/ with status: in_progress>
- Approvals Waiting: <count of Pending_Approval/ files>
- Inbox Items: <count of files in Inbox/>
```

---

### Filling in the format — rules

**`last_updated`** — today's date in `YYYY-MM-DD` format.
**`last_updated_time`** — current time in `HH:MM UTC`.

**System Status** — use the watcher verdicts from Phase 4. The "AI Employee"
line is always 🟢 Online (Claude is running this skill right now).

**Alerts** — list all items from Phase 5. `🚨 ALERT` for critical/blocked
items, `⚠️ WARNING` for everything else. If the list is empty, write
`_No alerts_`. Alerts should be the first thing a human reads.

**Pending Actions** — sourced from `Needs_Action/` phase 1 counts. For each
pending `.md` file, read its companion Plan (match by `source_file` field) to
get a one-line objective. If no plan exists yet, use the filename.

**Awaiting Approval** — sourced from `Pending_Approval/`. Mark blocked items
with `[BLOCKED]`. List blocked items before awaiting items.

**Active Plans** — sourced from Phase 2. Five most recent `in_progress` plans.

**Recent Activity** — sourced from Phase 3 log parsing. Five most recent
meaningful events.

**Quick Stats** — use the exact counts from Phase 1. "Tasks Completed Today"
counts files in `Done/` whose modification time is today.

---

## Output you must produce

After writing `Dashboard.md`, print this summary to the user:

```
## Dashboard Updated

last_updated: YYYY-MM-DD HH:MM UTC

System Status:
  File Watcher     : 🟢 / 🟡 / 🔴
  Gmail Watcher    : 🟢 / 🟡 / 🔴
  WhatsApp Watcher : 🟢 / 🟡 / 🔴

Counts:
  Needs_Action     : N pending
  Pending_Approval : N items (N blocked)
  Plans            : N in progress
  Done             : N total
  Inbox            : N total

Alerts: N  |  Warnings: N

Dashboard.md written successfully.
```

---

## Error handling

- If `Dashboard.md` does not exist, create it from scratch.
- If a folder does not exist, count it as 0 — do not error.
- If `Plans/` has unreadable or malformed frontmatter files, skip them and
  note the count in the summary as "N plans (M unreadable)".
- If the log file is missing or unreadable, set Recent Activity to
  `_No recent activity_` and all watcher statuses to 🔴 Not configured.
- Never leave `Dashboard.md` in a partial state — write the complete file
  atomically (compose in memory, then write once).

---

## Files this skill reads

| File | Purpose |
|---|---|
| `Needs_Action/*.md` | Pending work queue |
| `Plans/*.md` | Active plan statuses and objectives |
| `Pending_Approval/*.md` | Items awaiting human sign-off |
| `Done/` | Completed item count |
| `Inbox/` | Unprocessed file drop count |
| `Logs/watcher_YYYY-MM-DD.log` | Watcher status and recent events |
| `Business_Goals.md` | Backlog alert thresholds |

## Files this skill writes

| File | Purpose |
|---|---|
| `Dashboard.md` | Full rewrite with current vault state |

---

## Example invocation

User says:

> "Update the dashboard" or "What's the current status?"

Claude Code executes all six phases and writes `Dashboard.md`. The full process
should complete in a single response — no back-and-forth needed.
