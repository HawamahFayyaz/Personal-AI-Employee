# AI Employee Vault — Architecture

> Last updated: 2026-04-10

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Component Descriptions](#2-component-descriptions)
   - [Perception Layer — Watchers](#21-perception-layer--watchers)
   - [Reasoning Layer — Claude Code & Skills](#22-reasoning-layer--claude-code--skills)
   - [Action Layer — MCP Servers](#23-action-layer--mcp-servers)
   - [Orchestration Layer](#24-orchestration-layer)
3. [Data Flow Diagrams](#3-data-flow-diagrams)
   - [Email arrives → processed → reply sent](#31-email-arrives--processed--reply-sent)
   - [Invoice request → generated → approved → sent](#32-invoice-request--generated--approved--sent)
   - [Weekly audit → CEO briefing generated](#33-weekly-audit--ceo-briefing-generated)
4. [Folder Structure](#4-folder-structure)
5. [Security Architecture](#5-security-architecture)
6. [Error Handling Strategy](#6-error-handling-strategy)
7. [Scaling Considerations](#7-scaling-considerations)

---

## 1. System Overview

The AI Employee Vault is a **locally-hosted autonomous agent** built on top of
Claude Code.  It monitors external services, processes incoming events using
Claude's reasoning, routes actions through a human approval gate, then
dispatches results via MCP servers.

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                         AI EMPLOYEE VAULT                                    ║
║                                                                              ║
║  ┌─────────────────────────────────────────────────────────────────────┐    ║
║  │  EXTERNAL WORLD                                                      │    ║
║  │  Gmail · LinkedIn · Twitter/X · Facebook/Instagram · Odoo 19        │    ║
║  └──────────┬────────────────────────────────────────────┬─────────────┘    ║
║             │ inbound events                             │ outbound actions  ║
║             ▼                                            ▲                   ║
║  ┌──────────────────────────┐          ┌─────────────────────────────────┐  ║
║  │   PERCEPTION LAYER       │          │   ACTION LAYER                  │  ║
║  │   (Python Watchers)      │          │   (Node.js MCP Servers)         │  ║
║  │                          │          │                                 │  ║
║  │  FilesystemWatcher       │          │  email-mcp   (Gmail)            │  ║
║  │  GmailWatcher            │          │  odoo-mcp    (ERP/Accounting)   │  ║
║  │  TwitterWatcher          │          │  social-mcp  (Facebook/IG)      │  ║
║  │  SocialMediaWatcher      │          │  twitter-mcp (Twitter/X)        │  ║
║  │  ApprovalWatcher ────────┼──────────┼──────────────────────────────►  │  ║
║  └──────────┬───────────────┘          └─────────────────────────────────┘  ║
║             │ action files                                                   ║
║             ▼                                                                ║
║  ┌──────────────────────────────────────────────────────────────────────┐   ║
║  │   VAULT FILESYSTEM  (Obsidian-compatible Markdown + JSON)            │   ║
║  │                                                                      │   ║
║  │   Inbox/ → Needs_Action/ → Pending_Approval/ → Approved/│Rejected/  │   ║
║  │                                      └────────────────► Done/        │   ║
║  └──────────┬───────────────────────────────────────────────────────────┘   ║
║             │                                                                ║
║             ▼                                                                ║
║  ┌──────────────────────────────────────────────────────────────────────┐   ║
║  │   REASONING LAYER                                                    │   ║
║  │   Claude Code CLI  +  19 Skills  +  Reasoning Loop                   │   ║
║  │                                                                      │   ║
║  │   PERCEIVE → CONSULT → REASON → PLAN → ACT → REPORT                 │   ║
║  └──────────────────────────────────────────────────────────────────────┘   ║
║                                                                              ║
║  ┌──────────────────────────────────────────────────────────────────────┐   ║
║  │   ORCHESTRATION LAYER                                                │   ║
║  │   scheduler.py · watchdog.py · run_watchers.py                       │   ║
║  │   audit_logger · retry_handler · error_handler                       │   ║
║  └──────────────────────────────────────────────────────────────────────┘   ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

### Design principles

| Principle | Implementation |
|-----------|----------------|
| **Human in the loop** | All external writes pass through `Pending_Approval/` → human review → `Approved/` |
| **Draft before live** | Odoo invoices/payments created in DRAFT; Twitter posts staged before publish |
| **Fail safe** | 4-category error model; transient errors retry, others alert and pause |
| **Observable** | Every action appended to `Logs/YYYY-MM-DD.json`; `Dashboard.md` reflects live state |
| **Dry-run everywhere** | `DRY_RUN=true` in `.env` suppresses all external API calls across every layer |
| **Composable** | Skills are SKILL.md prompts; any session can load exactly the context it needs |

---

## 2. Component Descriptions

### 2.1 Perception Layer — Watchers

**Location:** `Watchers/`  
**Language:** Python  
**Runner:** `Watchers/run_watchers.py` (daemon threads)

All watchers share a common abstract base class and write standardised
Markdown action files into `Needs_Action/` (or directly to `Pending_Approval/`
for pre-planned content like LinkedIn posts).

```
Watchers/
├── base_watcher.py          ← Abstract base: polling loop, create_action_file()
├── run_watchers.py          ← Starts all watchers as daemon threads; health monitor
├── filesystem_watcher.py    ← Event-driven (watchdog library), monitors Inbox/
├── gmail_watcher.py         ← Polls Gmail every 2 min; writes GMAIL_*.md
├── gmail_auth.py            ← OAuth2 flow helper; produces token.json
├── approval_watcher.py      ← Event-driven; dispatches Approved/ → API calls
├── linkedin_poster.py       ← Generates LinkedIn drafts via Claude API
├── twitter_watcher.py       ← Polls Twitter/X API v2 every 5 min
├── twitter_auth.py          ← OAuth2 PKCE flow; produces .twitter_token.json
├── twitter_dispatcher.py    ← Dispatches tweets/replies from Approved/
├── social_media_watcher.py  ← Polls Meta Graph API every 5 min
└── social_dispatcher.py     ← Dispatches Facebook/Instagram posts from Approved/
```

#### Watcher reference table

| Watcher | Trigger | Output | Poll interval | External API |
|---------|---------|--------|---------------|--------------|
| `FilesystemWatcher` | File created/moved in `Inbox/` | `Needs_Action/FILE_*.md` | Event-driven | None |
| `GmailWatcher` | Unread+important email ≤ 1 day old | `Needs_Action/GMAIL_*.md` | 2 min | Gmail API v1 |
| `TwitterWatcher` | Mentions or DMs | `Needs_Action/TWITTER_*.md` | 5 min | Twitter API v2 |
| `SocialMediaWatcher` | FB messages, IG DMs, comments, mentions | `Needs_Action/FACEBOOK_*.md` | 5 min | Meta Graph API v19 |
| `ApprovalWatcher` | File created in `Approved/` or `Rejected/` | Dispatches action → `Done/` | Event-driven | LinkedIn · Gmail · Twitter · Meta · Odoo |
| `LinkedInPoster` | Scheduled (Mon/Wed/Fri 09:00) | `Pending_Approval/LINKEDIN_*.md` | Via scheduler | LinkedIn API + Anthropic API |

#### BaseWatcher contract

```python
class BaseWatcher:
    def run(self) -> None          # main loop; calls check_for_updates()
    def check_for_updates(self)    # abstract; implemented by each watcher
    def create_action_file(        # writes structured Markdown to Needs_Action/
        self, title, content, action_type, priority, metadata
    ) -> Path
```

The polling interval is configurable per-watcher and defaults to 60 seconds.
Each watcher tracks processed IDs in a sidecar JSON file (e.g.,
`.gmail_seen_ids.json`) to prevent duplicate action files.

---

### 2.2 Reasoning Layer — Claude Code & Skills

**Language:** Claude prompts (SKILL.md) + Claude Code CLI subprocess  
**Entry points:** `claude --print -p "<prompt>"` or `/skill` invocations

Claude Code is invoked by the orchestrator (or directly via `run_watchers`)
when new items appear in `Needs_Action/`.  The reasoning loop is the core
cognitive engine:

```
PERCEIVE  → Read all files in Needs_Action/ and classify by type/urgency
CONSULT   → Load relevant SKILL.md files for the item types found
REASON    → Apply business rules; decide: auto-act / draft / escalate / skip
PLAN      → Write PLAN_*.md to Plans/ for multi-step or high-stakes items
ACT       → Execute low-risk actions autonomously; queue others for approval
REPORT    → Append to Logs/YYYY-MM-DD.json; update Dashboard.md
```

#### Skills reference (19 total)

| Skill | Trigger | What Claude does |
|-------|---------|-----------------|
| `process_needs_action` | Items in `Needs_Action/` | Triage, route, create plans |
| `reasoning_loop` | High-stakes or mixed queue | Full PERCEIVE→REPORT cycle |
| `approval_system_skill` | Before any external write | Writes to `Pending_Approval/`; explains what needs human review |
| `email_mcp_skill` | Email task | Uses `email-mcp` tools to draft/send |
| `linkedin_poster_skill` | LinkedIn task | Generates post copy; stages in `Pending_Approval/` |
| `twitter_skill` | Twitter task | Composes tweet/reply; routes to approval |
| `social_media_skill` | Facebook/Instagram task | Composes post or reply; routes to approval |
| `odoo_mcp_skill` | Finance/accounting task | Creates DRAFT invoice or payment; confirms only on explicit approval |
| `ceo_briefing_skill` | Sunday 20:00 | Assembles weekly briefing from vault data |
| `scheduling_skill` | Schedule queries | Reads/updates `Config/schedule.md` |
| `audit_logging_skill` | Post-action | Records structured entry in daily JSON log |
| `error_recovery_skill` | Exception caught | Classifies error; retries or escalates |
| `ralph_wiggum_skill` | Long-running task | Persistent retry loop (up to 10 iterations) |
| `reasoning_loop` | Complex triage | Deep structured reasoning with explicit plan |
| `update_dashboard` | After any state change | Rewrites `Dashboard.md` sections |
| `complete_task` | Task finished | Archives to `Done/`; appends to log |
| `filesystem_watcher_skill` | FilesystemWatcher reference | Documents Inbox/ event protocol |
| `gmail_watcher_skill` | GmailWatcher reference | Documents GMAIL_*.md schema |
| `base_watcher_skill` | Watcher development | Documents BaseWatcher contract |
| `odoo_setup_skill` | Odoo deployment | Docker Compose setup procedure |

---

### 2.3 Action Layer — MCP Servers

**Location:** `MCP_Servers/`  
**Language:** Node.js (Model Context Protocol stdio transport)  
**Config:** `~/.claude/mcp.json`

MCP servers expose typed tools that Claude Code calls directly.  They never
initiate actions themselves — they only respond to Claude's tool calls.

```
MCP_Servers/
├── email-mcp/       ← Gmail operations (Node.js + googleapis)
├── odoo-mcp/        ← Odoo 19 ERP operations (Node.js + XML-RPC)
├── social-mcp/      ← Facebook & Instagram (Node.js + Meta Graph API)
└── twitter-mcp/     ← Twitter/X (Node.js + Twitter API v2)
```

#### Tool inventory

**email-mcp** — `credentials.json` + `token.json` (shared with GmailWatcher)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `send_email` | to, subject, body, cc? | Send via Gmail API |
| `draft_email` | to, subject, body | Save as Gmail draft |
| `search_emails` | query, maxResults? | Search mailbox |

**odoo-mcp** — `ODOO_URL`, `ODOO_DB`, `ODOO_USER`, `ODOO_PASSWORD`

| Tool | Parameters | Description |
|------|-----------|-------------|
| `create_invoice` | partner_id, lines[], date? | Create DRAFT invoice |
| `list_invoices` | state?, partner_id?, limit? | List invoices |
| `get_invoice` | invoice_id | Fetch invoice detail |
| `create_payment` | partner_id, amount, journal? | Create DRAFT payment |
| `get_account_balance` | account_id | Current balance |
| `list_transactions` | date_from?, date_to? | List journal entries |
| `create_partner` | name, email?, phone? | Create customer/vendor |

**social-mcp** — `META_PAGE_ACCESS_TOKEN`, `META_PAGE_ID`, `META_IG_USER_ID`

| Tool | Parameters | Description |
|------|-----------|-------------|
| `post_to_facebook` | message, link?, image_url? | Publish Facebook post |
| `post_to_instagram` | image_url, caption | Publish Instagram post |
| `reply_to_comment` | comment_id, message | Reply to comment |
| `get_page_insights` | metric, period? | Fetch Page analytics |
| `get_messages` | limit? | Fetch inbox messages |

**twitter-mcp** — `TWITTER_BEARER_TOKEN`, `TWITTER_CLIENT_ID/SECRET`, `TWITTER_USER_ID`

| Tool | Parameters | Description |
|------|-----------|-------------|
| `post_tweet` | text, reply_to_id? | Post (goes to Pending_Approval first) |
| `reply_to_tweet` | tweet_id, text | Reply to tweet |
| `get_mentions` | max_results? | Fetch recent mentions |
| `get_timeline` | max_results? | Fetch home timeline |
| `get_analytics` | tweet_id | Fetch tweet metrics |

---

### 2.4 Orchestration Layer

The orchestration layer keeps all moving parts alive, scheduled, and
observable.

```
scripts/
├── watchdog.py        ← Process supervisor (auto-restart + health file)
├── error_handler.py   ← Error handling facade (categorise → retry/alert/quarantine)
├── retry_handler.py   ← Exponential backoff engine
├── audit_logger.py    ← Structured JSON audit trail
├── generate_briefing.py  ← Data collector for CEO briefing
└── ralph_loop.py      ← Persistent Claude loop (STOP_RALPH kill switch)

scheduler.py           ← Reads Config/schedule.md; runs tasks on cron
Config/schedule.md     ← Task definitions (YAML frontmatter)
```

#### `watchdog.py`

Runs as a **separate process** from the watchers.  Manages two supervised
processes: `WatcherRunner` (`Watchers/run_watchers.py`) and `Scheduler`
(`scheduler.py`).

```
watchdog
   ├── monitor_thread(WatcherRunner)
   │     poll every 5s → crashed? → log → wait 5s → restart
   │     3 consecutive crashes → write HUMAN_ALERT to Logs/alerts.json
   ├── monitor_thread(Scheduler)
   │     same logic
   └── health_writer_thread
         every 60s → write Logs/health_status.json
```

#### `scheduler.py`

Reads `Config/schedule.md` YAML frontmatter, registers jobs with the
`schedule` library, and runs each in a daemon thread with a per-task
semaphore (prevents overlapping runs).

```
Config/schedule.md jobs:

  daily_dashboard_update   — daily 08:00 UTC
    claude --print -p "Run update_dashboard skill"

  linkedin_post_generation — Mon/Wed/Fri 09:00 UTC
    python -m Watchers.linkedin_poster generate

  ceo_briefing             — Sunday 20:00 UTC
    python scripts.generate_briefing
```

#### `audit_logger.py`

Every action in any layer should call `log_action()` or use the `audit()`
context manager.  Entries are appended to `Logs/YYYY-MM-DD.json` with
cross-process file locking (`fcntl.flock`).

```python
# Fire-and-forget
log_action(action_type="gmail_check", actor="watcher", target="inbox",
           result="success", duration_ms=142)

# Auto-timed block
with audit("email_send", actor="mcp_server", target="alice@example.com") as a:
    send_email(payload)
    a.set_result("success")
```

---

## 3. Data Flow Diagrams

### 3.1 Email arrives → processed → reply sent

```
  Gmail inbox
      │
      │  poll every 2 min
      ▼
  GmailWatcher
  (Watchers/gmail_watcher.py)
      │
      │  write GMAIL_<id>.md to Needs_Action/
      │  fields: from, subject, snippet, thread_id, labels
      ▼
  run_watchers.py health monitor
  detects new file (or scheduler triggers orchestrator)
      │
      ▼
  Claude Code CLI
  loads: process_needs_action/SKILL.md
         email_mcp_skill/SKILL.md
      │
      │  REASON: is this auto-respondable?
      │    yes → draft reply
      │    no  → write to Review_Queue/; notify human
      │
      ├──[auto-reply path]──────────────────────────────┐
      │                                                  │
      │  calls email-mcp tool: draft_email()             │
      │  writes Pending_Approval/EMAIL_<id>.md           │
      │  action: email_send, subject, body, thread_id    │
      │                                                  │
      ▼                                                  │
  Human reviews Pending_Approval/EMAIL_<id>.md           │
  moves to Approved/  or  Rejected/                      │
      │                                                  │
      ▼                                                  │
  ApprovalWatcher detects file in Approved/              │
  (Watchers/approval_watcher.py)                         │
      │                                                  │
      │  parses frontmatter: action = email_send         │
      │  calls email-mcp tool: send_email()              │
      │  moves file to Done/                             │
      │  updates Dashboard.md                            │
      │                                                  │
      ▼                                                  │
  audit_logger.log_action(                              ◄┘
    action_type="email_send",
    actor="mcp_server",
    result="success" | "failure",
    duration_ms=...
  )
  → appended to Logs/YYYY-MM-DD.json
```

---

### 3.2 Invoice request → generated → approved → sent

```
  Inbox/invoice_request_acme.txt
      │
      │  FilesystemWatcher detects file creation
      ▼
  Needs_Action/FILE_invoice_request_acme_<ts>.md
      │
      ▼
  Claude Code CLI
  loads: reasoning_loop/SKILL.md
         odoo_mcp_skill/SKILL.md
         approval_system_skill/SKILL.md
      │
      │  PERCEIVE:  identifies invoice request
      │  CONSULT:   odoo_mcp_skill — draft-first rule
      │  REASON:    extract partner, line items, amounts
      │  PLAN:      create PLAN_*.md if ambiguous data
      │  ACT:       call odoo-mcp: create_invoice(partner_id, lines)
      │             Odoo creates record in DRAFT state
      │
      ▼
  Pending_Approval/INVOICE_acme_<ts>.md
  frontmatter:
    action:      odoo_confirm_invoice
    invoice_id:  <odoo record ID>
    partner:     Acme Corp
    total:       $4,200.00
    status:      DRAFT
      │
      │  Human reviews invoice in Odoo (or via approval file)
      │  moves to Approved/
      ▼
  ApprovalWatcher
      │  parses action: odoo_confirm_invoice
      │  calls odoo-mcp: confirm invoice (state → posted)
      │  calls email-mcp: send_email() with PDF attachment
      │  moves to Done/
      ▼
  Logs/YYYY-MM-DD.json
  {action_type: "odoo_invoice_confirmed", result: "success", ...}
```

---

### 3.3 Weekly audit → CEO briefing generated

```
  scheduler.py
  Sunday 20:00 UTC — ceo_briefing job fires
      │
      ▼
  scripts/generate_briefing.py
      │
      │  Collects data packet from vault:
      │    ├── Logs/YYYY-MM-DD.json  (last 7 days of audit entries)
      │    ├── Plans/*.md            (active plans + status)
      │    ├── Done/*.md             (completed tasks this week)
      │    ├── Pending_Approval/*.md (items still awaiting review)
      │    ├── Config/schedule.md    (upcoming scheduled tasks)
      │    └── Dashboard.md         (current system state)
      │
      ▼
  Claude Code CLI
  loads: ceo_briefing_skill/SKILL.md
  prompt: full data packet + output template
      │
      │  REASON: summarise week, flag risks, list priorities
      │  ACT: write Briefings/BRIEFING_<date>.md
      │       update Dashboard.md "Latest Briefing" section
      │
      ▼
  Briefings/BRIEFING_2026-04-13.md
  (Monday-dated briefing ready before 08:00 Monday)
      │
      ▼
  audit_logger.log_action(
    action_type="ceo_briefing",
    actor="claude_code",
    result="success",
    output_file="BRIEFING_2026-04-13.md"
  )
  → Logs/2026-04-13.json
```

---

## 4. Folder Structure

```
AI_Employee_Vault/
│
│  ── WORK QUEUES (runtime state) ──────────────────────────────────────────
│
├── Inbox/                      # Drop zone: any file here triggers FilesystemWatcher
├── Needs_Action/               # Unprocessed action files waiting for Claude
│     ├── FILE_*.md             #   from FilesystemWatcher
│     ├── GMAIL_*.md            #   from GmailWatcher
│     ├── TWITTER_*.md          #   from TwitterWatcher
│     └── FACEBOOK_*.md         #   from SocialMediaWatcher
├── Pending_Approval/           # Staged actions awaiting human review
│     ├── EMAIL_*.md            #   draft email
│     ├── LINKEDIN_*.md         #   LinkedIn post draft
│     ├── TWITTER_*.md          #   tweet draft
│     ├── FACEBOOK_*.md         #   Facebook/Instagram post draft
│     └── INVOICE_*.md          #   Odoo invoice (DRAFT state)
├── Approved/                   # Human-approved: ApprovalWatcher dispatches these
├── Rejected/                   # Human-rejected: logged and archived
├── Done/                       # Completed: all finished action files
│     └── TASK_*.md             #   from ralph_loop.py completions
├── Plans/                      # Multi-step reasoning plans (from reasoning_loop)
│     └── PLAN_*.md             #   Objective, reasoning, steps, status
├── Active_Projects/            # Ongoing project context files
├── Review_Queue/               # Items needing human investigation (logic errors)
│     └── *.json                #   from error_handler.py LOGIC errors
├── Quarantine/                 # Malformed or suspicious data
│     ├── *.md / *.json         #   original quarantined file
│     └── *.reason              #   sidecar: why it was quarantined
│
│  ── OUTPUTS ────────────────────────────────────────────────────────────────
│
├── Briefings/                  # CEO briefings (weekly, Monday-dated)
│     └── BRIEFING_YYYY-MM-DD.md
├── Dashboard.md                # Live system state (auto-refreshed daily)
│
│  ── CODE ───────────────────────────────────────────────────────────────────
│
├── Watchers/                   # Python perception layer
├── MCP_Servers/                # Node.js action layer
│     ├── email-mcp/
│     ├── odoo-mcp/
│     ├── social-mcp/
│     └── twitter-mcp/
├── Skills/                     # Claude skill definitions (SKILL.md prompts)
├── scripts/                    # Orchestration utilities
│     ├── audit_logger.py       #   structured JSON logging
│     ├── error_handler.py      #   error handling facade
│     ├── retry_handler.py      #   exponential backoff
│     ├── watchdog.py           #   process supervisor
│     ├── generate_briefing.py  #   CEO briefing data collector
│     └── ralph_loop.py         #   persistent task loop
├── scheduler.py                # Task scheduler (reads Config/schedule.md)
│
│  ── CONFIGURATION ──────────────────────────────────────────────────────────
│
├── Config/
│     └── schedule.md           # Cron-style task definitions (YAML frontmatter)
├── Odoo/                       # Odoo 19 Docker Compose setup
│
│  ── CREDENTIALS (git-ignored) ──────────────────────────────────────────────
│
├── .env                        # All secrets (API keys, passwords)
├── credentials.json            # Google OAuth client secret
├── token.json                  # Gmail OAuth access/refresh token
└── .twitter_token.json         # Twitter OAuth2 PKCE tokens
│
│  ── LOGS ────────────────────────────────────────────────────────────────────
│
└── Logs/
      ├── YYYY-MM-DD.json       # Daily audit log (JSON array, one entry/action)
      ├── watcher_YYYY-MM-DD.log # Watcher stdout/stderr (plain text)
      ├── watchdog_YYYY-MM-DD.log
      ├── health_status.json    # Live process states (updated every 60s)
      ├── alerts.json           # All escalation alerts
      ├── restart_log.json      # Watchdog restart history
      ├── error_counts.json     # Per-operation error tallies
      ├── email_queue/          # Emails queued while Gmail is down
      ├── odoo_queue/           # Odoo transactions queued while Odoo is down
      └── Archive/              # Rotated daily JSON logs (>90 days old)
```

#### Action file schema (frontmatter)

Every file in the work queues uses YAML frontmatter so Claude and the
ApprovalWatcher can parse it without reading the full body:

```yaml
---
action:          email_send           # dispatch handler key
priority:        HIGH                 # HIGH | MEDIUM | LOW
created_at:      2026-04-10T13:00:00Z
source:          gmail_watcher
target:          alice@example.com
approval_status: pending              # auto | pending | approved | rejected
# handler-specific fields follow:
thread_id:       abc123
subject:         "Re: Project proposal"
---
Full email body here...
```

---

## 5. Security Architecture

### 5.1 Credential management

All secrets are stored in `.env` at the vault root and loaded at process
startup via `python-dotenv`.  The file is **git-ignored** and must never be
committed.

| Secret | Where stored | Used by |
|--------|-------------|---------|
| `ANTHROPIC_API_KEY` | `.env` | LinkedInPoster, ralph_loop, orchestrator |
| `GMAIL_CLIENT_ID/SECRET` | `credentials.json` (git-ignored) | GmailWatcher, email-mcp |
| `GMAIL_ACCESS/REFRESH_TOKEN` | `token.json` (git-ignored) | GmailWatcher, email-mcp |
| `LINKEDIN_ACCESS_TOKEN` | `.env` | linkedin_poster.py |
| `TWITTER_BEARER_TOKEN` | `.env` | TwitterWatcher, twitter-mcp |
| `TWITTER_CLIENT_ID/SECRET` | `.env` | twitter_auth.py, twitter-mcp |
| `TWITTER_ACCESS/REFRESH_TOKEN` | `.twitter_token.json` (git-ignored) | TwitterWatcher, twitter-mcp |
| `META_PAGE_ACCESS_TOKEN` | `.env` | SocialMediaWatcher, social-mcp |
| `ODOO_USER` / `ODOO_PASSWORD` | `.env` | odoo-mcp |

### 5.2 Human approval gate

The approval gate is the primary security control for all external writes.
**No outbound action bypasses it without an explicit autonomous-mode flag.**

```
Claude drafts action → Pending_Approval/
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
         Human moves    Human moves    Item expires
         to Approved/   to Rejected/   (stays pending)
              │               │
              ▼               ▼
      ApprovalWatcher    logged to
      dispatches API     Done/ with
      call               rejection note
```

Autonomous actions (auto-approved) are limited to:
- Reading/searching (Gmail search, Twitter mentions fetch, Odoo list)
- Writing to vault-internal files (Plans, Dashboard, Logs)
- Sending brief automated acknowledgements explicitly configured as auto

### 5.3 Odoo draft-first rule

All financial operations go through a mandatory two-step process:

1. Claude calls `create_invoice()` or `create_payment()` → Odoo record in **DRAFT** state
2. Only after human approval does the ApprovalWatcher call the confirm endpoint

This means no money moves and no invoice becomes official without a human
explicitly moving a file to `Approved/`.

### 5.4 DRY_RUN mode

Setting `DRY_RUN=true` in `.env` suppresses all external API calls across
every layer simultaneously:

- All four MCP servers log tool calls but do not send HTTP requests
- All watchers log detected events but do not create action files
- `scheduler.py` logs scheduled invocations but does not run them

This makes the entire system safe to operate in staging or on a shared
machine without risk of unintended external effects.

### 5.5 MCP server isolation

Each MCP server runs as a separate Node.js child process communicating over
stdio (Model Context Protocol).  They:

- Have no inbound network listener (stdio only; no open ports)
- Load credentials from environment variables passed at launch, not hardcoded
- Cannot read the vault filesystem directly — only Claude can

### 5.6 Quarantine and review queue

Untrusted data (malformed files, unexpected API payloads) never reaches the
reasoning layer.  The error handler quarantines it immediately:

```
Malformed input detected
        │
        ▼
scripts/error_handler.py
  category = ErrorCategory.DATA
        │
        ├── move file to Quarantine/<timestamp>_<name>
        ├── write Quarantine/<name>.reason (why + original path)
        ├── append to Logs/alerts.json
        └── raise DataError (caller does not process further)
```

---

## 6. Error Handling Strategy

The vault uses a **four-category error model** implemented in
`scripts/retry_handler.py` and exposed through `scripts/error_handler.py`.

```
Exception raised
       │
       ▼
  error_handler.py
  classifies by ErrorCategory
       │
  ┌────┴──────────────────────────────────────────────┐
  │                                                   │
  ▼                                                   ▼
TRANSIENT                                           AUTH
(network, timeout, 429)                    (401, 403, token expired)
  │                                                   │
  │  retry up to 3× with backoff:                     │  write to Logs/alerts.json
  │    attempt 1 → wait 2s                            │  pause that integration
  │    attempt 2 → wait 4s                            │  raise AuthError
  │    attempt 3 → wait 8s                            │  human must rotate creds
  │  if all fail → write alert →                      │
  │  raise RetryExhausted                             │
  │                                                   │
  ▼                                                   ▼
LOGIC                                              DATA
(unexpected code error, bad state)          (malformed / corrupt input)
  │                                                   │
  │  write Review_Queue/<ts>_<op>.json                │  move file to Quarantine/
  │  write Logs/alerts.json                           │  write .reason sidecar
  │  raise LogicError                                 │  write Logs/alerts.json
  │  human reads review queue                         │  raise DataError
```

### Graceful degradation matrix

| Service down | Immediate behaviour | Recovery path |
|---|---|---|
| Gmail API | Queue email body to `Logs/email_queue/` | Iterate queue once API recovers |
| Odoo | Log transaction as Markdown to `Logs/odoo_queue/` | Sync when Odoo available |
| Claude Code | Watchers continue; `Needs_Action/` queue grows | Run reasoning loop on recovery |
| Obsidian vault locked | Write to `/tmp/ai_employee_vault_buffer/` | Copy back when vault unlocks |
| Twitter/Meta API | Alert written; watcher skips cycle | Retried next poll cycle |

### Watchdog restart policy

```
Process crash detected
        │
        ▼
  log to Logs/restart_log.json
        │
        ▼
  wait 5 seconds (cooldown)
        │
        ▼
  restart process
        │
  clean start? ──yes──→ reset crash_count = 0
        │
        no (still crashing)
        │
        ▼
  crash_count >= 3?
        │
        yes → write HUMAN_ALERT to Logs/alerts.json
              log CRITICAL message
              continue restarting (does not give up)
```

---

## 7. Scaling Considerations

### 7.1 Current constraints

| Constraint | Current limit | Cause |
|---|---|---|
| Watcher concurrency | 1 process, N daemon threads | `run_watchers.py` is single-process |
| Reasoning throughput | 1 Claude invocation at a time | Sequential subprocess calls |
| Log I/O | 1 file per day, fcntl-locked | Read-modify-write on every append |
| MCP server count | 4 (email, odoo, social, twitter) | Manually registered in `mcp.json` |
| Scheduler precision | ~1 second | Python `schedule` library polling |

### 7.2 Scaling the perception layer

**More watchers without code changes:**  
Each new watcher only needs to subclass `BaseWatcher`, implement
`check_for_updates()`, and call `create_action_file()`.  Register it in
`run_watchers.py` and it runs as a daemon thread.

**High-volume polling:**  
For services with webhook support (e.g., Meta Graph webhooks), replace the
polling loop with an HTTP listener.  The output format (`Needs_Action/` action
files) does not change — the reasoning layer is unaffected.

**Multiple vault instances:**  
The filesystem queue model is network-friendly.  Point multiple watcher
processes at the same NFS/SMB-mounted vault root and the `fcntl.flock` in
`audit_logger.py` provides cross-host serialisation on Linux.

### 7.3 Scaling the reasoning layer

**Parallelism:**  
The current orchestrator processes `Needs_Action/` items sequentially.
To process multiple items in parallel, spawn one `ralph_loop.py` subprocess
per item.  Each subprocess writes to its own Plan file and separate audit log
entries; `fcntl.flock` keeps the daily JSON log consistent.

**Context window management:**  
Each Claude invocation receives only the SKILL.md files relevant to the items
being processed.  The vault never passes the entire file tree to a single
prompt.  As the Skills directory grows, this selective loading pattern keeps
context lean.

**Cost controls:**  
- `DRY_RUN=true` for development/staging
- `--max-iterations N` on `ralph_loop.py` (default 10) caps runaway loops
- `STOP_RALPH` touch file is an emergency halt for the persistent loop

### 7.4 Scaling the action layer

**Adding a new MCP server:**  
1. Create `MCP_Servers/<name>-mcp/index.js` using `@modelcontextprotocol/sdk`
2. Add entry to `~/.claude/mcp.json`
3. Write `Skills/<name>_skill/SKILL.md` describing the tools
4. Add `from scripts.error_handler import handle_error` to wrap API calls

**Rate limiting:**  
Each MCP server should wrap its API calls with `scripts/retry_handler.py`
`call_with_retry(..., category=ErrorCategory.TRANSIENT)` to get automatic
exponential backoff.  The current `email-mcp` and `odoo-mcp` handle this
internally; `social-mcp` and `twitter-mcp` should adopt the same pattern.

### 7.5 Operational runbook (start order)

```
1. Start Odoo (if needed):
     cd Odoo && docker compose up -d

2. Start the watchdog (supervises everything else):
     python scripts/watchdog.py

   The watchdog automatically starts:
     → Watchers/run_watchers.py   (all watchers)
     → scheduler.py               (scheduled tasks)

3. Verify health:
     cat Logs/health_status.json
     cat Logs/alerts.json

4. Stop everything:
     Ctrl+C on watchdog → it terminates supervised processes gracefully
```

For development without external API calls:

```bash
DRY_RUN=true python Watchers/run_watchers.py --no-gmail --no-twitter --no-social
```
