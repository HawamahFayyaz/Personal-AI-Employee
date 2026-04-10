# Skill: Reasoning Loop

## Purpose

This skill is the AI Employee's core cognitive engine. It is a more deliberate,
auditable form of processing than `process_needs_action` — every decision is
traced to a specific rule, every plan is self-contained enough for a fresh
Claude instance to execute without re-reading context, and the reasoning is
written _into_ the plan rather than staying in working memory.

Use this skill when you need:
- High-stakes items (payments, client-facing emails, public posts)
- A backlog of mixed item types requiring triage
- An audit trail of exactly why each routing decision was made
- Plans that will be executed by a different Claude session or a human

The orchestrator invokes this skill automatically when `Needs_Action/` or
`Pending_Approval/` contains items. It can also be triggered manually.

---

## Trigger phrases

- "Run the reasoning loop"
- "Think through the queue"
- "Deep process Needs_Action"
- "Reason through pending items"
- "Full cycle on the vault"
- "What should I do about [file]?" — run the loop for that single file
- Any orchestrator invocation that references `reasoning_loop/SKILL.md`

---

## The six-phase cycle

```
┌─────────────────────────────────────────────────────────┐
│  PERCEIVE → CONSULT → REASON → PLAN → ACT → REPORT      │
│                                                          │
│  Runs once per trigger. All phases complete before       │
│  any files are moved or external calls are made.         │
└─────────────────────────────────────────────────────────┘
```

Work through every phase in order. Do not skip phases or combine them.
Complete all phases for **all items** in the current batch before writing
any files — reasoning first, execution second.

---

## Phase 1 — PERCEIVE

**Goal:** Build a complete, prioritised picture of everything that needs attention.

### 1.1 Scan Needs_Action/

List every `.md` file in `Needs_Action/`. For each file, read its full content
and extract these fields from the YAML frontmatter:

| Field | Purpose |
|---|---|
| `type` | Routes to the correct decision tree |
| `priority` | `critical` / `high` / `medium` / `low` |
| `status` | Skip anything that is not `pending` |
| `from` / `sender` | Flag unknown contacts |
| `subject` | Scan for trigger keywords |
| `amount` | Apply financial thresholds |
| `original_name` | Detect file type from extension |

### 1.2 Scan Pending_Approval/

List every `.md` file in `Pending_Approval/`. Read frontmatter and extract:
- `status` — `awaiting_approval` or `blocked`
- `action` — what was requested
- `created` — age of the pending item (flag if >24 hours old)
- `autonomy` — the routing decision already made

### 1.3 Build the processing queue

Sort all items by priority, then by age (oldest first within same priority):

```
Priority order: critical → high → medium → low
Age tiebreak:   oldest first (use `created` or file mtime)
```

Produce an internal table before proceeding to Phase 2:

```
PERCEIVE SUMMARY
================
Needs_Action items : N
  critical : N  (files: ...)
  high     : N  (files: ...)
  medium   : N  (files: ...)
  low      : N  (files: ...)

Pending_Approval items : N
  awaiting : N
  blocked  : N
  stale (>24h): N  (files: ...)

Processing order:
  1. [CRITICAL] FILE_x.md
  2. [HIGH]     EMAIL_y.md
  ...
```

If both queues are empty, report that and stop.

---

## Phase 2 — CONSULT

**Goal:** Load the rules you will cite in every routing decision.

Before reasoning about any item, read these two files in full:

### 2.1 Company_Handbook.md — extract these rules verbatim

Copy these rule sets into your working context. You will cite rule IDs in
every REASON entry.

| Rule ID | Rule |
|---|---|
| `COMM-1` | Always be professional and polite in all communications |
| `COMM-2` | Never send emails to unknown contacts without approval |
| `COMM-3` | Flag any message containing keywords: urgent, payment, invoice, legal, contract |
| `FIN-1` | Auto-approve recurring payments under $50 |
| `FIN-2` | Flag ALL payments over $100 for human approval |
| `FIN-3` | Flag ALL payments to new recipients regardless of amount |
| `FIN-4` | Never share banking credentials |
| `SOC-1` | Only post pre-approved content on business accounts |
| `SOC-2` | Never respond to DMs without approval |
| `SOC-3` | All scheduled posts must align with Business_Goals.md |
| `PRI-0` | Critical — Payment issues, security alerts → Notify immediately |
| `PRI-1` | High — Client messages, deadlines → Process within 1 hour |
| `PRI-2` | Medium — Social media, routine emails → Process within 4 hours |
| `PRI-3` | Low — Newsletters, FYI messages → Batch process daily |
| `AUT-FA` | Full Auto — Read files, create plans, update dashboard, log actions |
| `AUT-NA` | Needs Approval — Send emails, make payments, post on social media, delete files |
| `AUT-NV` | Never Auto — Legal documents, contracts, bulk sends, new payee payments |

### 2.2 Business_Goals.md — extract live context

