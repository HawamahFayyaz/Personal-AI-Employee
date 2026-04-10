# email-mcp

MCP server that exposes Gmail send, draft, and search as Claude tools.

## Tools

| Tool | Description |
|------|-------------|
| `send_email(to, subject, body, cc?, bcc?)` | Send an email immediately via Gmail API |
| `draft_email(to, subject, body)` | Save a draft locally + in Gmail without sending |
| `search_emails(query, max_results?)` | Search Gmail with standard query syntax |

## Quick start

```bash
cd MCP_Servers/email-mcp
npm install
node index.js          # stdio MCP server, reads token.json from vault root
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GMAIL_CREDENTIALS` | `<vault>/credentials.json` | Path to Google OAuth client secrets |
| `GMAIL_TOKEN` | `<vault>/token.json` | Path to stored OAuth token |
| `DRY_RUN` | `false` | Set `true` to log without calling Gmail API |

## Prerequisites

- `token.json` must exist in the vault root (run the Python `gmail_auth.py` flow first)
- Gmail scopes required: `gmail.send`, `gmail.readonly`

## Claude Code MCP config

Add to `~/.claude/mcp.json`:

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

Set `DRY_RUN` to `"false"` when ready to send real emails.
