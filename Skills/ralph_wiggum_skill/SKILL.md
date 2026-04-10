# Skill: Ralph Wiggum Loop

## Purpose

The Ralph Wiggum Loop is a **persistent execution engine** that keeps invoking
Claude Code on a task until the task declares itself complete or a safety limit
is reached. Named for its stubborn optimism: if Claude didn't finish, we ask
again with more context.

Use it for complex multi-step tasks that may take more than one Claude session
to complete, tasks whose completion depends on file system changes that unfold
over time, or long-running workflows where a single Claude invocation might time
out or leave work unfinished.

**Invoked via:**
```
python scripts/ralph_loop.py "your task description"
```

---

## When to Use the Ralph Loop

Use the loop when a task has **any** of these properties:

| Property | Example |
|----------|---------|
| Multi-file, multi-step | "Process every file in Needs_Action, write a plan, and move each to Done" |
| Depends on prior output | "Review last week's logs and fix every WARNING that mentions timeout" |
| Unknown number of sub-tasks | "Check all pending approvals and dispatch them" |
| Could time out in one session | "Crawl all .md files and rebuild the index" |
| Needs retry on partial failure | "Generate LinkedIn drafts for all items in Done/ that have no draft yet" |

**Do NOT use it for:**
- Single-shot reads or writes (use orchestrator.py directly)
- Tasks with strict real-time constraints (the loop adds latency between iterations)
- Tasks that require interactive human input mid-stream

---

## How the Loop Works

```
ralph_loop.py "task"
    │
    ▼
Create Active_Projects/TASK_<id>.md
    │
    ╔══ Iteration Loop ══════════════════════════════════════════╗
    ║                                                            ║
    ║  Safety checks:                                            ║
    ║  ├─ STOP_RALPH file in vault root? → halt immediately      ║
    ║  └─ Total wall-clock timeout exceeded? → halt              ║
    ║                                                            ║
    ║  Build prompt:                                             ║
    ║  ├─ Iteration 1 → fresh task prompt                        ║
    ║  └─ Iteration N → continuation prompt with prior output    ║
    ║                                                            ║
    ║  Invoke: claude --print --dangerously-skip-permissions     ║
    ║  (per-iteration timeout enforced via subprocess.timeout)   ║
    ║                                                            ║
    ║  Completion check:                                         ║
    ║  ├─ Output contains "TASK_COMPLETE"? → done ✓              ║
    ║  ├─ Task file moved to Done/ by Claude? → done ✓           ║
    ║  └─ Neither? → increment iteration, loop again             ║
    ║                                                            ║
    ╚════════════════════════════════════════════════════════════╝
    │
    ▼
Archive TASK_<id>.md → Done/
Write Logs/YYYY-MM-DD.json entry
Update Dashboard.md Recent Activity
```

---

## Two Completion Strategies

Claude can signal task completion in two ways. Either is sufficient.

### Strategy 1 — Promise-based (required)

Claude prints the exact string `TASK_COMPLETE` on its own line when all work is
done. The loop scans every iteration's output for this string.

**Claude must do this.** The initial and continuation prompts both instruct Claude
to print `TASK_COMPLETE` when finished. If Claude forgets, the loop runs the next
iteration with a reminder.

```
(Claude's output)
...
All 7 files have been processed and moved to Done/.
TASK_COMPLETE
```

### Strategy 2 — File movement (optional, auto-detected)

Claude may move the task state file from `Active_Projects/TASK_<id>.md` to
`Done/TASK_<id>.md` as part of its work. The loop detects this after each
iteration and treats it as a completion signal even if `TASK_COMPLETE` was not
printed.

This strategy is useful when Claude is explicitly working the vault's approval or
task-completion workflow and naturally archives the state file itself.

**Both strategies can fire simultaneously** — that is fine. Either one stops the
loop.

---

## Task State File

Each loop run creates `Active_Projects/TASK_<id>.md` with YAML frontmatter:

```yaml
---
task: "Process all files in Needs_Action and create plans"
status: in_progress
created: 2026-04-10T15:00:00+00:00
max_iterations: 10
current_iteration: 3
completion_promise: "TASK_COMPLETE"
---
```

The loop updates `current_iteration` and appends iteration notes after each run.
Claude can read and update this file to preserve state between iterations.

When the loop ends (for any reason), the final status and summary are written to
the frontmatter, and the file is moved to `Done/`.

---

## Safety Features

| Feature | Default | Flag |
|---------|---------|------|
| Maximum iterations | 10 | `--max-iterations N` |
| Per-iteration timeout | 5 minutes | `--iteration-timeout SECS` |
| Total wall-clock timeout | 30 minutes | `--total-timeout SECS` |
| Emergency stop file | `STOP_RALPH` in vault root | `touch STOP_RALPH` |
| Dry-run mode | off | `--dry-run` |

### Emergency Stop

Create the file `STOP_RALPH` in the vault root at any time to halt the loop
cleanly at the end of the current iteration:

```bash
touch /path/to/vault/STOP_RALPH
```

The loop checks for this file at the top of every iteration (before invoking
Claude). It will:
1. Exit the loop immediately
2. Archive the task file with `status: stopped`
3. Write a log entry
4. Return exit code 1

Remove the file after stopping to allow future runs:
```bash
rm /path/to/vault/STOP_RALPH
```

### Outcome Codes