Read `Business_Goals.md` and note:
- Monthly revenue target and current progress
- Active projects (names, deadlines, owners)
- Alert thresholds (subscription cost limits, response time SLAs)
- Any keywords that link an item to a current goal

Record a short context summary:

```
BUSINESS CONTEXT (from Business_Goals.md)
==========================================
Revenue target : $X/month  |  Current MTD: $Y
Active projects: [names]
Key thresholds : [list]
Goal-linked keywords : [list]
```

---

## Phase 3 — REASON

**Goal:** For every item in the processing queue, produce a complete, explicit
decision record before writing any plan or moving any file.

Work through items in priority order. For each item, answer every question
below and write the answers as the `## Reasoning Trace` section of the plan
you will create in Phase 4.

### 3.1 Per-item reasoning questions

**Q1 — What type of item is this?**
Determine from `type` frontmatter. If missing or ambiguous, infer from
filename prefix and content. State your conclusion and how you reached it.

**Q2 — Which priority level applies?**
Use the `priority` frontmatter if present. Cross-check against the
`PRI-*` rules: does the content (keywords, amounts, sender) justify
escalating or de-escalating the stated priority?

State the final priority and any adjustment:
```
Priority: HIGH (promoted from medium — subject contains "invoice" → COMM-3)
```

**Q3 — What specific action is needed?**
Be concrete. Not "handle email" but "draft a reply confirming receipt of
invoice #1042 and stating the 5-day payment terms." Reference any
active project or goal this connects to.

**Q4 — Does this require human approval?**

Run through this decision tree:

```
Does this involve sending a message externally?
  YES → AUT-NA (Needs Approval)
  NO  ↓

Does this involve spending money?
  YES → Is it a known recurring payment under $50? (FIN-1)
        YES → AUT-FA (Full Auto)
        NO  → Is amount > $100? (FIN-2) OR new recipient? (FIN-3)
              EITHER YES → AUT-NA (Needs Approval)
  NO  ↓

Does this involve posting on social media?
  YES → AUT-NA (Needs Approval) [SOC-1]
  NO  ↓

Does this involve a legal document, contract, or bulk send?
  YES → AUT-NV (Never Auto) — add blocked: true
  NO  ↓

Is this purely internal? (read, log, plan, organise, summarise)
  YES → AUT-FA (Full Auto)
```

**Q5 — What are the step-by-step actions required?**

List every concrete action needed to fully resolve this item.
Each step must be specific enough that a person or another Claude instance
can execute it without re-reading the source file.

**Q6 — What dependencies exist between steps?**

Identify steps that block other steps. Mark them explicitly:
- `[DEPENDS ON: Step N]`
- `[PARALLEL WITH: Step N]`
- `[REQUIRES APPROVAL BEFORE: Step N]`

**Q7 — Are there flag conditions in this item?**

Scan subject, body, and sender fields for these trigger keywords:
`urgent`, `payment`, `invoice`, `legal`, `contract`, `new payee`,
`bulk send`, `credential`, `password`, `wire transfer`

Each keyword hit adds a flag:
```
FLAGS: [invoice → COMM-3] [urgent → priority elevated to HIGH]
```

**Q8 — What is the estimated effort?**

```
Effort: LOW  (<5 minutes, single action)
        MED  (5–20 minutes, multiple steps or external lookup needed)
        HIGH (>20 minutes, multiple approvals or complex coordination)
```

### 3.2 Reasoning record format

Write the full reasoning for each item as a structured block. This block
becomes the `## Reasoning Trace` section verbatim:

```markdown
## Reasoning Trace

**Item type**: email_inbound
**Source**: alice@client.com | Subject: "Re: Invoice #1042 — Payment query"

**Q1 — Type**: Email inbound from a known client contact.
  Inferred as `email_inbound`; subject links to active project "Client A".

**Q2 — Priority**: HIGH
  Frontmatter states `medium`. Elevated to `high` because:
  - Subject contains "Invoice" → COMM-3 (flag keyword)
  - Sender matches active client in Business_Goals.md
  - PRI-1: Client messages → process within 1 hour

**Q3 — Action needed**: Draft a professional reply acknowledging receipt
  of their payment query, confirming the 30-day payment terms on invoice
  #1042, and offering a call if they need clarification.

**Q4 — Approval required**: YES → AUT-NA
  Reason: Sending an email externally → AUT-NA always applies.
  Rule COMM-2 also applies: confirm alice@client.com is a known contact
  before approving send. [Status: KNOWN — found in Business_Goals.md
  under "Client A".]

**Q5 — Steps**:
  1. Draft reply email confirming receipt of query
  2. Include invoice #1042 total and payment terms (30 days net)
  3. Offer to schedule a call: "Happy to jump on a quick call if helpful"
  4. Apply COMM-1: professional, polite tone throughout
  5. Write approval request to Pending_Approval/ for human sign-off
  6. [BLOCKED ON APPROVAL] Send email via email MCP once approved

**Q6 — Dependencies**:
  Step 6 depends on Step 5 (approval required before send).
  Steps 1–4 can be completed now (Full Auto for draft composition).

**Q7 — Flags**: [invoice → COMM-3] [known contact — COMM-2 cleared]

**Q8 — Effort**: MED — draft composition + approval file creation (~10 min)
```

