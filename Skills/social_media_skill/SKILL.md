# Skill: Social Media (Facebook + Instagram)

## Purpose
Monitor Facebook and Instagram via the Meta Graph API, draft posts and replies
through the approval workflow, and fetch page insights — all integrated into
the AI Employee Vault.

## Trigger phrases
- "post to Facebook"
- "post to Instagram"
- "check social media messages"
- "reply to comment on [platform]"
- "get page insights"
- "what's new on Instagram/Facebook"
- "social media activity"

---

## Prerequisites — Meta Developer Setup

### Step 1: Create a Meta App

1. Go to <https://developers.facebook.com/apps> → **Create App**
2. Choose **Business** as the app type
3. Complete the app creation wizard

### Step 2: Add Required Products

In your app dashboard, add:
- **Facebook Login** (for token management)
- **Instagram Graph API** (for IG features)
- **Webhooks** (optional — for real-time updates; the watcher uses polling instead)

### Step 3: Required Permissions

Request these permissions in **App Review** (some require business verification):

| Permission | Required for |
|------------|-------------|
| `pages_show_list` | List managed Pages |
| `pages_read_engagement` | Read comments and mentions |
| `pages_manage_posts` | Publish to Facebook Page |
| `pages_messaging` | Read/send Messenger messages |
| `instagram_basic` | IG account info |
| `instagram_content_publish` | Post to Instagram |
| `instagram_manage_comments` | Read/reply to IG comments |
| `instagram_manage_messages` | Read IG DMs |
| `read_insights` | Page + post analytics |

### Step 4: Generate a Page Access Token

1. Go to [Graph API Explorer](https://developers.facebook.com/tools/explorer/)
2. Select your app and add the permissions above
3. Click **Generate Access Token** → grant permissions
4. Exchange for a **long-lived token** (60-day):
   ```
   GET https://graph.facebook.com/v19.0/oauth/access_token
     ?grant_type=fb_exchange_token
     &client_id={app_id}
     &client_secret={app_secret}
     &fb_exchange_token={short_lived_token}
   ```
5. For a **never-expiring Page token**, use the long-lived User token to call:
   ```
   GET https://graph.facebook.com/v19.0/{page_id}?fields=access_token
   ```

### Step 5: Find Your IDs

```bash
# List pages you manage (replace USER_TOKEN)
curl "https://graph.facebook.com/v19.0/me/accounts?access_token=USER_TOKEN"

# Get Instagram Business Account ID linked to a Page
curl "https://graph.facebook.com/v19.0/{PAGE_ID}?fields=instagram_business_account&access_token=PAGE_TOKEN"
```

### Step 6: Configure `.env`

Add to `{vault_root}/.env`:

```dotenv
# Meta / Facebook
META_PAGE_ACCESS_TOKEN=your_long_lived_page_token
META_PAGE_ID=123456789012345
META_IG_USER_ID=987654321098765   # Instagram Business Account ID
META_API_VERSION=v19.0            # optional, default: v19.0

# Watcher tuning
SOCIAL_CHECK_INTERVAL=300         # seconds between polls (default: 300)
SOCIAL_SUMMARY_HOUR=8             # UTC hour for daily summary (default: 8)
SOCIAL_LOOKBACK_POSTS=5           # recent posts to check for comments
```

---

## Running the Watcher

The watcher is auto-started by `run_watchers.py` when `META_PAGE_ACCESS_TOKEN`
and `META_PAGE_ID` are set. To skip it:

```bash
python Watchers/run_watchers.py --no-social
```

To run standalone:

```bash
python Watchers/social_media_watcher.py
```

---

## MCP Server Setup

```bash
cd MCP_Servers/social-mcp/
npm install
npm start          # production
npm run dev        # DRY_RUN=true — no files written, no API calls
```

Register in Claude Code settings (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "social-mcp": {
      "command": "node",
      "args": ["/path/to/vault/MCP_Servers/social-mcp/index.js"],
      "env": {
        "META_PAGE_ACCESS_TOKEN": "your_token",
        "META_PAGE_ID": "your_page_id",
        "META_IG_USER_ID": "your_ig_id"
      }
    }
  }
}
```

---

## Available MCP Tools

### `post_to_facebook(message, image_url?)`
Draft a Facebook Page post. **Does not publish immediately** — creates a
`Pending_Approval/SOCIAL_FACEBOOK_POST_*.md` file. Move to `Approved/` to publish.

```
post_to_facebook("Check out our new product launch! 🎉")
post_to_facebook("Big news this week", image_url="https://example.com/banner.jpg")
```

### `post_to_instagram(message, image_url)`
Draft an Instagram post. Image URL is **required**. Creates a pending approval file.

```
post_to_instagram("Behind the scenes 🎬", image_url="https://cdn.example.com/photo.jpg")
```

### `reply_to_comment(comment_id, message, platform?)`
Draft a reply to a comment. **Always requires approval.** Creates
`Pending_Approval/SOCIAL_SOCIAL_REPLY_*.md`.

```
reply_to_comment("123456789_987654321", "Thank you for your feedback!")
reply_to_comment("17858893269000001", "Great question!", platform="instagram")
```

### `get_page_insights(page_id?, metrics, period?)`
Fetch analytics directly. No approval needed (read-only).

```
get_page_insights(metrics="page_impressions,page_engaged_users", period="day")
get_page_insights(metrics="impressions,reach", period="week")  # Instagram
```

Common Facebook metrics: `page_impressions`, `page_engaged_users`, `page_post_engagements`, `page_fans`
Common Instagram metrics: `impressions`, `reach`, `profile_views`, `follower_count`

### `get_messages(platform?, unread_only?, limit?)`
Fetch message threads. No approval needed (read-only).

```
get_messages()                                    # Facebook, all threads
get_messages(platform="instagram", unread_only=true)
get_messages(platform="facebook", limit=5)
```

---

## Approval Workflow for Posts

```
MCP tool called
    │
    ▼