| Outcome | Meaning | Exit code |
|---------|---------|-----------|
| `completed` | `TASK_COMPLETE` found or file moved to Done | 0 |
| `max_iterations` | Ran N times, never completed | 1 |
| `timeout` | Total wall-clock limit reached | 1 |
| `stopped` | STOP_RALPH file detected | 1 |
| `interrupted` | Ctrl-C | 1 |
| `error` | `claude` CLI not found | 1 |

---

## Usage Examples

### Basic usage
```bash
python scripts/ralph_loop.py "Process all files in Needs_Action and create plans for each"
```

### Custom limits
```bash
python scripts/ralph_loop.py "Generate weekly LinkedIn post drafts for all approved tasks" \
    --max-iterations 5 \
    --iteration-timeout 180 \
    --total-timeout 900
```

### Dry run (test the prompt and loop logic without Claude)
```bash
python scripts/ralph_loop.py "Rebuild the Dashboard from scratch" --dry-run
```

### Custom task ID (useful for resuming or referencing in logs)
```bash
python scripts/ralph_loop.py "Full vault audit" --id "AUDIT_2026_Q2"
```

### Run via scheduler (add to Config/schedule.md)
```yaml
- name: weekly_needs_action_sweep
  description: Process all pending Needs_Action items every Monday
  schedule: monday
  time: "07:30"
  command: python
  module: scripts.ralph_loop
  args: "Process all files in Needs_Action and create and execute plans"
  enabled: true
```

---

## What Claude Receives

### Iteration 1 — Fresh prompt
```
You are the AI Employee for this vault. Your vault root is: /path/to/vault

## Task
<description>

## Your Instructions
- Work autonomously. Do not ask for clarification.
- Read Company_Handbook.md and Business_Goals.md for context.
- Use any Skills in Skills/ that are relevant.
- When completely and fully finished, print: TASK_COMPLETE
- Your task state file is: Active_Projects/TASK_<id>.md
  Update it with progress notes as you work.
...
```

### Iteration N — Continuation prompt
The continuation prompt includes:
- Which iteration this is out of the max
- The tail (last ~3 000 characters) of the previous iteration's output
- A reminder that previous work should not be repeated
- The same `TASK_COMPLETE` requirement

This gives Claude enough context to avoid starting over while keeping the
prompt size bounded.

---

## Output & Logging

After each run the loop appends an entry to `Logs/YYYY-MM-DD.json`:

```json
{
  "timestamp": "2026-04-10T15:34:21+00:00",
  "action_type": "ralph_loop",
  "result": "completed",
  "task_id": "20260410_153400_A1B2C3",
  "iterations_used": 2,
  "summary": "Task completed in 2 iteration(s)",
  "task": "Process all files in Needs_Action..."
}
```

`Dashboard.md` is updated with a line in `## Recent Activity`:
```
- `2026-04-10 15:34` — [Ralph Loop] ✓ `20260410_153400_A1B2C3` completed: Process all files in…
```

---

## Recommended Scenarios

### 1. Batch processing Needs_Action
```bash
python scripts/ralph_loop.py \
  "Process every file currently in Needs_Action/. For each file: read it, \
   apply the reasoning_loop skill (all 6 phases), write a plan, and execute \
   full_auto plans immediately. Move each item to Done/ when finished. \
   Print TASK_COMPLETE when all items are processed."
```

### 2. Approval queue drain
```bash
python scripts/ralph_loop.py \
  "Review all items in Pending_Approval/. For items older than 24 hours with \
   priority high or critical, auto-approve them per Company_Handbook.md rules. \
   For others, add a note explaining the delay. Print TASK_COMPLETE when done."
```

### 3. Full vault audit
```bash
python scripts/ralph_loop.py \
  "Audit the entire vault: check Plans/ for stale plans (>7 days, status pending), \
   check Done/ for any items missing resolution timestamps, check Dashboard.md \
   for accuracy. Fix issues found and write a summary to Briefings/AUDIT_<date>.md. \
   Print TASK_COMPLETE when finished." \
  --max-iterations 5 --total-timeout 1800
```

### 4. Content generation pipeline
```bash
python scripts/ralph_loop.py \
  "Generate LinkedIn post drafts for every file in Done/ from the past 7 days \
   that does not already have a corresponding file in Pending_Approval/. \
   Use the linkedin_poster skill. Print TASK_COMPLETE when all drafts are created." \
  --max-iterations 3
```

---

## Files

| Path | Role |
|------|------|
| `scripts/ralph_loop.py` | Main loop implementation |
| `Active_Projects/TASK_<id>.md` | Live task state (created at start, archived at end) |
| `Done/TASK_<id>.md` | Archived task state after completion |
| `Logs/YYYY-MM-DD.json` | Per-run log entry (`action_type: ralph_loop`) |
| `STOP_RALPH` | Emergency stop sentinel (create to halt, delete to allow future runs) |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Loop completes 10 iterations without finishing | Increase `--max-iterations`; or simplify the task description; check Claude output logs for errors |
| `claude CLI not found` | Ensure `claude` is on PATH: `which claude`; install via `npm install -g @anthropic-ai/claude-code` |
| Task state file stuck in Active_Projects/ | Run `python scripts/ralph_loop.py "..."` again — it creates a new task ID; manually archive the stuck file |
| Loop ran but Dashboard not updated | Dashboard.md is only updated if it already exists and has `## Recent Activity` |
| Need to halt mid-run | `touch STOP_RALPH` in vault root; loop will stop at the end of the current iteration |
| Iteration timeout too short for complex tasks | Pass `--iteration-timeout 600` (10 min) or higher |
