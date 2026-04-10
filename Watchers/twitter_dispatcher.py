"""
twitter_dispatcher.py — approval-watcher dispatch handlers for Twitter/X actions.

Registered in approval_watcher._DISPATCHERS as:
  tweet        → dispatch_tweet
  tweet_reply  → dispatch_tweet_reply

Each function has the signature:
  (filepath: Path, fm: dict, body: str, logger: Logger) -> tuple[bool, str]
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import urlopen, Request

_API_BASE   = "https://api.twitter.com/2"
_MAX_RETRY  = 3
_BACKOFF    = 2

VAULT_ROOT  = Path(__file__).parent.parent
TOKEN_PATH  = VAULT_ROOT / ".twitter_token.json"


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _load_tokens() -> dict:
    if not TOKEN_PATH.exists():
        return {}
    try:
        return json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _get_access_token(logger: logging.Logger) -> str:
    """Return a valid access token, refreshing if needed. Raises on failure."""
    tokens = _load_tokens()
    if not tokens:
        raise RuntimeError(
            ".twitter_token.json not found. Run twitter_auth.py to authorize."
        )

    expires_at = tokens.get("expires_at", 0)
    if int(time.time()) >= expires_at:
        logger.info("Twitter access token expired — refreshing…")
        client_id     = os.getenv("TWITTER_CLIENT_ID", "")
        client_secret = os.getenv("TWITTER_CLIENT_SECRET", "")
        refresh_token = tokens.get("refresh_token", "")

        if not client_id or not refresh_token:
            raise RuntimeError(
                "Cannot refresh token: TWITTER_CLIENT_ID or refresh_token missing."
            )
        try:
            from Watchers.twitter_auth import refresh_tokens, save_tokens
            new    = refresh_tokens(client_id, client_secret, refresh_token)
            merged = {**tokens, **new}
            import time as _t
            merged["expires_at"] = int(_t.time()) + int(new.get("expires_in", 7200)) - 60
            save_tokens(merged)
            tokens = merged
            logger.info("Twitter token refreshed.")
        except Exception as exc:
            raise RuntimeError(f"Token refresh failed: {exc}") from exc

    access_token = tokens.get("access_token", "")
    if not access_token:
        raise RuntimeError("No access_token in .twitter_token.json.")
    return access_token


def _is_dry_run() -> bool:
    return os.getenv("DRY_RUN", "false").strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# HTTP helper — POST with JSON body (Twitter v2 style)
# ---------------------------------------------------------------------------

def _twitter_post(endpoint: str, payload: dict, access_token: str) -> dict:
    url  = f"{_API_BASE}/{endpoint}"
    data = json.dumps(payload).encode("utf-8")

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRY):
        try:
            req = Request(
                url, data=data, method="POST",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type":  "application/json",
                },
            )
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 429:
                reset = int(exc.headers.get("x-rate-limit-reset", time.time() + 60))
                wait  = max(reset - int(time.time()), 1)
                time.sleep(wait)
                last_exc = exc
                continue
            if exc.code not in {500, 502, 503, 504}:
                try:
                    body = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    body = str(exc)
                raise RuntimeError(f"Twitter API HTTP {exc.code}: {body}") from exc
            time.sleep(_BACKOFF ** attempt)
            last_exc = exc
        except Exception as exc:
            time.sleep(_BACKOFF ** attempt)
            last_exc = exc

    raise last_exc or RuntimeError(f"Twitter POST {endpoint} failed after retries")


# ---------------------------------------------------------------------------
# Dispatcher: post a tweet
# ---------------------------------------------------------------------------

def dispatch_tweet(
    filepath: Path,
    fm: dict[str, Any],
    body: str,
    logger: logging.Logger,
) -> tuple[bool, str]:
    """Post an approved tweet."""
    text = str(fm.get("text", "")).strip() or body.strip()
    if not text:
        return False, "No tweet text in frontmatter 'text' field or body."

    if len(text) > 280:
        return False, f"Tweet text is {len(text)} characters — exceeds 280 limit."

    if _is_dry_run():
        logger.info("[DRY RUN] Would tweet:\n%s", text)
        return True, "DRY RUN — tweet logged, not sent."

    try:
        access_token = _get_access_token(logger)
        payload: dict[str, Any] = {"text": text}

        # Optional media attachment
        raw_media = fm.get("media_ids", "")
        if raw_media:
            ids = [m.strip() for m in str(raw_media).split(",") if m.strip()]
            if ids:
                payload["media"] = {"media_ids": ids}

        result   = _twitter_post("tweets", payload, access_token)
        tweet_id = result.get("data", {}).get("id", "unknown")
        logger.info("Tweet posted: %s", tweet_id)
        return True, f"Tweet posted (ID: {tweet_id})"
    except Exception as exc:
        return False, f"Tweet post error: {exc}"


# ---------------------------------------------------------------------------
# Dispatcher: reply to a tweet
# ---------------------------------------------------------------------------

def dispatch_tweet_reply(
    filepath: Path,
    fm: dict[str, Any],
    body: str,
    logger: logging.Logger,
) -> tuple[bool, str]:
    """Post an approved reply to a tweet."""
    in_reply_to = str(fm.get("in_reply_to_tweet_id", "")).strip()
    text        = str(fm.get("text", "")).strip() or body.strip()

    if not in_reply_to:
        return False, "No 'in_reply_to_tweet_id' in frontmatter."
    if not text:
        return False, "No reply text in frontmatter 'text' field or body."
    if len(text) > 280:
        return False, f"Reply text is {len(text)} characters — exceeds 280 limit."

    if _is_dry_run():
        logger.info(
            "[DRY RUN] Would reply to tweet %s:\n%s", in_reply_to, text
        )
        return True, f"DRY RUN — reply to {in_reply_to} logged, not sent."

    try:
        access_token = _get_access_token(logger)
        payload: dict[str, Any] = {
            "text":  text,
            "reply": {"in_reply_to_tweet_id": in_reply_to},
        }
        result   = _twitter_post("tweets", payload, access_token)
        reply_id = result.get("data", {}).get("id", "unknown")
        logger.info("Reply posted: %s → in_reply_to=%s", reply_id, in_reply_to)
        return True, f"Reply posted (reply ID: {reply_id}, in_reply_to: {in_reply_to})"
    except Exception as exc:
        return False, f"Tweet reply error: {exc}"
