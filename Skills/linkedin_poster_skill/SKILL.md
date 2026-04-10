# Skill: LinkedInPoster

## What it does

`LinkedInPoster` generates professional LinkedIn posts using Claude (reading
`Business_Goals.md` for context), saves them to `Pending_Approval/` for human
review, and publishes them to LinkedIn once moved to `Approved/`.

No post ever goes live without explicit human approval — the approval folder
acts as the gate.

---

## File locations

```
Watchers/linkedin_poster.py          ← main script
Pending_Approval/LINKEDIN_<date>.md  ← awaiting your review
Approved/LINKEDIN_<date>.md          ← cleared for publishing
Rejected/LINKEDIN_<date>.md          ← discarded drafts
Done/LINKEDIN_<date>.md              ← successfully published
Logs/linkedin_poster.log             ← full audit trail
.env                                 ← credentials & config (vault root)
```

---

## LinkedIn API credentials setup

### 1. Create a LinkedIn App

1. Go to <https://www.linkedin.com/developers/apps> → **Create App**.
2. Fill in App Name, LinkedIn Page, and Privacy Policy URL.
3. Under **Products**, request access to **Share on LinkedIn** (UGC Posts API).
4. Once approved, go to **Auth** → copy your **Client ID** and **Client Secret**.

### 2. Obtain an OAuth 2.0 Access Token

LinkedIn uses OAuth 2.0. The simplest path for a personal/solo account:

```bash
# Step A — Build the auth URL and open it in a browser:
CLIENT_ID=your_client_id
REDIRECT_URI=https://localhost
SCOPE=w_member_social

https://www.linkedin.com/oauth/v2/authorization
  ?response_type=code
  &client_id=$CLIENT_ID
  &redirect_uri=$REDIRECT_URI
  &scope=$SCOPE

# Step B — Exchange the code for a token:
curl -X POST https://www.linkedin.com/oauth/v2/accessToken \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=authorization_code" \
  -d "code=<auth_code_from_redirect>" \
  -d "redirect_uri=$REDIRECT_URI" \
  -d "client_id=$CLIENT_ID" \
  -d "client_secret=<your_secret>"
```

The response contains `access_token` (valid ~60 days) and `refresh_token`.

### 3. Find your Author URN

```bash
curl -H "Authorization: Bearer <access_token>" \
     https://api.linkedin.com/v2/userinfo
```

Look for the `sub` field — your URN is `urn:li:person:<sub>`.

### 4. Add credentials to `.env`

Create (or update) `.env` in the vault root:

```dotenv
# Claude
ANTHROPIC_API_KEY=sk-ant-...

# LinkedIn
LINKEDIN_ACCESS_TOKEN=AQX...
LINKEDIN_AUTHOR_URN=urn:li:person:XXXXXXXX

# Optional settings
DRY_RUN=false          # Set to true to skip real LinkedIn API calls
CHECK_INTERVAL=3600    # Seconds between Approved/ polls in continuous mode
```

---

## Approval flow

```
generate command
      │
      ▼
Claude reads Business_Goals.md
      │
      ▼
Post draft written to Pending_Approval/LINKEDIN_<date>.md
      │
      ▼  (you review the file)
      ├─ Move to Approved/   → publish command posts to LinkedIn
      └─ Move to Rejected/   → draft is discarded
```

### Approval file format

```markdown
---
type: approval_request
action: linkedin_post
platform: linkedin
created: 2026-04-07T10:00:00+00:00
status: pending
---

## Proposed Post

<generated post text here>

## To Approve
Move this file to the `/Approved` folder.

## To Reject
Move this file to the `/Rejected` folder.
```

---

## Usage

### Install dependencies

```bash
pip install anthropic requests python-dotenv
```

### Generate a new post (saves to Pending_Approval/)

```bash
python -m Watchers.linkedin_poster generate
# With optional style guidance:
python -m Watchers.linkedin_poster generate --style "focus on our invoice milestone"
```

### Publish all approved posts

```bash
python -m Watchers.linkedin_poster publish
```

### Continuous mode (generate daily + poll for approvals)

```bash
python -m Watchers.linkedin_poster run
```

In continuous mode the script:
1. Generates one post on startup if none is pending for today.
2. Polls `Approved/` every `CHECK_INTERVAL` seconds and publishes anything found.

### Dry-run mode

Set `DRY_RUN=true` in `.env`. The script logs what it *would* post to
`Logs/linkedin_poster.log` without making any LinkedIn API calls. Useful for
testing generation and the approval flow.

---

## Customising post frequency and style

| Knob | How to change |
|------|---------------|
| **Post frequency** | Set `CHECK_INTERVAL` in `.env` (default 3600 s = 1 h). Run `generate` on a cron schedule (e.g. daily at 9 AM) to control draft cadence. |
| **Tone / style** | Edit `_SYSTEM_PROMPT` in `linkedin_poster.py`. The current prompt targets solo entrepreneurs and emphasises value + authenticity. |
| **Style per post** | Pass `--style "..."` to the `generate` command for one-off guidance. |
| **Post length** | Change `_MAX_POST_LENGTH` (default 3000, LinkedIn's hard limit). |
| **Source context** | The poster always reads `Business_Goals.md`. Point it at additional files by editing `generate_post()`. |
| **Claude model** | Change the `model=` argument in `generate_post()`. |

---

## Running alongside other watchers

```python
# In run_watchers.py
import threading
from Watchers.linkedin_poster import publish_approved

def linkedin_poll_loop(interval=3600):
    import time
    while True:
        publish_approved()
        time.sleep(interval)

t = threading.Thread(target=linkedin_poll_loop, daemon=True)
t.start()
```

The `generate_and_queue()` function can be called from a separate scheduled
task (cron, Claude Code `/schedule`, etc.) to produce drafts on a cadence.

---

## Logs

Every action is appended to `Logs/linkedin_poster.log`:

| Event label | Meaning |
|-------------|---------|
| `POST_PENDING` | Draft saved to Pending_Approval/ |
| `POST_PUBLISHED` | Successfully posted; LinkedIn Post ID recorded |
| `POST_FAILED` | LinkedIn API error; file left in Approved/ for retry |
| `DRY_RUN_POST` | Dry-run mode; would-be post logged, nothing sent |

---

## Error handling

| Scenario | Behaviour |
|----------|-----------|
| Missing `ANTHROPIC_API_KEY` | `RuntimeError` with clear message |
| Missing `LINKEDIN_ACCESS_TOKEN` or `LINKEDIN_AUTHOR_URN` | `RuntimeError` with clear message |
| LinkedIn API HTTP error | Logged at ERROR; file stays in Approved/ for retry |
| `Business_Goals.md` missing | Warning logged; Claude generates with minimal context |
| Duplicate pending file for today | Counter suffix added (`LINKEDIN_2026-04-07_01.md`) |
| `anthropic` or `requests` not installed | `RuntimeError` with `pip install` instruction |
