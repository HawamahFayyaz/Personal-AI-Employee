"""
SocialMediaWatcher — polls Meta Graph API for Facebook and Instagram activity.

Monitors:
  - Facebook Page Messenger conversations (new messages)
  - Instagram DMs (new messages)
  - Comments on recent Facebook / Instagram posts
  - Facebook mentions (posts where the Page is tagged)
  - Instagram mentions (@username tags in other users' posts)

Creates action files in Needs_Action/ for each new item.
Generates a daily summary at SOCIAL_SUMMARY_HOUR (default 08:00 UTC).

Environment variables (loaded from .env at vault root):
  META_PAGE_ACCESS_TOKEN   Long-lived Page Access Token
  META_PAGE_ID             Facebook Page numeric ID
  META_IG_USER_ID          Instagram Business Account User ID (linked to the Page)
  META_API_VERSION         Graph API version (default: v19.0)
  SOCIAL_CHECK_INTERVAL    Seconds between polls (default: 300)
  SOCIAL_SUMMARY_HOUR      UTC hour for daily summary (default: 8)
  SOCIAL_LOOKBACK_POSTS    Number of recent posts to check for new comments (default: 5)
  DRY_RUN                  "true" → log without creating files
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen, Request

# ---------------------------------------------------------------------------
# Optional dotenv
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

from Watchers.base_watcher import BaseWatcher

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_GRAPH_BASE = "https://graph.facebook.com"
_MAX_RETRIES = 4
_BACKOFF_BASE = 3         # seconds; wait = _BACKOFF_BASE ** attempt
_RETRYABLE_HTTP = {429, 500, 502, 503, 504}

# How many seen IDs to retain per category (prevents unbounded growth)
_MAX_SEEN = 5_000


class SocialMediaWatcher(BaseWatcher):
    """
    Polls Meta Graph API for Facebook and Instagram activity.

    The seen-ID store is persisted at vault_root/.social_seen_ids.json
    to prevent duplicate action files across restarts.
    """

    def __init__(self, vault_path: str, check_interval: int = 300):
        super().__init__(vault_path, check_interval)

        # ── Config ───────────────────────────────────────────────────────────
        self._token    = os.getenv("META_PAGE_ACCESS_TOKEN", "")
        self._page_id  = os.getenv("META_PAGE_ID", "")
        self._ig_id    = os.getenv("META_IG_USER_ID", "")
        self._api_ver  = os.getenv("META_API_VERSION", "v19.0")
        self._summary_hour = int(os.getenv("SOCIAL_SUMMARY_HOUR", "8"))
        self._lookback = int(os.getenv("SOCIAL_LOOKBACK_POSTS", "5"))
        self._dry_run  = os.getenv("DRY_RUN", "false").strip().lower() in ("1", "true", "yes")

        if not self._token:
            self.logger.warning(
                "META_PAGE_ACCESS_TOKEN not set — SocialMediaWatcher will be a no-op."
            )

        # ── State ─────────────────────────────────────────────────────────────
        self._state_path: Path = self.vault_path / ".social_seen_ids.json"
        self._state: dict[str, Any] = self._load_state()

        # ── Ensure Needs_Action exists ────────────────────────────────────────
        self.needs_action.mkdir(parents=True, exist_ok=True)

    # =========================================================================
    # State persistence
    # =========================================================================

    def _load_state(self) -> dict[str, Any]:
        default: dict[str, Any] = {
            "fb_messages":   [],
            "ig_messages":   [],
            "fb_comments":   [],
            "ig_comments":   [],
            "fb_mentions":   [],
            "ig_mentions":   [],
            "last_summary":  "",   # "YYYY-MM-DD"
        }
        if not self._state_path.exists():
            return default
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            # Merge so new keys from default are populated
            for k, v in default.items():
                data.setdefault(k, v)
            return data
        except (json.JSONDecodeError, OSError):
            self.logger.warning("Could not parse %s — starting fresh.", self._state_path)
            return default

    def _save_state(self) -> None:
        # Trim each list to _MAX_SEEN (keep most recent)
        for key in ("fb_messages", "ig_messages", "fb_comments", "ig_comments",
                    "fb_messages", "ig_mentions"):
            if isinstance(self._state.get(key), list) and len(self._state[key]) > _MAX_SEEN:
                self._state[key] = self._state[key][-_MAX_SEEN:]
        try:
            self._state_path.write_text(
                json.dumps(self._state, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            self.logger.warning("Could not save state: %s", exc)

    # =========================================================================
    # HTTP helper — wraps urllib with retries and backoff
    # =========================================================================

    def _graph_get(self, endpoint: str, params: dict | None = None) -> dict:
        """GET {_GRAPH_BASE}/{_api_ver}/{endpoint} and return parsed JSON."""
        params = params or {}
        params["access_token"] = self._token
        qs = urlencode(params)
        url = f"{_GRAPH_BASE}/{self._api_ver}/{endpoint}?{qs}"

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                req = Request(url, headers={"Accept": "application/json"})
                with urlopen(req, timeout=20) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except HTTPError as exc:
                if exc.code not in _RETRYABLE_HTTP:
                    self.logger.error("Graph API HTTP %s: %s — %s", exc.code, url, exc.reason)
                    raise
                wait = _BACKOFF_BASE ** attempt
                self.logger.warning("Graph API HTTP %s (attempt %d) — retry in %ss", exc.code, attempt + 1, wait)
                last_exc = exc
                time.sleep(wait)
            except URLError as exc:
                wait = _BACKOFF_BASE ** attempt
                self.logger.warning("Network error (attempt %d): %s — retry in %ss", attempt + 1, exc, wait)
                last_exc = exc
                time.sleep(wait)

        raise last_exc or RuntimeError(f"Graph API GET failed after {_MAX_RETRIES} attempts")

    def _graph_paginate(self, endpoint: str, params: dict | None = None, max_pages: int = 5) -> list[dict]:
        """Follow cursor-based pagination and return all items across pages."""
        items: list[dict] = []
        params = dict(params or {})
        page = 0

        while page < max_pages:
            data = self._graph_get(endpoint, params)
            items.extend(data.get("data", []))
            nxt = data.get("paging", {}).get("next")
            after = data.get("paging", {}).get("cursors", {}).get("after")
            if not nxt or not after:
                break
            params["after"] = after
            page += 1

        return items

    # =========================================================================
    # Poll helpers
    # =========================================================================

    def _poll_fb_messages(self) -> list[dict]:
        """Fetch new Facebook Messenger conversations with unread messages."""
        if not self._page_id:
            return []
        new: list[dict] = []
        seen = set(self._state["fb_messages"])
        try:
            threads = self._graph_paginate(
                f"{self._page_id}/conversations",
                {"platform": "messenger", "fields": "id,participants,messages{id,message,from,created_time},unread_count"},
            )
        except Exception as exc:
            self.logger.error("FB messages fetch error: %s", exc)
            return []

        for thread in threads:
            if thread.get("unread_count", 0) == 0:
                continue
            for msg in thread.get("messages", {}).get("data", []):
                uid = f"fb_msg:{msg['id']}"
                if uid in seen:
                    continue
                sender = msg.get("from", {})
                new.append({
                    "type":      "fb_message",
                    "platform":  "facebook",
                    "id":        uid,
                    "raw_id":    msg["id"],
                    "from":      sender.get("name", "Unknown"),
                    "from_id":   sender.get("id", ""),
                    "content":   msg.get("message", ""),
                    "timestamp": msg.get("created_time", ""),
                    "thread_id": thread["id"],
                    "context":   f"Thread: {thread['id']}",
                })
                seen.add(uid)
                self._state["fb_messages"].append(uid)

        return new

    def _poll_ig_messages(self) -> list[dict]:
        """Fetch new Instagram DMs."""
        if not self._ig_id:
            return []
        new: list[dict] = []
        seen = set(self._state["ig_messages"])
        try:
            threads = self._graph_paginate(
                f"{self._ig_id}/conversations",
                {"platform": "instagram", "fields": "id,participants,messages{id,message,from,created_time},unread_count"},
            )
        except Exception as exc:
            self.logger.error("IG messages fetch error: %s", exc)
            return []

        for thread in threads:
            if thread.get("unread_count", 0) == 0:
                continue
            for msg in thread.get("messages", {}).get("data", []):
                uid = f"ig_msg:{msg['id']}"
                if uid in seen:
                    continue
                sender = msg.get("from", {})
                new.append({
                    "type":      "ig_message",
                    "platform":  "instagram",
                    "id":        uid,
                    "raw_id":    msg["id"],
                    "from":      sender.get("username", sender.get("name", "Unknown")),
                    "from_id":   sender.get("id", ""),
                    "content":   msg.get("message", ""),
                    "timestamp": msg.get("created_time", ""),
                    "thread_id": thread["id"],
                    "context":   "Instagram DM",
                })
                seen.add(uid)
                self._state["ig_messages"].append(uid)

        return new

    def _poll_fb_comments(self) -> list[dict]:
        """Check recent Facebook Page posts for new comments."""
        if not self._page_id:
            return []
        new: list[dict] = []
        seen = set(self._state["fb_comments"])
        try:
            posts = self._graph_get(
                f"{self._page_id}/posts",
                {"fields": "id,message,created_time", "limit": str(self._lookback)},
            )
        except Exception as exc:
            self.logger.error("FB posts fetch error: %s", exc)
            return []

        for post in posts.get("data", []):
            post_id = post["id"]
            post_snippet = (post.get("message") or "")[:60]
            try:
                comments = self._graph_paginate(
                    f"{post_id}/comments",
                    {"fields": "id,message,from,created_time"},
                )
            except Exception as exc:
                self.logger.warning("Could not fetch comments for post %s: %s", post_id, exc)
                continue

            for comment in comments:
                uid = f"fb_comment:{comment['id']}"
                if uid in seen:
                    continue
                commenter = comment.get("from", {})
                new.append({
                    "type":      "fb_comment",
                    "platform":  "facebook",
                    "id":        uid,
                    "raw_id":    comment["id"],
                    "from":      commenter.get("name", "Unknown"),
                    "from_id":   commenter.get("id", ""),
                    "content":   comment.get("message", ""),
                    "timestamp": comment.get("created_time", ""),
                    "post_id":   post_id,
                    "context":   f'Comment on: "{post_snippet}"',
                })
                seen.add(uid)
                self._state["fb_comments"].append(uid)

        return new

    def _poll_ig_comments(self) -> list[dict]:
        """Check recent Instagram media for new comments."""
        if not self._ig_id:
            return []
        new: list[dict] = []
        seen = set(self._state["ig_comments"])
        try:
            media = self._graph_get(
                f"{self._ig_id}/media",
                {"fields": "id,caption,timestamp", "limit": str(self._lookback)},
            )
        except Exception as exc:
            self.logger.error("IG media fetch error: %s", exc)
            return []

        for item in media.get("data", []):
            media_id = item["id"]
            caption_snippet = (item.get("caption") or "")[:60]
            try:
                comments = self._graph_paginate(
                    f"{media_id}/comments",
                    {"fields": "id,text,username,timestamp"},
                )
            except Exception as exc:
                self.logger.warning("Could not fetch IG comments for %s: %s", media_id, exc)
                continue

            for comment in comments:
                uid = f"ig_comment:{comment['id']}"
                if uid in seen:
                    continue
                new.append({
                    "type":      "ig_comment",
                    "platform":  "instagram",
                    "id":        uid,
                    "raw_id":    comment["id"],
                    "from":      comment.get("username", "unknown"),
                    "from_id":   "",
                    "content":   comment.get("text", ""),
                    "timestamp": comment.get("timestamp", ""),
                    "post_id":   media_id,
                    "context":   f'Comment on: "{caption_snippet}"',
                })
                seen.add(uid)
                self._state["ig_comments"].append(uid)

        return new

    def _poll_fb_mentions(self) -> list[dict]:
        """Fetch Facebook posts where the Page has been tagged."""
        if not self._page_id:
            return []
        new: list[dict] = []
        seen = set(self._state["fb_mentions"])
        try:
            tagged = self._graph_paginate(
                f"{self._page_id}/tagged",
                {"fields": "id,message,from,created_time,permalink_url"},
            )
        except Exception as exc:
            self.logger.error("FB mentions fetch error: %s", exc)
            return []

        for post in tagged:
            uid = f"fb_mention:{post['id']}"
            if uid in seen:
                continue
            author = post.get("from", {})
            new.append({
                "type":      "fb_mention",
                "platform":  "facebook",
                "id":        uid,
                "raw_id":    post["id"],
                "from":      author.get("name", "Unknown"),
                "from_id":   author.get("id", ""),
                "content":   post.get("message", ""),
                "timestamp": post.get("created_time", ""),
                "url":       post.get("permalink_url", ""),
                "context":   "Facebook mention/tag",
            })
            seen.add(uid)
            self._state["fb_mentions"].append(uid)

        return new

    def _poll_ig_mentions(self) -> list[dict]:
        """Fetch Instagram media where the account has been @mentioned."""
        if not self._ig_id:
            return []
        new: list[dict] = []
        seen = set(self._state["ig_mentions"])
        try:
            tags = self._graph_paginate(
                f"{self._ig_id}/tags",
                {"fields": "id,caption,media_type,timestamp,permalink"},
            )
        except Exception as exc:
            self.logger.error("IG mentions fetch error: %s", exc)
            return []

        for media in tags:
            uid = f"ig_mention:{media['id']}"
            if uid in seen:
                continue
            new.append({
                "type":      "ig_mention",
                "platform":  "instagram",
                "id":        uid,
                "raw_id":    media["id"],
                "from":      "Instagram user",
                "from_id":   "",
                "content":   media.get("caption", ""),
                "timestamp": media.get("timestamp", ""),
                "url":       media.get("permalink", ""),
                "context":   f"Instagram @mention ({media.get('media_type', 'post')})",
            })
            seen.add(uid)
            self._state["ig_mentions"].append(uid)

        return new

    # =========================================================================
    # Daily summary
    # =========================================================================

    def _maybe_generate_daily_summary(self) -> None:
        """Generate a daily summary MD file if not done yet today."""
        now_utc = datetime.now(timezone.utc)
        today = now_utc.strftime("%Y-%m-%d")

        if self._state.get("last_summary") == today:
            return
        if now_utc.hour < self._summary_hour:
            return

        self.logger.info("Generating daily social media summary for %s", today)
        summary = self._build_daily_summary(today)
        self._write_summary(summary, today)
        self._state["last_summary"] = today
        self._save_state()

    def _build_daily_summary(self, today: str) -> dict[str, Any]:
        """Query the Graph API for today's insights and compile a summary dict."""
        summary: dict[str, Any] = {
            "date": today,
            "fb_page_insights": {},
            "ig_insights": {},
            "errors": [],
        }

        if self._page_id:
            try:
                insights = self._graph_get(
                    f"{self._page_id}/insights",
                    {
                        "metric": "page_impressions,page_engaged_users,page_post_engagements,page_fans",
                        "period": "day",
                    },
                )
                for item in insights.get("data", []):
                    name = item.get("name", "")
                    values = item.get("values", [{}])
                    summary["fb_page_insights"][name] = values[-1].get("value", 0) if values else 0
            except Exception as exc:
                summary["errors"].append(f"FB insights: {exc}")

        if self._ig_id:
            try:
                ig_insights = self._graph_get(
                    f"{self._ig_id}/insights",
                    {
                        "metric": "impressions,reach,profile_views,follower_count",
                        "period": "day",
                    },
                )
                for item in ig_insights.get("data", []):
                    name = item.get("name", "")
                    values = item.get("values", [{}])
                    summary["ig_insights"][name] = values[-1].get("value", 0) if values else 0
            except Exception as exc:
                summary["errors"].append(f"IG insights: {exc}")

        return summary

    def _write_summary(self, summary: dict[str, Any], today: str) -> None:
        """Write the daily summary to Needs_Action/."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"SOCIAL_SUMMARY_{today}.md"
        dest = self.needs_action / filename

        fb = summary.get("fb_page_insights", {})
        ig = summary.get("ig_insights", {})
        errors = summary.get("errors", [])

        content = (
            "---\n"
            "type: social_summary\n"
            f"date: {today}\n"
            "platform: facebook,instagram\n"
            "priority: low\n"
            "status: pending\n"
            "---\n\n"
            f"# Daily Social Media Summary — {today}\n\n"
            "## Facebook Page Insights\n\n"
        )

        if fb:
            for metric, value in fb.items():
                label = metric.replace("page_", "").replace("_", " ").title()
                content += f"- **{label}**: {value:,}\n"
        else:
            content += "_No Facebook data available_\n"

        content += "\n## Instagram Insights\n\n"
        if ig:
            for metric, value in ig.items():
                label = metric.replace("_", " ").title()
                content += f"- **{label}**: {value:,}\n"
        else:
            content += "_No Instagram data available_\n"

        if errors:
            content += "\n## Errors\n\n"
            for err in errors:
                content += f"- {err}\n"

        content += "\n## Suggested Actions\n- [ ] Review engagement metrics\n- [ ] Plan content for tomorrow\n- [ ] Respond to any outstanding comments or messages\n"

        if self._dry_run:
            self.logger.info("[DRY RUN] Would write summary → %s", filename)
            return

        dest.write_text(content, encoding="utf-8")
        self.logger.info("Daily summary written → %s", filename)

    # =========================================================================
    # BaseWatcher interface
    # =========================================================================

    def check_for_updates(self) -> list[dict]:
        """Poll all Meta Graph API sources and return new activity items."""
        if not self._token:
            return []

        new: list[dict] = []
        new.extend(self._poll_fb_messages())
        new.extend(self._poll_ig_messages())
        new.extend(self._poll_fb_comments())
        new.extend(self._poll_ig_comments())
        new.extend(self._poll_fb_mentions())
        new.extend(self._poll_ig_mentions())

        if new:
            self._save_state()

        return new

    def create_action_file(self, data: list[dict]) -> None:
        """Write a Needs_Action MD file for each new social activity item."""
        type_labels = {
            "fb_message":  "Facebook Message",
            "ig_message":  "Instagram DM",
            "fb_comment":  "Facebook Comment",
            "ig_comment":  "Instagram Comment",
            "fb_mention":  "Facebook Mention",
            "ig_mention":  "Instagram Mention",
        }
        priority_map = {
            "fb_message": "high",
            "ig_message": "high",
            "fb_comment": "medium",
            "ig_comment": "medium",
            "fb_mention": "medium",
            "ig_mention": "low",
        }

        for item in data:
            item_type  = item.get("type", "social")
            platform   = item.get("platform", "social")
            raw_id     = item.get("raw_id", item.get("id", ""))
            from_name  = item.get("from", "Unknown")
            content    = item.get("content", "")
            timestamp  = item.get("timestamp", "")
            context    = item.get("context", "")
            url        = item.get("url", "")
            post_id    = item.get("post_id", "")
            thread_id  = item.get("thread_id", "")

            label      = type_labels.get(item_type, "Social Activity")
            priority   = priority_map.get(item_type, "medium")
            prefix     = item_type.upper().replace("_", "")
            ts_file    = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            safe_from  = "".join(c if c.isalnum() or c in " -_" else "_" for c in from_name)[:30].strip()
            filename   = f"{prefix}_{ts_file}_{safe_from}.md"
            dest       = self.needs_action / filename

            reply_action = "social_reply"
            ref_id       = post_id or thread_id or raw_id

            frontmatter = (
                "---\n"
                f"type: {item_type}\n"
                f"platform: {platform}\n"
                f"from: {from_name}\n"
                f"item_id: {raw_id}\n"
                f"received: {timestamp}\n"
                f"priority: {priority}\n"
                "status: pending\n"
            )
            if ref_id:
                frontmatter += f"ref_id: {ref_id}\n"
            if url:
                frontmatter += f"url: {url}\n"
            frontmatter += "---\n"

            body = (
                f"\n## {label}\n\n"
                f"**From:** {from_name}\n"
                f"**Platform:** {platform.title()}\n"
                f"**Context:** {context}\n"
            )
            if url:
                body += f"**URL:** {url}\n"
            body += (
                f"\n---\n\n"
                f"{content}\n\n"
                "## Suggested Actions\n"
                f"- [ ] Reply via MCP `reply_to_comment` (id: `{raw_id}`)\n"
                f"- [ ] Escalate to team\n"
                f"- [ ] Archive / dismiss\n"
            )

            if self._dry_run:
                self.logger.info("[DRY RUN] Would write action file → %s", filename)
                continue

            dest.write_text(frontmatter + body, encoding="utf-8")
            self.logger.info(
                "New %s queued → %s  (from=%s)",
                label, filename, from_name,
            )

    # =========================================================================
    # run() — override to inject daily summary check
    # =========================================================================

    def run(self) -> None:
        self.logger.info(
            "Starting SocialMediaWatcher (interval=%ss, page=%s, ig=%s)",
            self.check_interval, self._page_id or "NOT SET", self._ig_id or "NOT SET",
        )
        while True:
            try:
                self._maybe_generate_daily_summary()
                updates = self.check_for_updates()
                if updates:
                    self.create_action_file(updates)
            except Exception:
                self.logger.exception(
                    "Unhandled error in SocialMediaWatcher — continuing."
                )
            time.sleep(self.check_interval)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    vault_path = str(Path(__file__).parent.parent)
    interval   = int(os.getenv("SOCIAL_CHECK_INTERVAL", "300"))
    watcher    = SocialMediaWatcher(vault_path=vault_path, check_interval=interval)

    print("\n" + "=" * 60)
    print("Social Media Watcher")
    print("=" * 60)
    print(f"Vault       : {vault_path}")
    print(f"Page ID     : {watcher._page_id or 'NOT SET'}")
    print(f"IG User ID  : {watcher._ig_id or 'NOT SET'}")
    print(f"Interval    : {interval}s")
    print(f"DRY_RUN     : {watcher._dry_run}")
    print("Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    try:
        watcher.run()
    except KeyboardInterrupt:
        print("\nStopping Social Media Watcher…")