---

## Phase 4 — PLAN

**Goal:** Write a self-contained Plan file for every item. Plans must be
executable by a fresh Claude instance that has never seen the original item.

### 4.1 Plan file location and naming

```
Plans/PLAN_<YYYYMMDD_HHMMSS>_<source_stem>.md
```
Example: `Plans/PLAN_20260408_143200_EMAIL_Invoice_query.md`

Use the current UTC timestamp. If two plans would have the same timestamp,
add a 1-second offset to the second.

### 4.2 Plan file format

```markdown
---
source_file: <exact filename from Needs_Action/ or Pending_Approval/>
created: <ISO 8601 UTC timestamp>
type: <item type>
priority: <critical | high | medium | low>
autonomy: <full_auto | needs_approval | never_auto>
status: in_progress
effort: <LOW | MED | HIGH>
handbook_rules: [<COMM-1>, <FIN-2>, ...]   ← all rules that applied
flags: [<keyword list or empty>]
blocked: <true | false>                     ← true only for never_auto
---

## Objective

<One precise, action-oriented sentence. Must be self-contained — readable
without the source file. Bad: "Handle the email." Good: "Reply to
alice@client.com confirming 30-day payment terms on Invoice #1042 and
request approval before sending.">

## Context

<2–4 sentences of relevant background. Include: who the sender/requester is,
what project or goal this connects to, any relevant financial or deadline
context, and any handbook flags triggered. A Claude instance starting cold
should understand the full situation after reading this section alone.>

## Reasoning Trace

<Paste the complete reasoning record from Phase 3 verbatim.>

## Steps

- [ ] 1. <First concrete action — present tense imperative>
- [ ] 2. <Second action> [DEPENDS ON: 1]
- [ ] 3. <Third action> [PARALLEL WITH: 2]
...

Each step must specify:
- The exact action (verb + object + detail)
- Which file to read/write/call if applicable
- Whether it can be done autonomously or requires approval first
- Any specific values, amounts, or content to use

## Required Approvals

<If autonomy is full_auto: "None — this plan can be executed without
human sign-off.">

<If autonomy is needs_approval:>
- [ ] Human must approve Step N before execution
- Approval file location: Pending_Approval/<filename>
- Approval triggers: <what the human is authorising specifically>

<If autonomy is never_auto:>
- 🚨 BLOCKED — This item requires explicit human review before ANY action.
- Reason: <specific handbook rule + content reason>

## Success Criteria

<How will we know this is done? Be specific.>
- [ ] <Criterion 1 — observable outcome>
- [ ] <Criterion 2>

## Rollback / If Something Goes Wrong

<What to do if execution fails or the human rejects the approval.>
```

### 4.3 Approval request files (for needs_approval items)

For every item that routes to `Pending_Approval/`, also create the approval
request file NOW (do not wait for ACT phase). Format from
`approval_system_skill/SKILL.md`:

```markdown
---
type: approval_request
action: <action_type>
platform: <platform>
created: <ISO 8601 UTC>
status: pending
<action-specific fields: to, subject, amount, recipient, etc.>
---

## Proposed Action

<Plain-language description of exactly what will happen if approved.
Should be readable by a non-technical human in 30 seconds.>

## Handbook Rules Applied

- <RULE-ID>: <rule text and why it applies here>

## Risks if Approved

<One sentence on what could go wrong. Be honest.>

## Risks if Rejected

<One sentence on what is lost or delayed.>

## To Approve
Move this file to the `/Approved` folder.

## To Reject
Move this file to the `/Rejected` folder.
```

### 4.4 Quality check before proceeding

Before moving to ACT, verify every plan satisfies these checks:

- [ ] Objective is one sentence and self-contained
- [ ] Context has enough background for a cold start
- [ ] Reasoning Trace cites at least one handbook rule by ID
- [ ] Every step starts with a verb and includes a specific target
- [ ] Dependencies are explicitly marked
- [ ] Approval section correctly reflects the autonomy decision
- [ ] Success Criteria are observable (not "it's handled")

If any plan fails a check, fix it before proceeding.

---

## Phase 5 — ACT

**Goal:** Execute everything that is `full_auto`. Queue everything else.

### 5.1 Execute full_auto plans

For items with `autonomy: full_auto`, complete all steps now in the order
defined by their dependency graph (respect `[DEPENDS ON:]` markers).

Common full_auto actions:
- Read and summarise a file's contents
- Extract structured data (contact info, amounts, dates) from a file drop
- Create or update internal tracking entries
- Log insights to a topic-specific file in `Logs/`
- Add a CRM entry for a new contact
- Cross-reference content against `Business_Goals.md` and note connections

