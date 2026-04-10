"""
social_dispatcher.py — approval-watcher dispatch handlers for social media actions.

Registered in approval_watcher._DISPATCHERS as:
  facebook_post  → _dispatch_facebook_post
  instagram_post → _dispatch_instagram_post
  social_reply   → _dispatch_social_reply

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
from urllib.parse import urlencode
from urllib.request import urlopen, Request

_GRAPH_BASE = "https://graph.facebook.com"
_MAX_RETRIES = 3
_BACKOFF_BASE = 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_env() -> tuple[str, str, str, str]:
    """Return (token, page_id, ig_user_id, api_version)."""
    return (
        os.getenv("META_PAGE_ACCESS_TOKEN", ""),
        os.getenv("META_PAGE_ID", ""),
        os.getenv("META_IG_USER_ID", ""),
        os.getenv("META_API_VERSION", "v19.0"),
    )


def _is_dry_run() -> bool:
    return os.getenv("DRY_RUN", "false").strip().lower() in ("1", "true", "yes")


def _graph_post(endpoint: str, payload: dict, api_ver: str = "v19.0") -> dict:
    """POST to the Graph API with retries. Returns parsed response JSON."""
    token = os.getenv("META_PAGE_ACCESS_TOKEN", "")
    url = f"{_GRAPH_BASE}/{api_ver}/{endpoint}"
    payload = dict(payload)
    payload["access_token"] = token
    data = urlencode(payload).encode("utf-8")

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            req = Request(url, data=data, method="POST",
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504}:
                body = ""
                try:
                    body = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                raise RuntimeError(f"Graph API HTTP {exc.code}: {body}") from exc
            wait = _BACKOFF_BASE ** attempt
            time.sleep(wait)
            last_exc = exc
        except Exception as exc:
            wait = _BACKOFF_BASE ** attempt
            time.sleep(wait)
            last_exc = exc

    raise last_exc or RuntimeError("Graph API POST failed after retries")


def _graph_get(endpoint: str, params: dict | None = None, api_ver: str = "v19.0") -> dict:
    """GET from the Graph API."""
    params = params or {}
    params["access_token"] = os.getenv("META_PAGE_ACCESS_TOKEN", "")
    qs = urlencode(params)
    url = f"{_GRAPH_BASE}/{api_ver}/{endpoint}?{qs}"
    req = Request(url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Dispatch: Facebook post
# ---------------------------------------------------------------------------

def dispatch_facebook_post(
    filepath: Path,
    fm: dict[str, Any],
    body: str,
    logger: logging.Logger,
) -> tuple[bool, str]:
    """Post an approved message to the Facebook Page feed."""
    token, page_id, _, api_ver = _get_env()

    if not token or not page_id:
        return False, "META_PAGE_ACCESS_TOKEN or META_PAGE_ID not configured."

    message   = str(fm.get("message", "")).strip() or body.strip()
    image_url = str(fm.get("image_url", "")).strip()

    if not message:
        return False, "No message text found in frontmatter or body."

    if _is_dry_run():
        logger.info("[DRY RUN] Would post to Facebook Page %s:\n%s", page_id, message)
        return True, f"DRY RUN — Facebook post to page {page_id} logged, not sent."

    try:
        payload: dict[str, str] = {"message": message}
        if image_url:
            # Use the link attachment to show a preview image
            payload["link"] = image_url
        result = _graph_post(f"{page_id}/feed", payload, api_ver)
        post_id = result.get("id", "unknown")
        logger.info("Facebook post published: %s", post_id)
        return True, f"Facebook post published (ID: {post_id})"
    except Exception as exc:
        return False, f"Facebook post error: {exc}"


# ---------------------------------------------------------------------------
# Dispatch: Instagram post (2-step: create container → publish)
# ---------------------------------------------------------------------------

def dispatch_instagram_post(
    filepath: Path,
    fm: dict[str, Any],
    body: str,
    logger: logging.Logger,
) -> tuple[bool, str]:
    """Publish an approved Instagram media post (image required)."""
    token, _, ig_id, api_ver = _get_env()

    if not token or not ig_id:
        return False, "META_PAGE_ACCESS_TOKEN or META_IG_USER_ID not configured."

    caption   = str(fm.get("message", "")).strip() or body.strip()
    image_url = str(fm.get("image_url", "")).strip()

    if not image_url:
        return False, "Instagram posts require an image_url. None found in frontmatter."

    if _is_dry_run():
        logger.info("[DRY RUN] Would post to Instagram IG user %s:\n%s\nimage: %s", ig_id, caption, image_url)
        return True, f"DRY RUN — Instagram post for {ig_id} logged, not sent."

    try:
        # Step 1: Create media container
        container_payload: dict[str, str] = {
            "image_url": image_url,
            "caption":   caption,
        }
        container = _graph_post(f"{ig_id}/media", container_payload, api_ver)
        creation_id = container.get("id")
        if not creation_id:
            return False, f"Media container creation returned no id: {container}"

        logger.info("Instagram media container created: %s", creation_id)

        # Step 2: Wait briefly for media processing, then publish
        time.sleep(5)
        publish = _graph_post(f"{ig_id}/media_publish", {"creation_id": creation_id}, api_ver)
        media_id = publish.get("id", "unknown")
        logger.info("Instagram post published: %s", media_id)
        return True, f"Instagram post published (media ID: {media_id})"
    except Exception as exc:
        return False, f"Instagram post error: {exc}"


# ---------------------------------------------------------------------------
# Dispatch: Reply to comment
# ---------------------------------------------------------------------------

def dispatch_social_reply(
    filepath: Path,
    fm: dict[str, Any],
    body: str,
    logger: logging.Logger,
) -> tuple[bool, str]:
    """Post an approved reply to a Facebook or Instagram comment."""
    token, _, _, api_ver = _get_env()

    if not token:
        return False, "META_PAGE_ACCESS_TOKEN not configured."

    comment_id = str(fm.get("comment_id", "")).strip()
    message    = str(fm.get("message", "")).strip() or body.strip()
    platform   = str(fm.get("platform", "facebook")).strip().lower()

    if not comment_id:
        return False, "No comment_id found in frontmatter."
    if not message:
        return False, "No reply message found in frontmatter or body."

    if _is_dry_run():
        logger.info(
            "[DRY RUN] Would reply to %s comment %s:\n%s",
            platform, comment_id, message,
        )
        return True, f"DRY RUN — Reply to {platform} comment {comment_id} logged, not sent."

    try:
        result = _graph_post(
            f"{comment_id}/replies",
            {"message": message},
            api_ver,
        )
        reply_id = result.get("id", "unknown")
        logger.info("Replied to %s comment %s → reply ID %s", platform, comment_id, reply_id)
        return True, f"Reply posted to {platform} comment {comment_id} (reply ID: {reply_id})"
    except Exception as exc:
        return False, f"Reply post error: {exc}"
