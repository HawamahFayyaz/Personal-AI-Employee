# Skill: Twitter/X

## Purpose
Monitor Twitter/X for mentions and DMs, draft tweets and replies through the
approval workflow, and fetch analytics — all integrated into the AI Employee Vault
using Twitter API v2 with OAuth 2.0 PKCE.

## Trigger phrases
- "post a tweet"
- "draft a tweet"
- "check Twitter mentions"
- "reply to tweet"
- "get Twitter analytics"
- "what's my Twitter activity"
- "check my DMs on Twitter"

---

## Prerequisites — Twitter/X Developer Setup

### Step 1: Create a Twitter Developer App

1. Go to <https://developer.twitter.com/en/portal/projects-and-apps>
2. Create a new **Project** → create an **App** inside it
3. Choose **Free** tier (read/write access) or **Basic** (higher rate limits)
4. Under **User authentication settings**, enable:
   - **OAuth 2.0** with **PKCE**
   - App type: **Web App, Automated App or Bot** (for confidential client) or **Native App** (for public PKCE)
   - Callback URI: `http://localhost:8765/callback`
   - Website URL: your domain or `http://localhost`

### Step 2: Required Permissions (OAuth 2.0 Scopes)

| Scope | Required for |
|-------|-------------|
| `tweet.read` | Read mentions, timeline, tweet analytics |
| `tweet.write` | Post tweets and replies |
| `users.read` | Fetch user profiles and metrics |
| `dm.read` | Read Direct Messages |
| `offline.access` | Get a refresh token (token auto-renewal) |

### Step 3: Collect credentials

From the **Keys and Tokens** tab:
- **Client ID** — OAuth 2.0 Client ID
- **Client Secret** — OAuth 2.0 Client Secret (confidential apps only)

From your Twitter profile URL or the API:
- **User ID** — your numeric account ID

To find your numeric User ID:
```bash
# Replace YOUR_BEARER_TOKEN and YOUR_USERNAME
curl -s "https://api.twitter.com/2/users/by/username/YOUR_USERNAME" \
  -H "Authorization: Bearer YOUR_BEARER_TOKEN" | python -m json.tool
```

### Step 4: Configure `.env`

Add to `{vault_root}/.env`:

```dotenv
# Twitter / X
TWITTER_CLIENT_ID=your_oauth2_client_id
TWITTER_CLIENT_SECRET=your_oauth2_client_secret   # omit for public PKCE apps
TWITTER_BEARER_TOKEN=your_app_only_bearer_token    # read-only fallback
TWITTER_USER_ID=1234567890123456789                # your numeric user ID
TWITTER_USERNAME=yourhandle                         # without @, display only

# Watcher tuning
TWITTER_CHECK_INTERVAL=300       # seconds between polls (default: 300)
TWITTER_SUMMARY_HOUR=8           # UTC hour for daily summary (default: 8)
```

### Step 5: Run the OAuth 2.0 authorization flow

This creates `.twitter_token.json` with your access and refresh tokens:

```bash
cd /path/to/vault
python Watchers/twitter_auth.py
```

The script will:
1. Generate a PKCE code verifier and challenge
2. Open your browser to Twitter's authorization page
3. Listen on `localhost:8765` for the OAuth callback
4. Exchange the code for access and refresh tokens
5. Save tokens to `.twitter_token.json`

> **Token lifetimes:**
> - Access token: ~2 hours (auto-refreshed by watcher and MCP server)
> - Refresh token: valid until used once (new one issued on each refresh)
> - Re-run `twitter_auth.py` only if refresh token is lost or revoked

---

## Running the Watcher

Auto-started by `run_watchers.py` when `.twitter_token.json` exists and
`TWITTER_USER_ID` is set:

```bash
python Watchers/run_watchers.py
```

To skip it:
```bash
python Watchers/run_watchers.py --no-twitter
```

To run standalone:
```bash
python Watchers/twitter_watcher.py
```

---

## MCP Server Setup

```bash
cd MCP_Servers/twitter-mcp/
npm install
npm start          # production
npm run dev        # DRY_RUN=true — no files written, no write API calls
```