When executing, check each step off in the Plan file:
```
- [x] 1. Summarised file contents — key points extracted (see Notes below)
```

Add an `## Execution Log` section to the plan after the Steps section:
```markdown
## Execution Log

- `<ISO timestamp>` — Step 1 complete: <brief result>
- `<ISO timestamp>` — Step 3 complete: <brief result>
```

After all steps are complete, update Plan frontmatter: `status: done`

### 5.2 Queue needs_approval and never_auto items

Move the source file from `Needs_Action/` to `Pending_Approval/`:
- Update frontmatter `status` to `awaiting_approval` (or `blocked` for never_auto)
- The approval request file was already written in Phase 4

Do not execute any steps that are `[BLOCKED ON APPROVAL]`.

### 5.3 Move completed full_auto items to Done/

After all steps execute successfully:
- Update source file frontmatter: `status: done`, `completed_at: <ISO UTC>`
- Move source file to `Done/`
- Update Plan frontmatter: `status: done`

---

## Phase 6 — REPORT

**Goal:** Leave a complete, accurate record of the cycle.

### 6.1 Update Dashboard.md

Apply the `update_dashboard` skill in full. The dashboard must reflect the
state of the vault _after_ this cycle completes:
- `Pending_Approval/` count includes any items just queued
- `Done/` count includes any items just completed
- Recent Activity shows this reasoning loop run

### 6.2 Write the cycle summary

At the end of the run, print this structured summary:

```
## Reasoning Loop — Cycle Complete
=====================================
Timestamp : <ISO UTC>
Items processed : N

PERCEIVED
  Needs_Action    : N items
  Pending_Approval: N items

REASONED & PLANNED
  Plans created   : N
  Full Auto       : N  →  Done/ immediately
  Needs Approval  : N  →  Pending_Approval/ (awaiting human)
  Never Auto      : N  →  Pending_Approval/ (blocked)

ACTED (Full Auto completions)
  ✅ <filename> — <one-line result>
  ✅ <filename> — <one-line result>

QUEUED (Pending human approval)
  ⏳ <filename> → Pending_Approval/<approval_file>
     Rule: <handbook rule ID> | Risks: <brief>
  🚨 <filename> → Pending_Approval/<approval_file> [BLOCKED]
     Rule: AUT-NV | Reason: <brief>

FLAGS raised this cycle
  ⚠️ <filename> — <flag keyword> → <what action was taken>

REPORT
  Dashboard.md  : updated
  Plans created : <list of plan filenames>
  Log entry     : Logs/<YYYY-MM-DD>.json
=====================================
```

### 6.3 Append to the JSON activity log

Append to `Logs/<YYYY-MM-DD>.json`:

```json
{
  "timestamp": "<ISO UTC>",
  "action_type": "reasoning_loop",
  "items_perceived": N,
  "full_auto_completed": N,
  "needs_approval_queued": N,
  "never_auto_blocked": N,
  "plans_created": ["PLAN_x.md", "PLAN_y.md"],
  "flags_raised": ["<keyword> in <filename>"],
  "summary": "<one sentence describing this cycle>"
}
```

---

## Plan portability requirement

Every plan produced by this skill must satisfy the **cold-start test**:

> A Claude instance that has never seen the original item, never read
> `Company_Handbook.md`, and has no conversation history must be able to
> execute the plan correctly using only the plan file, `Business_Goals.md`,
> and access to the vault folders.

To pass this test, every plan must include:
1. The full reasoning trace (so the executor understands _why_ each step exists)
2. Exact file paths, API names, or tool names for each step
3. All relevant handbook rules cited by ID _and_ quoted by text
4. The success criteria (so the executor knows when to stop)
5. The rollback instructions (so the executor knows what to do if something breaks)

---

## Example Scenarios

The following three worked examples show the complete reasoning chain. They
are templates — use them as mental models when processing real items.

---

### Example A: Client email requiring a reply

**Source file:** `Needs_Action/EMAIL_20260408T143200Z_Invoice_Payment_query.md`

```yaml
---
type: email_inbound
from: alice@clientco.com
subject: "Re: Invoice #1042 — Quick question on payment terms"
received: 2026-04-08T14:32:00+00:00
priority: medium
status: pending
message_id: 18c3f9a0b2d5e601
---

## Email Content

Hi,

Hope you're well. Just had a question about Invoice #1042 that we received
last week — are the payment terms 30 or 45 days? Our accounts team needs
to confirm before processing.

Thanks,
Alice
```

---

**PERCEIVE output for this item:**
```
[HIGH] EMAIL_20260408T143200Z_Invoice_Payment_query.md
  type: email_inbound | from: alice@clientco.com
  subject contains: "Invoice" → flag keyword detected
  original priority: medium
```

**CONSULT — rules that apply:**
- `COMM-1`: Professional and polite in all communications
- `COMM-2`: Never send emails to unknown contacts without approval
- `COMM-3`: Subject contains "Invoice" → flag
- `PRI-1`: Client messages → High → process within 1 hour
- `AUT-NA`: Sending an external email → Needs Approval

