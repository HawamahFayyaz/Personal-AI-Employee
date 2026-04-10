# Skill: email-mcp

## What it does

`email-mcp` is a Node.js MCP server that gives Claude three Gmail-powered tools:

| Tool | What it does |
|------|-------------|
| `send_email` | Composes and sends an email via Gmail API |
| `draft_email` | Saves a draft locally (`Drafts/`) and in Gmail — never sends |
| `search_emails` | Queries Gmail with standard search syntax and returns metadata |

All tools respect `DRY_RUN` mode — when enabled, they log what they _would_ do
without making any Gmail API calls.

---

## File locations

```
MCP_Servers/email-mcp/
  index.js          ← MCP server (stdio transport)
  package.json      ← Node.js dependencies
  README.md         ← quick-start reference

Drafts/             ← local draft files written by draft_email (auto-created)
credentials.json    ← Google OAuth client secrets (vault root)
token.json          ← OAuth token, auto-created by gmail_auth.py (vault root)
```

---

## Prerequisites

### 1. Generate `token.json`

`token.json` is shared with the Python Gmail watcher. If it already exists in
the vault root you can skip this step. Otherwise run the Python auth helper
once:

```bash
cd /mnt/d/HACKATHON_00/AI_Employee_Vault
python -c "from Watchers.gmail_auth import get_gmail_service; get_gmail_service()"
```

Follow the browser/console OAuth prompt. `token.json` is written automatically.

The token requires these Gmail scopes:
- `https://www.googleapis.com/auth/gmail.send`
- `https://www.googleapis.com/auth/gmail.readonly`

### 2. Install Node.js dependencies

```bash
cd MCP_Servers/email-mcp
npm install
```

---

## Adding to Claude Code

Add the server entry to `~/.claude/mcp.json` (create the file if it doesn't exist):

```json
{
  "mcpServers": {
    "email": {
      "command": "node",
      "args": ["/mnt/d/HACKATHON_00/AI_Employee_Vault/MCP_Servers/email-mcp/index.js"],
      "env": {
        "GMAIL_CREDENTIALS": "/mnt/d/HACKATHON_00/AI_Employee_Vault/credentials.json",
        "GMAIL_TOKEN": "/mnt/d/HACKATHON_00/AI_Employee_Vault/token.json",
        "DRY_RUN": "true"
      }
    }
  }
}
```

Restart Claude Code after editing `mcp.json`. Verify with `/mcp` — you should
see `email` listed with three tools.

> Set `DRY_RUN` to `"false"` when you're ready to send real emails.

---

## Tool reference

### `send_email`

```
send_email(to, subject, body, cc?, bcc?)
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `to` | string | yes | Recipient address(es), comma-separated |
| `subject` | string | yes | Subject line |
| `body` | string | yes | Plain-text body |
| `cc` | string | no | CC addresses, comma-separated |
| `bcc` | string | no | BCC addresses, comma-separated |

**Returns:** `{ success, message_id, thread_id, message }` on success.

**Example prompt:** _"Send an email to alice@example.com about the invoice status."_

---

### `draft_email`

```
draft_email(to, subject, body)
```

Saves the draft in two places:
1. `Drafts/DRAFT_<timestamp>_<subject>.md` — local markdown file in the vault
2. Gmail Drafts folder (unless `DRY_RUN=true`)

**Returns:** `{ success, draft_id, local_draft, message }`

**Example prompt:** _"Draft a follow-up email to bob@example.com about the project proposal — don't send it yet."_

---

### `search_emails`

```
search_emails(query, max_results?)
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | yes | Gmail search query (same syntax as Gmail search bar) |
| `max_results` | number | no | Max results to return (default 10, max 50) |

**Returns:** `{ success, count, results[] }` where each result has:
`id, thread_id, from, to, subject, date, snippet`

**Gmail query examples:**

```
from:alice@example.com
subject:invoice is:unread
after:2026/01/01 label:important
has:attachment filename:pdf
```

**Example prompt:** _"Search my email for any unread messages from clients about invoices."_

---

## DRY_RUN mode

When `DRY_RUN=true`:
- `send_email` — logs the recipient, subject, and body to stderr; returns a
  dry-run confirmation message
- `draft_email` — still writes the local `Drafts/*.md` file, but skips the
  Gmail API draft creation
- `search_emails` — logs the query and returns an empty results array

Switch to live mode by setting `DRY_RUN=false` in the MCP config env block.

---

## Token refresh

The server listens for OAuth token refresh events from `google-auth-library`
and automatically writes updated tokens back to `token.json`, keeping the
shared token file in sync with the Python watcher.

---

## Error handling

| Scenario | Behaviour |
|----------|-----------|
| `credentials.json` missing | Server throws with clear path hint |
| `token.json` missing | Server throws — run Python auth flow first |
| Gmail API auth error | Error returned in tool result `isError: true` |
| Gmail API rate limit / 5xx | Error surfaced; no retry (caller should retry) |
| `npm` packages missing | `import` fails — run `npm install` first |

---

## Environment variable reference

| Variable | Default | Description |
|----------|---------|-------------|
| `GMAIL_CREDENTIALS` | `<vault>/credentials.json` | Google OAuth client secrets path |
| `GMAIL_TOKEN` | `<vault>/token.json` | Stored OAuth token path |
| `DRY_RUN` | `false` | Skip real API calls when `true` |