Pending_Approval/SOCIAL_*.md  ← file created with YAML frontmatter
    │
    ├─→ Move to Approved/   → ApprovalWatcher dispatches via social_dispatcher.py
    │                            facebook_post  → POST /{page_id}/feed
    │                            instagram_post → POST /{ig_id}/media → /media_publish
    │                            social_reply   → POST /{comment_id}/replies
    │
    └─→ Move to Rejected/   → Logged to Logs/approval_watcher.log, archived to Done/
```

---

## What the Watcher Detects

| Event | Action file prefix | Priority |
|-------|-------------------|----------|
| New Facebook Message | `FBMESSAGE_` | High |
| New Instagram DM | `IGMESSAGE_` | High |
| New Facebook Comment | `FBCOMMENT_` | Medium |
| New Instagram Comment | `IGCOMMENT_` | Medium |
| Facebook Mention/Tag | `FBMENTION_` | Medium |
| Instagram @Mention | `IGMENTION_` | Low |
| Daily Summary | `SOCIAL_SUMMARY_` | Low |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `META_PAGE_ACCESS_TOKEN not set` | Add token to `.env` |
| Token expired (60-day) | Re-generate or convert to never-expiring Page token |
| `OAuthException: (#200) The user hasn't authorized…` | Token lacks required permission — re-authorize |
| Instagram posts fail | Ensure account is an **Instagram Business or Creator** account linked to the Facebook Page |
| `instagram_manage_messages` errors | Requires completing **Business Verification** in Meta Business Manager |
| Rate limit (HTTP 429) | Watcher backs off automatically; reduce `SOCIAL_CHECK_INTERVAL` if needed |
| Daily summary not generated | Check `SOCIAL_SUMMARY_HOUR` is `<= current UTC hour` and `.social_seen_ids.json` `last_summary` field |

---

## Files

| Path | Role |
|------|------|
| `Watchers/social_media_watcher.py` | Polling watcher (Meta Graph API) |
| `Watchers/social_dispatcher.py` | Approval-watcher dispatch handlers |
| `MCP_Servers/social-mcp/index.js` | MCP server (5 tools) |
| `MCP_Servers/social-mcp/package.json` | Node.js dependencies |
| `{vault}/.social_seen_ids.json` | Seen-ID state (auto-created) |
| `Logs/social_mcp_YYYY-MM-DD.log` | MCP server logs |
