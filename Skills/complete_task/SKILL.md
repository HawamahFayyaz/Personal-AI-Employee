# Skill: Complete Task

## Purpose

This skill instructs Claude Code to cleanly archive a finished task.
It moves every file associated with the task to `Done/`, writes a structured
JSON log entry, and updates `Dashboard.md` — leaving the vault in a consistent
state with a complete audit trail.

Run this skill when a task has been executed and verified, or when a human
approves an item from `Pending_Approval/` and the work is done.

---

## Trigger phrases

Invoke this skill when the user says any of the following (or similar):

- "Complete task `<filename>`"
- "Mark `<filename>` as done"
- "Archive `<filename>`"
- "Task complete — `<filename>`"
- "Move `<filename>` to Done"
- "Close out `<filename>`"

If no filename is specified and only one task is `in_progress`, complete that
one. If multiple tasks are in progress, ask the user which one to complete
before proceeding.

---

## Inputs required

| Input | Source | Notes |
|---|---|---|
| `source_file` | User or frontmatter | Filename of the original action item |
| `summary` | User or inferred | One sentence describing what was accomplished |

If the user provides a summary, use it verbatim. If not, infer it from the
Plan's `## Objective` line.

---

## Step-by-step instructions

Complete every step in order. If any step hits an edge case, follow the
edge-case rules in the section below rather than stopping.

---

### Phase 1 — Locate all associated files

Given the `source_file` name, locate every file belonging to this task:

1. **Action item** — look in this order:
   - `Needs_Action/<source_file>`
   - `Pending_Approval/<source_file>`
   - `Done/<source_file>` (already archived — see edge cases)

2. **Plan file** — search `Plans/` for any `.md` whose frontmatter
   `source_file` field matches `<source_file>`. There may be more than one
   (re-plans); use the most recently created by `created` date.

3. **Approval file** — search `Approved/` for any file whose name contains
   the stem of `<source_file>`.

4. **Payload file** — if `<source_file>` is a metadata sidecar
   (e.g., `FILE_report.pdf.md`), check for a companion payload file
   (`FILE_report.pdf`) in the same folder.

Record the located paths. If a file is not found, note it as missing — do not
stop. Missing files are handled per the edge-case rules below.

---

### Phase 2 — Move files to Done/

Move each located file to `Done/`. Use this exact order:

1. Payload file (if any) → `Done/<payload_filename>`
2. Action item `.md` → `Done/<source_file>`
3. Plan `.md` → `Done/<plan_filename>`
4. Approval `.md` → `Done/<approval_filename>` (if it exists)

Before moving each file, update its frontmatter `status` field to `done` and
add a `completed_at` field set to the current ISO 8601 UTC timestamp:

```yaml
status: done
completed_at: 2026-10-15T14:32:00+00:00
```

If `Done/` does not exist, create it first.

Do not delete originals — the move operation must be atomic
(write to destination, then remove from source).

---

### Phase 3 — Write the completion log entry

Append a new JSON object to today's completion log:

