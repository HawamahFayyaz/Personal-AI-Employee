# Skill: Approval System (Human-in-the-Loop)

## What it does

The approval system is the safety layer between the AI employee and the outside
world. Every action that touches external systems — sending emails, posting to
LinkedIn, making payments — must pass through a human checkpoint before it
executes.

The `ApprovalWatcher` automates the _after_ side: once you move a file to
`Approved/` or `Rejected/`, the watcher takes over and executes (or logs) the
outcome within seconds.

---

## Complete approval workflow

```
AI generates action
        │
        ▼
  Pending_Approval/
  LINKEDIN_2026-04-08.md       ← you review this file
  (status: pending)
        │
   ┌────┴────┐
   │         │
   ▼         ▼
Approved/  Rejected/
   │         │
   ▼         ▼
ApprovalWatcher detects file (within ~10 seconds)
   │         │
   ▼         ▼
Dispatch    Log rejection
handler     (no API calls)
   │         │
   └────┬────┘
        ▼
      Done/
  LINKEDIN_2026-04-08.md
  (status: approved | rejected)
  + ## Resolution section appended
        │
        ▼
  Dashboard.md updated
  Logs/approval_watcher.log updated
```

---

## File naming conventions

All approval request files follow this pattern:

```
<ACTION_TYPE>_<YYYY-MM-DD>[_<counter>].md
```

| File name | Action type | Source |
|---|---|---|
| `LINKEDIN_2026-04-08.md` | `linkedin_post` | `linkedin_poster.py generate` |
| `EMAIL_2026-04-08_Re_Invoice.md` | `email_send` | Orchestrator / Claude |
| `PAYMENT_2026-04-08_Vendor_XYZ.md` | `payment` | Payment handler (Gold tier) |

If multiple requests are generated on the same day a counter is appended:
`LINKEDIN_2026-04-08_01.md`, `LINKEDIN_2026-04-08_02.md`, etc.

---

## Approval file format (full schema)

Every file in `Pending_Approval/` must use this structure:

```markdown
---
type: approval_request
action: <action_type>         ← dispatches to the correct handler
platform: <platform>          ← informational (linkedin, gmail, stripe, …)
created: <ISO 8601 timestamp>
status: pending
# Action-specific fields (see per-action schemas below):
to: recipient@example.com     ← email_send only
subject: Email subject         ← email_send only
cc: optional@example.com      ← email_send only
bcc: optional@example.com     ← email_send only
amount: 150.00                 ← payment only
recipient: Vendor Name         ← payment only
---

## Proposed Post       ← for linkedin_post
<post content here>

## Email Body          ← for email_send
<email body here>

## To Approve
Move this file to the `/Approved` folder.

## To Reject
Move this file to the `/Rejected` folder.
```

### Action type schemas

#### `linkedin_post`

```yaml
action: linkedin_post
platform: linkedin
```
Body must contain a `## Proposed Post` section. Content between that header
and `## To Approve` becomes the LinkedIn post text.

#### `email_send`

```yaml
action: email_send
platform: gmail
to: recipient@example.com      # required
subject: Subject line           # required
cc: cc@example.com              # optional
bcc: bcc@example.com            # optional
```
Body must contain a `## Email Body` section. Content between that header and
`## To Approve` becomes the email body.

#### `payment` (placeholder — Gold tier)

```yaml
action: payment
platform: stripe
amount: 150.00
recipient: Vendor Name
```
Currently logs the approval; payment execution is not yet implemented.

---

## How to create an approval request

### Generated automatically

| Action | How it's triggered |
|---|---|
| LinkedIn post | `python -m Watchers.linkedin_poster generate` |
| Email send | Orchestrator routes `email_outbound` type items to `Pending_Approval/` |
| Payment | Payment handler (Gold tier placeholder) |

### Created manually by Claude

Claude Code can create approval requests directly when it determines an action
requires human sign-off (per `Company_Handbook.md` autonomy rules):

```markdown
---
type: approval_request
action: email_send
platform: gmail
to: client@example.com
subject: Invoice follow-up
created: 2026-04-08T10:00:00+00:00
status: pending
---

## Email Body

Hi,

Just following up on invoice #1042 sent last week.
Please let me know if you need any additional information.

Best regards

## To Approve
Move this file to the `/Approved` folder.

## To Reject
Move this file to the `/Rejected` folder.
```

Save this to `Pending_Approval/EMAIL_2026-04-08_Invoice_followup.md`.

---

## How to approve or reject

### Approve

Move (rename) the file from `Pending_Approval/` to `Approved/`:

```bash
# From vault root:
mv "Pending_Approval/LINKEDIN_2026-04-08.md" "Approved/"

# Or in your file manager / Obsidian: drag the file to the Approved/ folder.
```

The `ApprovalWatcher` detects the new file within ~10 seconds and:
1. Reads the `action` field from frontmatter.
2. Calls the appropriate dispatcher.
3. Appends a `## Resolution` section to the file.
4. Moves it to `Done/`.
5. Updates `Dashboard.md` and `Logs/approval_watcher.log`.

### Reject

Move the file to `Rejected/`:

```bash
mv "Pending_Approval/LINKEDIN_2026-04-08.md" "Rejected/"
```

The watcher logs the rejection and moves the file to `Done/` — no external
API call is made.

---

## Running the watcher

### Standalone

```bash
cd /mnt/d/HACKATHON_00/AI_Employee_Vault
python -m Watchers.approval_watcher
# or
python Watchers/approval_watcher.py
```

### Alongside other watchers (in `run_watchers.py`)

```python
from Watchers.approval_watcher import ApprovalWatcher

approval_watcher = ApprovalWatcher(vault_path=str(VAULT_ROOT), check_interval=10)
watchers = [fs_watcher, gmail_watcher, approval_watcher]
```

