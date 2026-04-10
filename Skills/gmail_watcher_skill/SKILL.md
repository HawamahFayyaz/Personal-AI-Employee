# Skill: GmailWatcher

## What it does

`GmailWatcher` polls Gmail every 2 minutes for **new unread important emails**
and converts each one into a structured task file inside `Needs_Action/`.

The watcher tracks every processed message ID in `.gmail_seen_ids.json` so
emails are never surfaced more than once, even across restarts.

## File locations

```
Watchers/gmail_watcher.py   ← watcher implementation
Watchers/gmail_auth.py      ← OAuth2 helper (dependency)
credentials.json            ← Google Cloud OAuth client secret (vault root)
token.json                  ← auto-created on first run (vault root)
.gmail_seen_ids.json        ← processed-ID log (vault root, auto-created)
```

## Class interface

```python
class GmailWatcher(BaseWatcher):
    def __init__(self, vault_path: str, check_interval: int = 120): ...
    def run(self): ...   # inherited blocking poll loop from BaseWatcher
```

Inherits from `BaseWatcher` (`Watchers/base_watcher.py`).

### Constructor parameters

| Parameter        | Type  | Default | Description                                    |
|------------------|-------|---------|------------------------------------------------|
| `vault_path`     | `str` | —       | Absolute or relative path to the vault root.   |
| `check_interval` | `int` | `120`   | Seconds between Gmail polls (default 2 min).   |

## Gmail query

```
is:unread is:important newer_than:1d
```

Only emails Gmail has marked **Important** and received in the last 24 hours
are considered. Adjust `_GMAIL_QUERY` in the source to change this.

## Output format

Each email produces one file in `Needs_Action/`:

```
Needs_Action/
  EMAIL_20251001T143200Z_Meeting_follow_up.md
```

### Action file schema

```markdown
---
type: email
from: alice@example.com
subject: Meeting follow-up
received: 2025-10-01T14:32:00+00:00
priority: high
status: pending
message_id: 18c3f2a9b1d4e507
---

## Email Content

Hi, just following up on our meeting yesterday…

## Suggested Actions
- [ ] Reply to sender
- [ ] Forward to relevant party
- [ ] Archive after processing
```

## Usage

### Standalone

```python
import logging
from Watchers.gmail_watcher import GmailWatcher

logging.basicConfig(level=logging.INFO)

watcher = GmailWatcher(vault_path='/path/to/vault')
watcher.run()   # blocks; first run opens browser for OAuth2 consent
```

### In run_watchers.py (alongside other watchers)

```python
from Watchers.gmail_watcher import GmailWatcher

gmail_watcher = GmailWatcher(vault_path=str(VAULT_ROOT), check_interval=120)
watchers = [fs_watcher, gmail_watcher]
```

Then each watcher is started in its own daemon thread by the existing runner
loop — no other changes needed.

## First-run OAuth2 flow

1. Ensure `credentials.json` exists in the vault root (download from
   Google Cloud Console → APIs & Services → Credentials).
2. Start the watcher. A browser window opens for the Google consent screen.
3. After approval, `token.json` is written to the vault root.
4. All subsequent runs load `token.json` and refresh it silently when expired.

## Error handling

| Scenario                        | Behaviour                                              |
|---------------------------------|--------------------------------------------------------|
| HTTP 429 / 5xx from Gmail API   | Exponential backoff, up to 5 retries (2, 4, 8, 16, 32 s) |
| Network / socket error          | Same exponential backoff                               |
| Non-retryable HTTP error        | Logged at ERROR level; poll cycle skipped              |
| Malformed seen-IDs JSON         | Logged at WARNING; starts with empty set               |
| Missing `credentials.json`      | `FileNotFoundError` raised with a clear message        |
| Individual message fetch fails  | Logged at ERROR; remaining messages still processed    |

## Processed-ID log (`.gmail_seen_ids.json`)

A sorted JSON array of Gmail message IDs written to the vault root after every
new email is processed. Deleting this file causes the watcher to reprocess all
emails that currently match the query.

```json
[
  "18c3f2a9b1d4e507",
  "18c3f2a9b1d4e508"
]
```

## Dependencies

| Package                      | Used for                                    |
|------------------------------|---------------------------------------------|
| `google-auth`                | OAuth2 credentials & token refresh          |
| `google-auth-oauthlib`       | Browser-based OAuth2 consent flow           |
| `google-api-python-client`   | Gmail REST API client (`googleapiclient`)   |

```bash
pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client
```
