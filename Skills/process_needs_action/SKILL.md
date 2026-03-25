# Skill: Process Needs_Action

## Purpose

This skill instructs Claude Code to act as the AI Employee's processing engine.
It reads every `.md` file in `Needs_Action/`, decides what to do with it based
on the Company Handbook rules, creates a structured plan, and routes the item to
the correct next folder — all while keeping `Dashboard.md` up to date.

---

## Trigger phrases

Invoke this skill when the user says any of the following (or similar):

- "Process the inbox"
- "Check Needs_Action"
- "What needs to be done?"
- "Run the AI employee"
- "Process pending actions"
- "Handle the queue"

---

## Step-by-step instructions

Work through the following steps **in order**. Complete every step for **all**
files before moving to the next phase.

---

### Phase 1 — Discover

1. List every `.md` file in `Needs_Action/`.
   - If the folder is empty, report "No pending actions." and stop.
2. Read each file in full, parsing its YAML frontmatter fields:
   - `type` — what kind of item this is (see Type Routing below)
   - `priority` — `critical`, `high`, `medium`, or `low`
   - `status` — should be `pending`; skip files that are not `pending`
   - Any type-specific fields (`original_name`, `sender`, `subject`, `amount`, etc.)
3. Build an internal processing list sorted by priority:
   - `critical` → `high` → `medium` → `low`

---

### Phase 2 — Analyze & Route

For each file in priority order, apply the rules from `Company_Handbook.md` and
`Business_Goals.md` to decide the routing:

#### Autonomy levels (from Company_Handbook.md)

| Level | Definition | Action |
|-------|------------|--------|
| **Full Auto** | Read files, create plans, update dashboard, log actions | Process immediately → `Done/` |
| **Needs Approval** | Send emails, make payments, post on social media, delete files | Create plan → `Pending_Approval/` |
| **Never Auto** | Legal documents, contracts, bulk sends, new payee payments | Create plan → `Pending_Approval/` with `blocked: true` flag |

#### Type routing table

| `type` value | Default autonomy | Notes |
|---|---|---|
| `file_drop` | Full Auto | Analyze content; flag if filename suggests invoice/contract/legal |
| `email_inbound` | Full Auto (read) / Needs Approval (reply/send) | Flag if keywords: urgent, payment, invoice, legal, contract |
| `email_outbound` | Needs Approval | Always requires human sign-off before sending |
| `whatsapp` | Needs Approval | Never auto-reply to DMs |
| `payment` | Needs Approval if >$100 or new recipient; Full Auto if recurring <$50 | Cross-check Business_Goals.md thresholds |
| `social_media` | Needs Approval | Must align with Business_Goals.md before posting |
| `calendar` | Full Auto | Scheduling and reminders only |
| `unknown` | Needs Approval | When in doubt, escalate |

If any file triggers a **Never Auto** rule (legal, contract, bulk send, new
payee), add `blocked: true` to its `Pending_Approval` entry and include a clear
human-readable warning.

---

### Phase 3 — Create a Plan

For every file processed, write a `Plan.md` into the `Plans/` folder.

**Filename convention:** `PLAN_<YYYYMMDD_HHMMSS>_<original_stem>.md`
Example: `PLAN_20261015_143022_FILE_invoice.pdf.md`

**Required Plan.md format:**

```markdown
---
source_file: <original filename from Needs_Action/>
created: <ISO 8601 timestamp, UTC>
type: <type from frontmatter>
priority: <priority from frontmatter>
autonomy: <full_auto | needs_approval | never_auto>
status: in_progress
---

## Objective
<One clear sentence describing what needs to happen with this item.>

## Steps
- [ ] Step 1 — <first concrete action>
- [ ] Step 2 — <second concrete action>
- [ ] Step 3 — <etc.>

## Notes
<Any relevant observations: flags triggered, handbook rules applied,
amounts involved, sender/recipient details, content summary, risks.>
```

Steps should be specific and actionable. Examples:
- "Reply to sender confirming receipt" (not "handle email")
- "Extract total amount and compare to Business_Goals.md thresholds"
- "Forward invoice PDF to accountant for approval"

---

### Phase 4 — Route the source file

After writing the plan, move (rename) the source `.md` file from `Needs_Action/`
to the correct destination folder. If a companion payload file exists (e.g.,
`FILE_report.pdf` alongside `FILE_report.pdf.md`), move both together.

| Autonomy level | Destination |
|---|---|
| Full Auto | `Done/` |
| Needs Approval | `Pending_Approval/` |
| Never Auto | `Pending_Approval/` (with `blocked: true` in frontmatter) |

Update the frontmatter `status` field when moving:
- `Done/` → `status: done`
- `Pending_Approval/` → `status: awaiting_approval`
- `Pending_Approval/` (blocked) → `status: blocked`

---

### Phase 5 — Update Dashboard.md

After all files are processed, rewrite the relevant sections of `Dashboard.md`:

1. **System Status** — mark "File Watcher" as 🟢 Online if any `file_drop` was
   processed.

2. **Pending Actions** — list every item now in `Pending_Approval/` as:
   ```
   - [PRIORITY] `<filename>` — <one-line summary> → Pending_Approval/
   ```

3. **Recent Activity** — prepend a new entry (most recent first):
   ```
   - <ISO date> — Processed <N> item(s): <X> auto-completed, <Y> queued for approval
   ```

4. **Quick Stats** — update all three counters:
   - `Tasks Completed Today` — count of items moved to `Done/` this session
   - `Tasks Pending` — current total count of files in `Pending_Approval/`
   - `Approvals Waiting` — count of files with `status: awaiting_approval` or
     `status: blocked` in `Pending_Approval/`

5. Update the `last_updated` frontmatter field to today's ISO date.

---

## Output you must produce

At the end of the run, print a summary to the user:

```
## Needs_Action Processing Complete

Processed: N file(s)

✅ Auto-completed (Done/):
  - FILE_report.pdf → PLAN_...md

⏳ Queued for approval (Pending_Approval/):
  - FILE_invoice.pdf → PLAN_...md  [blocked: legal document]

Dashboard.md updated.
```

If nothing was processed, say so explicitly.

---

## Error handling

- If a `.md` file has no frontmatter or unparseable frontmatter, treat it as
  `type: unknown`, `priority: medium`, route to `Pending_Approval/`, and note
  the parse failure in the Plan's `## Notes` section.
- If a required destination folder (`Plans/`, `Done/`, `Pending_Approval/`)
  doesn't exist, create it before writing.
- Never delete files from `Needs_Action/` — always move them.
- Never overwrite an existing Plan file — use a timestamp in the filename.

---

## Example invocation

User says:

> "Process the inbox" or "Check what's in Needs_Action"

Claude Code should respond by executing this skill top to bottom, starting with
Phase 1 (discovery) and ending with the summary output above.

No special flags or parameters are needed. The vault root is always the parent
of the `Skills/`, `Watchers/`, and `Needs_Action/` folders.

---

## Files this skill reads

| File | Purpose |
|---|---|
| `Needs_Action/*.md` | Work queue — items to process |
| `Company_Handbook.md` | Autonomy rules and flag keywords |
| `Business_Goals.md` | Financial thresholds and metric targets |
| `Dashboard.md` | Status board to keep updated |

## Files this skill writes / moves

| File | Purpose |
|---|---|
| `Plans/PLAN_<timestamp>_<stem>.md` | Structured action plan per item |
| `Done/<filename>` | Completed items (Full Auto) |
| `Pending_Approval/<filename>` | Items requiring human sign-off |
| `Dashboard.md` | Updated in place |