**REASON — Q&A:**

Q1 — Type: `email_inbound`. Confirmed from frontmatter. Sender is a client contact.

Q2 — Priority: Elevated from `medium` to `HIGH`.
  - "Invoice" in subject triggers COMM-3.
  - Client message triggers PRI-1 (process within 1 hour).
  - alice@clientco.com: check Business_Goals.md — found under active project "Client A". Known contact → COMM-2 cleared.

Q3 — Action: Draft a reply confirming that Invoice #1042 has 30-day net payment
  terms (the standard from Business_Goals.md), and invite Alice to reach out if
  she has further questions.

Q4 — Approval: YES → `AUT-NA`. Sending an external email is always Needs Approval.
  Alice is a known contact so COMM-2 is satisfied, but the send itself still
  requires the human gate.

Q5 — Steps:
  1. Confirm payment terms from Business_Goals.md (30-day net standard)
  2. Compose reply: acknowledge query, state terms, offer follow-up
  3. Apply COMM-1: professional, warm, brief
  4. Write approval request to Pending_Approval/EMAIL_<date>_Invoice_1042_reply.md
  5. [BLOCKED ON APPROVAL] Send via email MCP `send_email` tool once approved

Q6 — Dependencies: Step 5 depends on Step 4 approval. Steps 1–4 parallel.

Q7 — Flags: `invoice` → COMM-3. `known contact` → COMM-2 cleared.

Q8 — Effort: MED

---

**PLAN produced:**

`Plans/PLAN_20260408_143500_EMAIL_Invoice_Payment_query.md`

```markdown
---
source_file: EMAIL_20260408T143200Z_Invoice_Payment_query.md
created: 2026-04-08T14:35:00+00:00
type: email_inbound
priority: high
autonomy: needs_approval
status: in_progress
effort: MED
handbook_rules: [COMM-1, COMM-2, COMM-3, PRI-1, AUT-NA]
flags: [invoice]
blocked: false
---

## Objective

Reply to alice@clientco.com confirming 30-day net payment terms on Invoice
#1042, applying COMM-1 professional tone, pending human approval before send.

## Context

Alice from ClientCo emailed asking whether Invoice #1042 (sent ~1 week ago)
has 30- or 45-day payment terms. Her accounts team is waiting to process
payment. Alice is a known active client (Business_Goals.md, "Client A").
Standard payment terms are 30-day net. Subject flagged under COMM-3 (contains
"Invoice") causing a priority elevation from medium to high under PRI-1.
The reply must not be sent without human approval (AUT-NA).

## Reasoning Trace

[full Q&A block from above]

## Steps

- [ ] 1. Confirm payment terms: read Business_Goals.md, locate standard terms
         for "Client A" or global default. Expected: 30-day net.
- [ ] 2. Compose reply email:
         To: alice@clientco.com
         Subject: Re: Invoice #1042 — Quick question on payment terms
         Body: "Hi Alice, Thanks for reaching out. Invoice #1042 is on our
         standard 30-day net terms, so payment is due by [date = received +
         30 days = 2026-05-07]. Please don't hesitate to get in touch if
         your accounts team needs anything else — happy to help. Best regards"
         Rule applied: COMM-1 (professional, polite)
- [ ] 3. Write approval request file to:
         Pending_Approval/EMAIL_20260408_Invoice_1042_reply.md
         Include: full email text, to/subject fields in frontmatter, risks section
- [ ] 4. [DEPENDS ON: Step 3 approval] Send email via MCP tool `send_email`:
         to="alice@clientco.com", subject="Re: ...", body=<composed in Step 2>

## Required Approvals

- [ ] Human must approve Step 4 before email is sent
- Approval file: Pending_Approval/EMAIL_20260408_Invoice_1042_reply.md
- Human is authorising: sending an external email to alice@clientco.com
  containing the 30-day payment terms confirmation

## Success Criteria

- [ ] Alice receives a reply within 1 hour of the original email (PRI-1 SLA)
- [ ] Reply text accurately states payment due date (received + 30 days)
- [ ] Email is sent from the correct account (Gmail via email MCP)

## Rollback / If Something Goes Wrong

If human rejects: do not send. Log rejection in Logs/. Flag for human to
draft an alternative reply or call Alice directly.
If Gmail API fails: retry once. If retry fails, log error and notify via
Dashboard.md alert.
```

**ACT for this item:**
- Steps 1–3 are Full Auto (compose + write approval file). Execute now.
- Step 4 is blocked on approval. Move source file to `Pending_Approval/`.

---

### Example B: File drop requiring categorisation

**Source file:** `Needs_Action/FILE_20260408T091500Z_Q1_meeting_notes.txt.md`

```yaml
---
type: file_drop
original_name: Q1_meeting_notes.txt
size: 2847
dropped_at: 2026-04-08T09:15:00+00:00
priority: medium
status: pending
---

New file dropped for processing: Q1_meeting_notes.txt

## Suggested Actions
- [ ] Review file contents
- [ ] Process or delegate
- [ ] Move to Done when complete
```