**Log file path:** `Logs/YYYY-MM-DD.json`
(use today's date; separate file from the watcher `.log` file)

**Entry format:**

```json
{
  "timestamp": "<ISO 8601 UTC>",
  "action_type": "task_complete",
  "source_file": "<source_file>",
  "plan_file": "<plan_filename or null>",
  "approval_file": "<approval_filename or null>",
  "result": "success",
  "summary": "<one sentence — what was accomplished>"
}
```

**File format rules:**

- The file is a **JSON array** — each entry is an element.
- If the file does not exist, create it with the entry as the sole element:
  ```json
  [
    { ...entry... }
  ]
  ```
- If the file already exists, read it, append the new entry to the array,
  and write it back. Preserve all existing entries.
- If the file exists but is malformed (not valid JSON), do not overwrite it.
  Instead write the new entry to `Logs/YYYY-MM-DD_recovery.json` and note
  the failure in the output summary.

---

### Phase 4 — Update Dashboard.md

Make the following targeted edits to `Dashboard.md`. Do not rewrite sections
that are not affected.

1. **Pending Actions** — remove the line matching `<source_file>`.
   If the section becomes empty, replace its content with `_No pending actions_`.

2. **Awaiting Approval** — remove the line matching `<source_file>` if present.
   If the section becomes empty, replace with `_No items awaiting approval_`.

3. **Active Plans** — remove the line matching the plan file if present.
   If the section becomes empty, replace with `_No active plans_`.

4. **Recent Activity** — prepend a new line at the top of the section:
   ```
   - `<YYYY-MM-DD HH:MM UTC>` — [complete_task] <summary>
   ```
   Keep only the 5 most recent entries; drop the oldest if needed.

5. **Quick Stats** — update:
   - Increment `Tasks Completed Today` by 1
   - Decrement `Tasks Pending` by 1 (floor at 0)
   - Decrement `Approvals Waiting` by 1 if the item came from `Pending_Approval/`
     (floor at 0)

6. **Frontmatter** — update `last_updated` to today's date and
   `last_updated_time` to the current `HH:MM UTC`.

---

## Output you must produce

After all steps complete, print this summary:

```
## Task Complete ✓

Source file  : <source_file>
Plan         : <plan_filename | not found>
Approval     : <approval_filename | none>
Completed at : <ISO timestamp>

Files moved to Done/:
  ✅ <filename>
  ✅ <filename>
  ⚠️  <filename> — not found, skipped

Log entry    : Logs/YYYY-MM-DD.json
Dashboard    : updated

Summary: <summary sentence>
```

---

## Edge cases

### Action item already in Done/
The file was already moved. Do not move it again. Log the entry with
`"result": "already_complete"` instead of `"success"`. Skip the Dashboard
decrement for `Tasks Pending`. Still write the log entry and print the summary.

### Plan file not found
Continue without it. Set `"plan_file": null` in the log entry. Note
`⚠️ Plan not found — skipped` in the output summary. Do not block completion.

### Approval file not found
Not all tasks have an approval file. If none is found in `Approved/`, set
`"approval_file": null` and skip silently — this is normal.

### Log file does not exist yet
Create `Logs/YYYY-MM-DD.json` as a new file containing a single-element JSON
array. Do not create any intermediate directory — `Logs/` already exists.

### Log file exists but is malformed JSON
Do not overwrite it. Write the entry to `Logs/YYYY-MM-DD_recovery.json`
instead. Report: `⚠️ Log file malformed — wrote to recovery file.`

### Done/ folder does not exist
Create it, then proceed with the move.

### Multiple plan files match
Use the one with the most recent `created` frontmatter timestamp.

### Source file not found anywhere
If the file cannot be found in `Needs_Action/`, `Pending_Approval/`, or
`Done/`, report: `❌ Source file not found: <source_file>` and stop. Do not
write a log entry or modify the dashboard.

---

## Files this skill reads

| File | Purpose |
|---|---|
| `Needs_Action/<source_file>` | Action item (primary location) |
| `Pending_Approval/<source_file>` | Action item (if it needed approval) |
| `Plans/*.md` | Find matching plan by `source_file` frontmatter |
| `Approved/*.md` | Find matching approval record |
| `Dashboard.md` | Read before targeted edits |
| `Logs/YYYY-MM-DD.json` | Existing log to append to |

## Files this skill writes

| File | Purpose |
|---|---|
| `Done/<source_file>` | Archived action item (status updated) |
| `Done/<plan_filename>` | Archived plan (status updated) |
| `Done/<approval_filename>` | Archived approval (if existed) |
| `Done/<payload_filename>` | Archived payload file (if existed) |
| `Logs/YYYY-MM-DD.json` | Completion log entry appended |
| `Dashboard.md` | Targeted updates to counts and activity |

---

## Example invocation

User says:

> "Mark FILE_report.pdf.md as done — I reviewed it and filed it."

Claude Code:
1. Locates `Needs_Action/FILE_report.pdf.md` + `FILE_report.pdf` + matching plan
2. Updates frontmatter in both `.md` files, moves all to `Done/`
3. Appends to `Logs/2026-10-15.json`
4. Updates `Dashboard.md` (removes from Pending Actions, adds to Recent Activity, bumps stats)
5. Prints the completion summary

User says:

> "Complete task FILE_invoice.pdf.md"

Claude Code uses the Plan's `## Objective` as the summary since none was provided,
then follows the same steps.