Each watcher is started in its own daemon thread by the existing runner loop.

### DRY_RUN mode

Set `DRY_RUN=true` in `.env`. The watcher will:
- Log what it _would_ do for every approved action.
- Still write to `Logs/approval_watcher.log`.
- Still move files to `Done/`.
- **Never** call LinkedIn, Gmail, or payment APIs.

---

## Log files

### `Logs/approval_watcher.log`

Structured log of every approval and rejection event:

```
[2026-04-08T10:05:33+00:00] APPROVED_EXECUTED
  file   : LINKEDIN_2026-04-08.md
  action : linkedin_post
  detail : LinkedIn post published (ID: urn:li:share:7XXXXXXX)
————————————————————————————————————————————————————————————

[2026-04-08T10:07:12+00:00] REJECTED
  file   : EMAIL_2026-04-08_Cold_outreach.md
  action : email_send
  detail : Human rejected this request.
————————————————————————————————————————————————————————————

[2026-04-08T10:09:01+00:00] DISPATCH_FAILED
  file   : EMAIL_2026-04-08_Invoice.md
  action : email_send
  detail : Gmail send error: token expired — re-run gmail_auth.py
————————————————————————————————————————————————————————————
```

| Event label | Meaning |
|---|---|
| `APPROVED_EXECUTED` | Handler ran successfully |
| `REJECTED` | Human rejected; no API call made |
| `DISPATCH_FAILED` | Handler raised an error; file left in `Approved/` for retry |
| `DRY_RUN_POST` | Dry-run; would-be action logged, nothing sent |

### `Dashboard.md` updates

After each event the watcher updates three sections in place:

| Section | What changes |
|---|---|
| `## Awaiting Approval` | Rebuilt from current `Pending_Approval/` file list |
| `## Recent Activity` | New entry prepended |
| `## Quick Stats` → Approvals Waiting | Updated count |
| `## Quick Stats` → Tasks Completed Today | Updated count |

---

## Dispatcher reference

| `action` value | Handler | External call |
|---|---|---|
| `linkedin_post` | `_dispatch_linkedin()` | `linkedin_poster.post_to_linkedin()` → LinkedIn UGC API |
| `email_send` | `_dispatch_email_send()` | `gmail_auth.get_gmail_service()` → Gmail send API |
| `payment` | `_dispatch_payment()` | _Placeholder — no API call yet_ |
| _(unknown)_ | None | Logged and moved to `Done/` unexecuted |

To add a new action type, register a function in `_DISPATCHERS` inside
`approval_watcher.py`:

```python
def _dispatch_my_action(filepath, fm, body, logger):
    # ... do the work ...
    return success: bool, detail: str

_DISPATCHERS["my_action"] = _dispatch_my_action
```

---

## Retry behaviour

| Outcome | Where file ends up | Can retry? |
|---|---|---|
| Success | `Done/` | No (archived) |
| Dispatch failed | `Approved/` | Yes — watcher re-detects on next poll |
| Rejected | `Done/` | No (archived) |
| Unknown action | `Done/` | No (logged) |

Failed dispatches are intentionally left in `Approved/` so the underlying
problem (expired token, missing env var, API outage) can be fixed and the
approval re-triggers without manual re-approval.

---

## Security considerations

### Principle of least privilege

- The watcher only executes actions that _already have explicit human approval_
  (file presence in `Approved/`). It never initiates actions on its own.
- `DRY_RUN=true` should always be the default in `.env`. Switch to `false`
  only after testing the full flow.

### File-based authorization

The approval gate relies on filesystem access to `Approved/`. This means:
- Only users with write access to the vault can approve actions.
- Do not expose the vault directory over an unauthenticated network share.
- On shared machines, set vault directory permissions to `700` (owner only).

### Credential protection

- `credentials.json` and `token.json` contain OAuth secrets. Keep them
  out of version control (add to `.gitignore`).
- `LINKEDIN_ACCESS_TOKEN` in `.env` grants posting access to your LinkedIn
  account — treat it like a password.
- Never log credential values. The watcher logs filenames and action types
  only — never frontmatter field values that might contain PII.

### Injection via approval file content

The watcher reads frontmatter fields directly as API parameters. A maliciously
crafted approval file could change the email recipient or LinkedIn post content.
Since only you (the vault owner) can create files in `Pending_Approval/`, this
risk is limited to self-inflicted misconfiguration — but always **read the file
before approving it**.

### Audit trail

Every approval and rejection is written to `Logs/approval_watcher.log` with:
- Exact ISO timestamp
- Action type
- Outcome and detail string

This log is append-only (never truncated by the watcher) and provides a
complete record of all automated actions taken on your behalf.

---

## Error handling reference

| Scenario | Behaviour |
|---|---|
| Approval file has no `action` field | Treated as `unknown`; moved to `Done/` unexecuted |
| No dispatcher registered for action | Warning logged; moved to `Done/` unexecuted |
| Dispatcher raises an exception | Error logged; file left in `Approved/` for retry |
| Gmail token expired | `DISPATCH_FAILED` logged; run `gmail_auth.py` to refresh |
| LinkedIn token expired | `DISPATCH_FAILED` logged; re-run OAuth flow in `linkedin_poster.py` |
| Dashboard.md missing | Dashboard update skipped (no error raised) |
| File unreadable (permissions) | Error logged; file skipped |

---

## Files written by this skill

| File | Purpose |
|---|---|
| `Done/<filename>` | Archived approval/rejection with `## Resolution` appended |
| `Logs/approval_watcher.log` | Structured audit log of all events |
| `Dashboard.md` | Targeted in-place updates (3 sections) |
