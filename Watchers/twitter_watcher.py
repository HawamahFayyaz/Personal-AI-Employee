"""
TwitterWatcher — polls Twitter/X API v2 for account activity.

Monitors:
  - Mentions (@username references)
  - Direct Messages (requires dm.read scope)

Creates action files in Needs_Action/ for each new item.
Generates a daily summary of tweet performance at TWITTER_SUMMARY_HOUR UTC.

Environment variables (from .env at vault root):
  TWITTER_BEARER_TOKEN     App-only Bearer Token (read-only fallback)
  TWITTER_CLIENT_ID        OAuth 2.0 Client ID (required for token refresh)
  TWITTER_CLIENT_SECRET    OAuth 2.0 Client Secret (confidential apps; omit for public)
  TWITTER_USER_ID          Authenticated user's numeric ID
  TWITTER_USERNAME         @handle without the @  (for display only)
  TWITTER_CHECK_INTERVAL   Seconds between polls (default: 300)
  TWITTER_SUMMARY_HOUR     UTC hour for daily summary (default: 8)
  DRY_RUN                  "true" → log without creating files

OAuth tokens are read from {vault_root}/.twitter_token.json (created by twitter_auth.py).
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

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

from Watchers.base_watcher import BaseWatcher

_API_BASE   = "https://api.twitter.com/2"
_MAX_RETRY  = 4
_BACKOFF    = 3    # base seconds for exponential backoff


class TwitterWatcher(BaseWatcher):
    """
    Polls Twitter/X API v2 for mentions and DMs.

    State is persisted in {vault_root}/.twitter_state.json.
    OAuth tokens are read from {vault_root}/.twitter_token.json.
    """

    def __init__(self, vault_path: str, check_interval: int = 300):
        super().__init__(vault_path, check_interval)

        self._bearer_token   = os.getenv("TWITTER_BEARER_TOKEN", "")
        self._client_id      = os.getenv("TWITTER_CLIENT_ID", "")
        self._client_secret  = os.getenv("TWITTER_CLIENT_SECRET", "")
        self._user_id        = os.getenv("TWITTER_USER_ID", "")
        self._username       = os.getenv("TWITTER_USERNAME", "")
        self._summary_hour   = int(os.getenv("TWITTER_SUMMARY_HOUR", "8"))
        self._dry_run        = os.getenv("DRY_RUN", "false").strip().lower() in ("1", "true", "yes")

        self._token_path = self.vault_path / ".twitter_token.json"
        self._state_path = self.vault_path / ".twitter_state.json"
        self._state: dict[str, Any] = self._load_state()

        if not self._bearer_token and not self._token_path.exists():
            self.logger.warning(
                "Neither TWITTER_BEARER_TOKEN nor .twitter_token.json found — "
                "TwitterWatcher will be a no-op. Run twitter_auth.py first."
            )

    # =========================================================================
    # State
    # =========================================================================

    def _load_state(self) -> dict[str, Any]:
        default: dict[str, Any] = {
            "last_mention_id":  None,
            "last_dm_event_id": None,
            "last_summary":     "",
        }
        if not self._state_path.exists():
            return default
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            for k, v in default.items():
                data.setdefault(k, v)
            return data
        except (json.JSONDecodeError, OSError):
            self.logger.warning("Could not parse %s — starting fresh.", self._state_path)
            return default

    def _save_state(self) -> None:
        try:
            self._state_path.write_text(
                json.dumps(self._state, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            self.logger.warning("Could not save Twitter state: %s", exc)

    # =========================================================================
    # Auth — prefers OAuth user context over app-only Bearer
    # =========================================================================

    def _load_tokens(self) -> dict:
        if not self._token_path.exists():
            return {}
        try:
            return json.loads(self._token_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_tokens(self, tokens: dict) -> None:
        try:
            self._token_path.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
        except OSError as exc:
            self.logger.warning("Could not save Twitter tokens: %s", exc)

    def _refresh_access_token(self, tokens: dict) -> dict | None:
        """Exchange refresh_token for a new access_token. Returns updated dict or None."""
        refresh_token = tokens.get("refresh_token", "")
        if not refresh_token or not self._client_id:
            self.logger.warning(
                "Cannot refresh: missing refresh_token or TWITTER_CLIENT_ID."
            )
            return None
        try:
            from Watchers.twitter_auth import refresh_tokens
            new = refresh_tokens(self._client_id, self._client_secret, refresh_token)
            if "error" in new:
                self.logger.error("Token refresh error: %s", new)
                return None
            # Merge: keep existing fields, override with new ones
            import base64 as _b64
            expires_in = int(new.get("expires_in", 7200))
            new["expires_at"] = int(time.time()) + expires_in - 60
            merged = {**tokens, **new}
            self._save_tokens(merged)
            self.logger.info("Access token refreshed successfully.")
            return merged
        except Exception as exc:
            self.logger.error("Token refresh failed: %s", exc)
            return None

    def _auth_header(self) -> str:
        """Return Authorization header value, refreshing if needed."""
        tokens = self._load_tokens()
        if tokens:
            expires_at = tokens.get("expires_at", 0)
            if int(time.time()) >= expires_at:
                self.logger.info("Access token expired — refreshing…")
                tokens = self._refresh_access_token(tokens) or tokens
            access_token = tokens.get("access_token", "")
            if access_token:
                return f"Bearer {access_token}"

        # Fallback to app-only Bearer token
        if self._bearer_token:
            return f"Bearer {self._bearer_token}"

        raise RuntimeError(
            "No valid auth available. Run twitter_auth.py or set TWITTER_BEARER_TOKEN."
        )

    # =========================================================================
    # HTTP helpers
    # =========================================================================

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        params = params or {}
        qs  = urlencode(params)
        url = f"{_API_BASE}/{endpoint}?{qs}" if qs else f"{_API_BASE}/{endpoint}"

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRY):
            try:
                auth = self._auth_header()
                req  = Request(url, headers={"Authorization": auth, "Accept": "application/json"})
                with urlopen(req, timeout=20) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    if "errors" in data and "data" not in data:
                        self.logger.warning("API errors in response: %s", data["errors"])
                    return data
            except HTTPError as exc:
                if exc.code == 429:
                    reset = int(exc.headers.get("x-rate-limit-reset", time.time() + 60))
                    wait  = max(reset - int(time.time()), 1)
                    self.logger.warning("Rate limited — sleeping %ss", wait)
                    time.sleep(wait)
                    last_exc = exc
                    continue
                if exc.code not in {500, 502, 503, 504}:
                    try:
                        body = exc.read().decode("utf-8", errors="replace")
                    except Exception:
                        body = str(exc)
                    raise RuntimeError(f"Twitter API HTTP {exc.code}: {body}") from exc
                wait = _BACKOFF ** attempt
                self.logger.warning("HTTP %s (attempt %d) — retry in %ss", exc.code, attempt + 1, wait)
                last_exc = exc
                time.sleep(wait)
            except URLError as exc:
                wait = _BACKOFF ** attempt
                self.logger.warning("Network error (attempt %d): %s — retry in %ss", attempt + 1, exc, wait)
                last_exc = exc
                time.sleep(wait)

        raise last_exc or RuntimeError(f"GET {endpoint} failed after {_MAX_RETRY} attempts")

    # =========================================================================
    # Poll: mentions
    # =========================================================================

    def _poll_mentions(self) -> list[dict]:
        if not self._user_id:
            self.logger.debug("TWITTER_USER_ID not set — skipping mentions poll.")
            return []

        params: dict[str, str] = {
            "tweet.fields": "id,text,created_at,author_id,conversation_id,public_metrics",
            "expansions":   "author_id",
            "user.fields":  "name,username",
            "max_results":  "20",
        }
        if self._state["last_mention_id"]:
            params["since_id"] = self._state["last_mention_id"]

        try:
            resp = self._get(f"users/{self._user_id}/mentions", params)
        except Exception as exc:
            self.logger.error("Mentions poll error: %s", exc)
            return []

        tweets = resp.get("data", [])
        if not tweets:
            return []

        # Build author lookup from expansions
        users = {u["id"]: u for u in resp.get("includes", {}).get("users", [])}

        # Update since_id to the highest tweet ID seen (tweets are newest-first)
        self._state["last_mention_id"] = tweets[0]["id"]

        new: list[dict] = []
        for tweet in reversed(tweets):  # oldest-first for action files
            author = users.get(tweet.get("author_id", ""), {})
            metrics = tweet.get("public_metrics", {})
            new.append({
                "type":          "twitter_mention",
                "id":            tweet["id"],
                "from":          author.get("name", "Unknown"),
                "username":      author.get("username", "unknown"),
                "text":          tweet.get("text", ""),
                "created_at":    tweet.get("created_at", ""),
                "conversation_id": tweet.get("conversation_id", tweet["id"]),
                "likes":         metrics.get("like_count", 0),
                "retweets":      metrics.get("retweet_count", 0),
                "replies":       metrics.get("reply_count", 0),
            })

        return new

    # =========================================================================
    # Poll: DMs
    # =========================================================================

    def _poll_dms(self) -> list[dict]:
        """Fetch new DM events. Requires dm.read scope in OAuth token."""
        params: dict[str, str] = {
            "dm_event.fields": "id,text,created_at,sender_id,dm_conversation_id",
            "expansions":      "sender_id",
            "user.fields":     "name,username",
            "max_results":     "20",
            "event_types":     "MessageCreate",
        }
        if self._state["last_dm_event_id"]:
            params["since_id"] = self._state["last_dm_event_id"]

        try:
            resp = self._get("dm_events", params)
        except RuntimeError as exc:
            # 403 = missing dm.read scope — log once, don't retry
            if "403" in str(exc):
                self.logger.debug("DM polling skipped (no dm.read scope): %s", exc)
            else:
                self.logger.error("DM poll error: %s", exc)
            return []
        except Exception as exc:
            self.logger.error("DM poll error: %s", exc)
            return []

        events = resp.get("data", [])
        if not events:
            return []

        users = {u["id"]: u for u in resp.get("includes", {}).get("users", [])}
        self._state["last_dm_event_id"] = events[0]["id"]

        new: list[dict] = []
        for event in reversed(events):
            sender = users.get(event.get("sender_id", ""), {})
            # Skip messages sent by the authenticated user
            if event.get("sender_id") == self._user_id:
                continue
            new.append({
                "type":            "twitter_dm",
                "id":              event["id"],
                "from":            sender.get("name", "Unknown"),
                "username":        sender.get("username", "unknown"),
                "text":            event.get("text", ""),
                "created_at":      event.get("created_at", ""),
                "conversation_id": event.get("dm_conversation_id", ""),
            })

        return new

    # =========================================================================
    # Daily summary
    # =========================================================================

    def _maybe_generate_daily_summary(self) -> None:
        now_utc = datetime.now(timezone.utc)
        today   = now_utc.strftime("%Y-%m-%d")
        if self._state.get("last_summary") == today:
            return
        if now_utc.hour < self._summary_hour:
            return

        self.logger.info("Generating daily Twitter summary for %s", today)
        self._write_daily_summary(today)
        self._state["last_summary"] = today
        self._save_state()

    def _write_daily_summary(self, today: str) -> None:
        recent_tweets: list[dict] = []
        user_metrics: dict = {}

        if self._user_id:
            # Recent tweets with public metrics
            try:
                resp = self._get(
                    f"users/{self._user_id}/tweets",
                    {
                        "tweet.fields": "id,text,public_metrics,created_at",
                        "max_results":  "10",
                    },
                )
                recent_tweets = resp.get("data", [])
            except Exception as exc:
                self.logger.warning("Could not fetch recent tweets for summary: %s", exc)

            # User profile metrics (followers, following, tweet count)
            try:
                resp = self._get(
                    f"users/{self._user_id}",
                    {"user.fields": "public_metrics,name,username"},
                )
                user_data    = resp.get("data", {})
                user_metrics = user_data.get("public_metrics", {})
                display_name = user_data.get("name", self._username)
            except Exception as exc:
                self.logger.warning("Could not fetch user metrics for summary: %s", exc)
                display_name = self._username

        filename = f"TWITTER_SUMMARY_{today}.md"
        dest     = self.needs_action / filename

        content = (
            "---\n"
            "type: twitter_summary\n"
            f"date: {today}\n"
            "platform: twitter\n"
            "priority: low\n"
            "status: pending\n"
            "---\n\n"
            f"# Daily Twitter/X Summary — {today}\n\n"
        )

        if user_metrics:
            content += "## Account Metrics\n\n"
            content += f"- **Followers**: {user_metrics.get('followers_count', 'n/a'):,}\n"
            content += f"- **Following**: {user_metrics.get('following_count', 'n/a'):,}\n"
            content += f"- **Total Tweets**: {user_metrics.get('tweet_count', 'n/a'):,}\n"
            content += f"- **Listed**: {user_metrics.get('listed_count', 'n/a'):,}\n\n"

        if recent_tweets:
            content += "## Recent Tweet Performance\n\n"
            content += "| Tweet | Likes | RTs | Replies | Quotes |\n"
            content += "|-------|-------|-----|---------|--------|\n"
            for t in recent_tweets[:10]:
                snippet = t.get("text", "")[:50].replace("|", "\\|")
                m       = t.get("public_metrics", {})
                content += (
                    f"| {snippet}… | {m.get('like_count', 0)} | "
                    f"{m.get('retweet_count', 0)} | {m.get('reply_count', 0)} | "
                    f"{m.get('quote_count', 0)} |\n"
                )
            content += "\n"

        content += "## Suggested Actions\n"
        content += "- [ ] Review engagement on top tweets\n"
        content += "- [ ] Reply to outstanding mentions (`get_mentions` in twitter-mcp)\n"
        content += "- [ ] Plan upcoming tweets\n"

        if self._dry_run:
            self.logger.info("[DRY RUN] Would write daily summary → %s", filename)
            return

        dest.write_text(content, encoding="utf-8")
        self.logger.info("Daily Twitter summary → %s", filename)

    # =========================================================================
    # BaseWatcher interface
    # =========================================================================

    def check_for_updates(self) -> list[dict]:
        try:
            self._auth_header()   # will raise if no auth at all
        except RuntimeError:
            return []

        new: list[dict] = []
        new.extend(self._poll_mentions())
        new.extend(self._poll_dms())

        if new:
            self._save_state()

        return new

    def create_action_file(self, data: list[dict]) -> None:
        priority_map = {"twitter_mention": "high", "twitter_dm": "high"}
        prefix_map   = {"twitter_mention": "TWMENTION", "twitter_dm": "TWDM"}

        for item in data:
            item_type = item.get("type", "twitter_item")
            ts_file   = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            safe_user = "".join(
                c if c.isalnum() or c in "-_" else "_"
                for c in item.get("username", "user")
            )[:25]
            prefix   = prefix_map.get(item_type, "TWITEM")
            filename = f"{prefix}_{ts_file}_{safe_user}.md"
            dest     = self.needs_action / filename

            conv_id  = item.get("conversation_id", item["id"])
            text     = item.get("text", "")
            fm = (
                "---\n"
                f"type: {item_type}\n"
                "platform: twitter\n"
                f"from: {item.get('from', 'Unknown')} (@{item.get('username', 'unknown')})\n"
                f"tweet_id: {item['id']}\n"
                f"conversation_id: {conv_id}\n"
                f"received: {item.get('created_at', '')}\n"
                f"priority: {priority_map.get(item_type, 'medium')}\n"
                "status: pending\n"
                "---\n"
            )

            type_label = "Mention" if item_type == "twitter_mention" else "Direct Message"
            body = (
                f"\n## Twitter/X {type_label}\n\n"
                f"**From:** {item.get('from', 'Unknown')} (@{item.get('username', '')})\n"
            )
            if item_type == "twitter_mention":
                body += (
                    f"**Metrics:** {item.get('likes', 0)} likes · "
                    f"{item.get('retweets', 0)} RTs · "
                    f"{item.get('replies', 0)} replies\n"
                )
            body += (
                f"\n---\n\n{text}\n\n"
                "## Suggested Actions\n"
                f"- [ ] Reply via MCP `reply_to_tweet` (tweet_id: `{item['id']}`)\n"
                "- [ ] Retweet or like\n"
                "- [ ] Escalate / archive\n"
            )

            if self._dry_run:
                self.logger.info("[DRY RUN] Would write → %s", filename)
                continue

            dest.write_text(fm + body, encoding="utf-8")
            self.logger.info(
                "New %s queued → %s (from=@%s)",
                type_label, filename, item.get("username", "?"),
            )

    # =========================================================================
    # run() — override to inject daily summary check
    # =========================================================================

    def run(self) -> None:
        self.logger.info(
            "Starting TwitterWatcher (interval=%ss, user_id=%s)",
            self.check_interval, self._user_id or "NOT SET",
        )
        while True:
            try:
                self._maybe_generate_daily_summary()
                updates = self.check_for_updates()
                if updates:
                    self.create_action_file(updates)
            except Exception:
                self.logger.exception("Unhandled error in TwitterWatcher — continuing.")
            time.sleep(self.check_interval)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    vault_path = str(Path(__file__).parent.parent)
    interval   = int(os.getenv("TWITTER_CHECK_INTERVAL", "300"))
    watcher    = TwitterWatcher(vault_path=vault_path, check_interval=interval)

    print("\n" + "=" * 60)
    print("Twitter/X Watcher")
    print("=" * 60)
    print(f"Vault       : {vault_path}")
    print(f"User ID     : {watcher._user_id or 'NOT SET — set TWITTER_USER_ID'}")
    print(f"Username    : @{watcher._username or 'NOT SET'}")
    print(f"Interval    : {interval}s")
    print(f"DRY_RUN     : {watcher._dry_run}")
    print("Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    try:
        watcher.run()
    except KeyboardInterrupt:
        print("\nStopping Twitter Watcher…")