Register in Claude Code settings (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "twitter-mcp": {
      "command": "node",
      "args": ["/path/to/vault/MCP_Servers/twitter-mcp/index.js"],
      "env": {
        "TWITTER_BEARER_TOKEN": "your_bearer_token",
        "TWITTER_CLIENT_ID":    "your_client_id",
        "TWITTER_CLIENT_SECRET": "your_client_secret",
        "TWITTER_USER_ID":      "your_numeric_user_id",
        "VAULT_ROOT":           "/path/to/vault"
      }
    }
  }
}
```

---

## Available MCP Tools

### `post_tweet(text, media_ids?)`
Draft a tweet (max 280 characters). Creates `Pending_Approval/TWITTER_TWEET_*.md`.
Move to `Approved/` to publish.

```
post_tweet("Just shipped a new feature! 🚀")
post_tweet("Check this out", media_ids="1234567890,9876543210")
```

### `reply_to_tweet(tweet_id, text)`
Draft a reply. **Always requires approval.** Fetches the original tweet for context
in the approval file.

```
reply_to_tweet("1234567890123456789", "Thanks for the feedback! Here's how to fix it…")
```

### `get_mentions(since_id?, max_results?)`
Fetch recent mentions. No approval needed (read-only). Returns tweet text,
author, metrics, and tweet IDs.

```
get_mentions()                               # latest 20 mentions
get_mentions(since_id="1234567890", max_results=50)
```

Save the newest tweet ID from the response and pass it as `since_id` next time
to avoid processing duplicates.

### `get_timeline(count?)`
Fetch the authenticated account's reverse-chronological home timeline.

```
get_timeline()           # 20 tweets
get_timeline(count=50)
```

### `get_analytics(tweet_id)`
Fetch engagement metrics for any tweet. Returns public metrics (likes, retweets,
replies, quotes, bookmarks, impressions). For own tweets with user context, also
returns organic metrics.

```
get_analytics("1234567890123456789")
```

---

## Approval Workflow

```
MCP tool called (post_tweet / reply_to_tweet)
    │
    ▼
Pending_Approval/TWITTER_*.md  ← YAML frontmatter + draft content
    │
    ├─→ Move to Approved/   → ApprovalWatcher dispatches via twitter_dispatcher.py
    │                            tweet        → POST /2/tweets  {text}
    │                            tweet_reply  → POST /2/tweets  {text, reply.in_reply_to_tweet_id}
    │
    └─→ Move to Rejected/   → Logged to Logs/approval_watcher.log, archived to Done/
```

### Frontmatter reference for approval files

| Field | Used by | Purpose |
|-------|---------|---------|
| `action: tweet` | `dispatch_tweet` | Post a new tweet |
| `action: tweet_reply` | `dispatch_tweet_reply` | Reply to a tweet |
| `text` | both | Tweet/reply text (≤ 280 chars) |
| `in_reply_to_tweet_id` | `dispatch_tweet_reply` | Tweet ID to reply to |
| `media_ids` | `dispatch_tweet` | Comma-separated media IDs |

---

## What the Watcher Detects

| Event | Action file prefix | Priority |
|-------|--------------------|----------|
| Mention (@handle) | `TWMENTION_` | High |
| Direct Message | `TWDM_` | High |
| Daily Summary | `TWITTER_SUMMARY_` | Low |

The watcher uses `since_id` polling — it only fetches tweets newer than the last
processed ID. State is persisted in `.twitter_state.json`.

---

## Files

| Path | Role |
|------|------|
| `Watchers/twitter_auth.py` | One-shot OAuth 2.0 PKCE authorization flow |
| `Watchers/twitter_watcher.py` | Polling watcher (mentions, DMs, daily summary) |
| `Watchers/twitter_dispatcher.py` | Approval-watcher dispatch handlers |
| `MCP_Servers/twitter-mcp/index.js` | MCP server (5 tools) |
| `MCP_Servers/twitter-mcp/package.json` | Node.js dependencies |
| `{vault}/.twitter_token.json` | OAuth tokens (auto-managed) |
| `{vault}/.twitter_state.json` | Watcher state — last mention ID, etc. |
| `Logs/twitter_mcp_YYYY-MM-DD.log` | MCP server logs |

---

## Twitter API v2 Rate Limits

| Endpoint | Free tier | Basic tier |
|----------|-----------|------------|
| `GET /2/users/:id/mentions` | 5 req / 15 min | 180 req / 15 min |
| `GET /2/users/:id/timelines/*` | 1 req / 15 min | 180 req / 15 min |
| `POST /2/tweets` | 17 tweets / 24 hr | 100 tweets / 24 hr |
| `GET /2/tweets/:id` | 15 req / 15 min | 15 req / 15 min |

> The watcher interval defaults to 300 s (5 min) which is safe for Basic tier.
> For Free tier, increase `TWITTER_CHECK_INTERVAL` to at least `900` (15 min).

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `No Twitter auth available` | Run `twitter_auth.py` or set `TWITTER_BEARER_TOKEN` |
| `Token refresh failed` | Re-run `twitter_auth.py` — refresh token may be expired/revoked |
| `HTTP 403 on dm_events` | `dm.read` scope not granted — re-run auth flow |
| `HTTP 429 rate limit` | Watcher backs off automatically; increase `TWITTER_CHECK_INTERVAL` |
| `TWITTER_USER_ID not set` | Add it to `.env`; find it with `GET /2/users/by/username/:username` |
| Tweet rejected at 280 chars | `post_tweet` counts Unicode characters — emoji count as 2 |
| `offline.access` not in scope | Re-run `twitter_auth.py` — token cannot be refreshed without it |