**(Companion file `FILE_20260408T091500Z_Q1_meeting_notes.txt` contains the
raw meeting notes.)**

---

**PERCEIVE:**
```
[MEDIUM] FILE_20260408T091500Z_Q1_meeting_notes.txt.md
  type: file_drop | filename: Q1_meeting_notes.txt
  No flag keywords in filename. Extension: .txt (text document)
  No financial amounts present.
```

**CONSULT — rules that apply:**
- `AUT-FA`: File drops → Full Auto (read, plan, log)
- `PRI-2`: Medium priority → process within 4 hours
- `COMM-3`: Scan content for flag keywords before confirming Full Auto

**REASON — Q&A:**

Q1 — Type: `file_drop`. Filename `Q1_meeting_notes.txt` → internal document,
  text file. No legal/contract keywords detected in filename.

Q2 — Priority: MEDIUM confirmed. No escalation triggers. PRI-2 applies:
  process within 4 hours.

Q3 — Action:
  1. Read the companion `.txt` file for content.
  2. Scan for flag keywords (urgent, payment, invoice, legal, contract).
  3. Extract key information: participants, decisions made, action items, dates.
  4. Write a structured summary to `Logs/meeting_notes_Q1_2026.md`.
  5. If any action items reference active projects in Business_Goals.md,
     create follow-up items in Needs_Action/ for each.

Q4 — Approval: NO → `AUT-FA`. Reading and summarising an internal file is
  Full Auto (AUT-FA). No external communication or financial action involved.
  _Condition_: if scanning the file content reveals flag keywords (payment,
  legal, contract), re-evaluate and escalate to Needs Approval before taking
  any action on those items.

Q5 — Steps:
  1. Read `Needs_Action/FILE_20260408T091500Z_Q1_meeting_notes.txt` in full
  2. Scan content for flag keywords: none → confirm Full Auto
  3. Extract: participants, decisions, action items with owners and dates
  4. Write structured summary to `Logs/meeting_notes_Q1_2026.md`
  5. Cross-reference action items against Business_Goals.md active projects
  6. For each action item that references an active project: create a
     `Needs_Action/FOLLOWUP_<date>_<action>.md` file
  7. Update Plan: mark all steps done
  8. Move source file + companion to `Done/`

Q6 — Dependencies: Steps 3–6 depend on Step 1 (must read file first).
  Steps 4, 5, 6 can proceed in parallel after Step 3.

Q7 — Flags: none detected in filename. Will confirm after reading content in Step 2.

Q8 — Effort: MED (file read + summarisation + possible follow-up creation)

---

**PLAN produced:**

`Plans/PLAN_20260408_092000_FILE_Q1_meeting_notes.md`

```markdown
---
source_file: FILE_20260408T091500Z_Q1_meeting_notes.txt.md
created: 2026-04-08T09:20:00+00:00
type: file_drop
priority: medium
autonomy: full_auto
status: in_progress
effort: MED
handbook_rules: [AUT-FA, PRI-2, COMM-3]
flags: []
blocked: false
---

## Objective

Read, categorise, and extract structured action items from the dropped file
Q1_meeting_notes.txt; create follow-up tasks for any identified action items
linked to active Business_Goals.md projects.

## Context

A file called Q1_meeting_notes.txt was dropped into Inbox/ at 09:15 UTC.
It is an internal text document (no external communication involved). Medium
priority under PRI-2. Full Auto applies (AUT-FA): reading and summarising
internal files requires no human approval. If flag keywords are found inside
the file during Step 2, re-evaluate before proceeding.

## Reasoning Trace

[full Q&A block from above]

## Steps

- [ ] 1. Read companion file: Needs_Action/FILE_20260408T091500Z_Q1_meeting_notes.txt
         If file is missing: log error, mark plan blocked, update Dashboard.
- [ ] 2. Scan full content for flag keywords: urgent, payment, invoice, legal,
         contract. If found: STOP full_auto — create Pending_Approval entry
         instead and re-route. If not found: proceed.
- [ ] 3. Extract structured data:
         - Participants (names, roles if mentioned)
         - Date and location of meeting
         - Decisions made (numbered list)
         - Action items (owner, description, deadline if stated)
- [ ] 4. Write summary to Logs/meeting_notes_Q1_2026.md using this format:
         ## Meeting: Q1 Review — 2026-04-08
         **Participants**: ...
         **Decisions**: 1. ... 2. ...
         **Action Items**: | Owner | Task | Deadline |
- [ ] 5. [PARALLEL WITH: 4] Cross-reference each action item against
         Business_Goals.md active projects. Note connections.
- [ ] 6. [DEPENDS ON: 3] For each action item with a named owner and deadline:
         Create Needs_Action/FOLLOWUP_<YYYYMMDD>_<slug>.md with:
         type: follow_up | priority: medium | owner: <name> | due: <date>
         body: action item description + meeting context
- [ ] 7. Update this plan: mark steps done, add Execution Log entries
- [ ] 8. Update source file frontmatter: status: done, completed_at: <ISO UTC>
- [ ] 9. Move Needs_Action/FILE_20260408T091500Z_Q1_meeting_notes.txt.md
         and Needs_Action/FILE_20260408T091500Z_Q1_meeting_notes.txt
         both to Done/

## Required Approvals

None — this plan can be executed without human sign-off.
Re-evaluate if Step 2 finds flag keywords.

## Success Criteria

- [ ] Structured summary written to Logs/meeting_notes_Q1_2026.md
- [ ] All action items extracted and listed
- [ ] Follow-up Needs_Action files created for actionable items
- [ ] Both source files moved to Done/
- [ ] No flag keywords found (or if found, re-routed correctly)

## Rollback / If Something Goes Wrong

If companion .txt file is missing: log the gap, mark as done with note
"companion file not found", move .md to Done/.
If follow-up creation fails: log the failed items, do not block completion
of the main plan.
```

**ACT for this item:**
- All steps are Full Auto. Execute immediately.
- After completion, move both files to `Done/`.

---

### Example C: Social media posting request

**Source file:** `Needs_Action/SOCIAL_20260408T160000Z_LinkedIn_product_update.md`

```yaml
---
type: social_media
platform: linkedin
requested_by: orchestrator
topic: "Q1 revenue milestone reached"
drafted_content: >
  Thrilled to share that we hit our Q1 revenue target! This milestone
  reflects the trust our clients place in us. Exciting things ahead.
  #growth #milestone
priority: medium
status: pending
---

Post the above to LinkedIn as part of our Q1 comms plan.
```

---

**PERCEIVE:**
```
[MEDIUM] SOCIAL_20260408T160000Z_LinkedIn_product_update.md
  type: social_media | platform: linkedin
  No flag keywords. Drafted content provided.
  Original priority: medium
```

**CONSULT — rules that apply:**
- `SOC-1`: Only post PRE-APPROVED content on business accounts
- `SOC-3`: All scheduled posts must align with Business_Goals.md
- `AUT-NA`: Posting on social media → Needs Approval, always
- `PRI-2`: Medium → process within 4 hours

**REASON — Q&A:**

Q1 — Type: `social_media`. Platform: LinkedIn. A drafted post is provided.

Q2 — Priority: MEDIUM confirmed. No escalation conditions. PRI-2: within 4h.

Q3 — Action:
  1. Verify the draft content aligns with Business_Goals.md (SOC-3):
     - Does "Q1 revenue milestone reached" match actual current status?
       Check Business_Goals.md "Current MTD" and "Monthly goal" fields.
     - If actual numbers support the claim → proceed.
     - If numbers don't support the claim → modify or reject.
  2. Check whether the LinkedIn API credentials exist in .env.
  3. Generate the approval request for human sign-off (SOC-1 requires this).
  4. [BLOCKED ON APPROVAL] Post via `linkedin_poster.py generate` or
     directly via LinkedIn API once approved.

Q4 — Approval: YES → `AUT-NA`. Social media posting ALWAYS requires approval.
  SOC-1 is absolute: "Only post pre-approved content." No exceptions.
  Even a clearly appropriate, well-drafted post must pass the human gate.

Q5 — Steps:
  1. Read Business_Goals.md — verify Q1 revenue claim is accurate
  2. Review draft content for tone (COMM-1), accuracy (SOC-3), hashtag count
  3. If content is accurate and appropriate: create LinkedIn approval file
  4. If content is inaccurate: modify draft to match actual figures, then
     create approval file with a note explaining the change
  5. Write approval request to Pending_Approval/LINKEDIN_<date>.md
  6. [BLOCKED ON APPROVAL] Execute: python -m Watchers.linkedin_poster publish

Q6 — Dependencies: Steps 3/4 depend on Step 1 (must verify accuracy first).
  Step 6 is fully blocked on approval.

Q7 — Flags: none detected. Platform is LinkedIn (not DM → SOC-2 not triggered).

Q8 — Effort: LOW (content is pre-drafted; main work is verification + approval file)

---

**PLAN produced:**

`Plans/PLAN_20260408_160500_SOCIAL_LinkedIn_Q1_milestone.md`

```markdown
---
source_file: SOCIAL_20260408T160000Z_LinkedIn_product_update.md
created: 2026-04-08T16:05:00+00:00
type: social_media
priority: medium
autonomy: needs_approval
status: in_progress
effort: LOW
handbook_rules: [SOC-1, SOC-3, COMM-1, AUT-NA, PRI-2]
flags: []
blocked: false
---

## Objective

Verify the Q1 milestone LinkedIn post draft against actual revenue figures,
adjust if needed, and create an approval request — do not post until human
approves.

## Context

A pre-drafted LinkedIn post celebrating a "Q1 revenue milestone" was submitted
for posting. Platform is LinkedIn (public business account). SOC-1 is absolute:
no social media post goes live without explicit human approval, regardless of
quality. SOC-3 requires verifying the claim against Business_Goals.md current
data before the approval request is written — we must not ask for approval of
inaccurate content. Medium priority under PRI-2 (4-hour window).

## Reasoning Trace

[full Q&A block from above]

## Steps

- [ ] 1. Read Business_Goals.md: locate "Revenue Target" and "Current MTD".
         Expected fields: `Monthly goal`, `Current MTD`.
         Decision gate:
           MTD >= Monthly goal → claim "milestone reached" is accurate → proceed
           MTD < Monthly goal  → modify draft to reflect actual progress instead
- [ ] 2. Review draft for COMM-1 compliance (professional, no controversial claims)
         and SOC-3 alignment (content matches business goals context).
         Check: hashtag count ≤ 5, no pricing/financial figures in the post
         (SOC-3 — never share specifics that aren't approved for public).
- [ ] 3. If accurate: use draft as-is.
         If inaccurate: rewrite to accurate version, e.g.:
         "We're making strong progress toward our Q1 revenue goal — grateful
         to our clients for their continued trust. #growth #milestone"
- [ ] 4. Write approval request:
         File: Pending_Approval/LINKEDIN_<YYYY-MM-DD>.md
         action: linkedin_post
         Include: final post text, accuracy verification result, word count,
         which handbook rules were checked and confirmed
- [ ] 5. [BLOCKED ON APPROVAL] Once approved, execute:
         python -m Watchers.linkedin_poster publish
         OR: ApprovalWatcher will auto-dispatch when file reaches Approved/
- [ ] 6. Move source file to Pending_Approval/ with status: awaiting_approval

## Required Approvals

- [ ] Human must approve Step 5 before any post is published
- Approval file: Pending_Approval/LINKEDIN_<date>.md
- Human is authorising: publishing the exact post text shown in the approval
  file to the LinkedIn business account. Any post text changes must go through
  a new approval cycle.
- Rule basis: SOC-1 — "Only post pre-approved content on business accounts"

## Success Criteria

- [ ] Accuracy check against Business_Goals.md completed and documented
- [ ] Approval file written with final post text
- [ ] Post goes live only after human moves file to Approved/
- [ ] LinkedIn post ID logged in Logs/approval_watcher.log after publish

## Rollback / If Something Goes Wrong

If human rejects: move approval file to Rejected/. Log reason. Do not post.
If accuracy check fails (MTD doesn't support claim): modify draft to be
accurate, flag the change in the approval file Notes section.
If LinkedIn API fails after approval: log DISPATCH_FAILED in
approval_watcher.log; file stays in Approved/ for automatic retry.
```

**ACT for this item:**
- Steps 1–4 are Full Auto (read, verify, write approval file). Execute now.
- Step 5 is blocked on approval. Move source to `Pending_Approval/`.
- ApprovalWatcher will handle dispatch when human moves to `Approved/`.

---

## Quick reference — decision rules

```
Item received
    │
    ▼
Scan for flag keywords (COMM-3)?
    YES → elevate priority, note flags
    │
    ▼
Type routing:
  email_inbound  → Full Auto (read/log); Needs Approval (any reply)
  email_outbound → Needs Approval always
  file_drop      → Full Auto (unless content flags trigger re-evaluation)
  social_media   → Needs Approval always (SOC-1 is absolute)
  payment        → Full Auto if recurring <$50 (FIN-1)
                   Needs Approval if >$100 (FIN-2) or new recipient (FIN-3)
  legal/contract → Never Auto (AUT-NV) — add blocked: true
  calendar       → Full Auto
  unknown        → Needs Approval (when in doubt, escalate)
    │
    ▼
autonomy = full_auto?
    YES → execute now → Done/
    NO  → write approval request → Pending_Approval/
          (ApprovalWatcher handles dispatch when human approves)
```

---

## Files this skill reads

| File | Purpose |
|---|---|
| `Needs_Action/*.md` | Primary work queue |
| `Pending_Approval/*.md` | Items awaiting human sign-off (check age/staleness) |
| `Company_Handbook.md` | Rules consulted and cited in every decision |
| `Business_Goals.md` | Live business context for content verification |
| `Dashboard.md` | Updated in Phase 6 |
| `Logs/watcher_YYYY-MM-DD.log` | Watcher status for dashboard update |

## Files this skill writes

| File | Purpose |
|---|---|
| `Plans/PLAN_<timestamp>_<stem>.md` | Self-contained reasoning + execution plan |
| `Pending_Approval/<action_file>.md` | Approval request for needs_approval items |
| `Done/<source_file>` | Archived completed items (full_auto) |
| `Logs/meeting_notes_*.md` | Structured summaries from file drops (example) |
| `Logs/<YYYY-MM-DD>.json` | Cycle activity log entry |
| `Dashboard.md` | Full refresh via update_dashboard skill |
