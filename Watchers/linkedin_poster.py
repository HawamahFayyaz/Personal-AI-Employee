"""
LinkedInPoster — generates professional LinkedIn posts via Claude, saves them
to Pending_Approval/ for human review, then posts to LinkedIn when approved.

Flow:
  1. Reads Business_Goals.md to understand current projects/objectives.
  2. Calls Claude API to generate a LinkedIn post.
  3. Writes post to Pending_Approval/LINKEDIN_<date>.md with YAML frontmatter.
  4. A separate approval-watcher (or manual trigger) moves the file to Approved/.
  5. On next run, any files in Approved/ are posted to LinkedIn and logged.

Environment variables (via .env at vault root):
  ANTHROPIC_API_KEY   — required; Claude API key
  LINKEDIN_ACCESS_TOKEN — required; LinkedIn OAuth 2.0 access token
  LINKEDIN_AUTHOR_URN   — required; e.g. "urn:li:person:XXXXXXXX"
  DRY_RUN             — optional; set to "true" to skip real API calls
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Optional dotenv support
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass  # python-dotenv not installed — rely on env vars already set

# ---------------------------------------------------------------------------
# Third-party imports (graceful failure messages)
# ---------------------------------------------------------------------------
try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]


logger = logging.getLogger("LinkedInPoster")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_VAULT_ROOT = Path(__file__).parent.parent
_BUSINESS_GOALS = _VAULT_ROOT / "Business_Goals.md"
_PENDING_DIR = _VAULT_ROOT / "Pending_Approval"
_APPROVED_DIR = _VAULT_ROOT / "Approved"
_REJECTED_DIR = _VAULT_ROOT / "Rejected"
_LOGS_DIR = _VAULT_ROOT / "Logs"

_LINKEDIN_API_BASE = "https://api.linkedin.com/v2"
_MAX_POST_LENGTH = 3000  # LinkedIn character limit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_dirs() -> None:
    for d in (_PENDING_DIR, _APPROVED_DIR, _REJECTED_DIR, _LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _read_business_goals() -> str:
    if not _BUSINESS_GOALS.exists():
        logger.warning("Business_Goals.md not found at %s", _BUSINESS_GOALS)
        return "(Business_Goals.md not found — no context available)"
    return _BUSINESS_GOALS.read_text(encoding="utf-8")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _is_dry_run() -> bool:
    return os.getenv("DRY_RUN", "false").strip().lower() in ("1", "true", "yes")


def _log_event(label: str, content: str) -> None:
    """Append an event to Logs/linkedin_poster.log."""
    log_file = _LOGS_DIR / "linkedin_poster.log"
    timestamp = _iso_now()
    entry = f"\n[{timestamp}] {label}\n{content}\n{'—' * 60}\n"
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(entry)
    logger.info("Logged: %s", label)


# ---------------------------------------------------------------------------
# Step 1: Generate post with Claude
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a professional LinkedIn ghostwriter for a solo entrepreneur / small business.
Write a single LinkedIn post (max 280 words) that is:
- Engaging and authentic, written in first-person
- Focused on value, insight, or a concrete update — not generic hype
- Ends with a subtle call-to-action or question to spark comments
- Uses 2-3 relevant hashtags at the end (no more)
- NO emojis unless they add clear value
Return ONLY the post text — no preamble, no commentary."""


def generate_post(goals_content: str, style_hint: str = "") -> str:
    """Call Claude to generate a LinkedIn post based on Business_Goals.md."""
    if anthropic is None:
        raise RuntimeError(
            "anthropic package not installed. Run: pip install anthropic"
        )

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set.")

    client = anthropic.Anthropic(api_key=api_key)

    user_message = (
        "Here is our current Business_Goals.md:\n\n"
        f"{goals_content}\n\n"
        "Generate a LinkedIn post that shares a relevant insight, milestone, "
        "or reflection based on the above context."
    )
    if style_hint:
        user_message += f"\n\nStyle guidance: {style_hint}"

    logger.info("Calling Claude to generate LinkedIn post...")
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    post_text = response.content[0].text.strip()
    if len(post_text) > _MAX_POST_LENGTH:
        post_text = post_text[:_MAX_POST_LENGTH]
        logger.warning("Post truncated to %d characters.", _MAX_POST_LENGTH)

    return post_text


# ---------------------------------------------------------------------------
# Step 2: Save to Pending_Approval
# ---------------------------------------------------------------------------

def save_pending(post_text: str) -> Path:
    """Write the generated post to Pending_Approval/ and return the file path."""
    date_slug = _date_slug()
    filename = f"LINKEDIN_{date_slug}.md"
    dest = _PENDING_DIR / filename

    # If a file already exists for today, add a counter suffix
    counter = 1
    while dest.exists():
        filename = f"LINKEDIN_{date_slug}_{counter:02d}.md"
        dest = _PENDING_DIR / filename
        counter += 1

    content = (
        "---\n"
        "type: approval_request\n"
        "action: linkedin_post\n"
        "platform: linkedin\n"
        f"created: {_iso_now()}\n"
        "status: pending\n"
        "---\n"
        "\n"
        "## Proposed Post\n"
        "\n"
        f"{post_text}\n"
        "\n"
        "## To Approve\n"
        "Move this file to the `/Approved` folder.\n"
        "\n"
        "## To Reject\n"
        "Move this file to the `/Rejected` folder.\n"
    )

    dest.write_text(content, encoding="utf-8")
    logger.info("Pending approval file created: %s", dest.name)
    _log_event("POST_PENDING", f"File: {dest.name}\n\n{post_text}")
    return dest


# ---------------------------------------------------------------------------
# Step 3: Poll Approved/ and post to LinkedIn
# ---------------------------------------------------------------------------

def _extract_post_text(approval_md: str) -> str:
    """Pull the raw post text from between '## Proposed Post' and '## To Approve'."""
    marker_start = "## Proposed Post"
    marker_end = "## To Approve"
    start = approval_md.find(marker_start)
    end = approval_md.find(marker_end)
    if start == -1:
        return ""
    start += len(marker_start)
    if end == -1:
        return approval_md[start:].strip()
    return approval_md[start:end].strip()


def post_to_linkedin(post_text: str) -> dict:
    """Send post_text to the LinkedIn Share API. Returns the API response dict."""
    if requests is None:
        raise RuntimeError(
            "requests package not installed. Run: pip install requests"
        )

    access_token = os.getenv("LINKEDIN_ACCESS_TOKEN")
    author_urn = os.getenv("LINKEDIN_AUTHOR_URN")

    if not access_token:
        raise RuntimeError("LINKEDIN_ACCESS_TOKEN environment variable is not set.")
    if not author_urn:
        raise RuntimeError("LINKEDIN_AUTHOR_URN environment variable is not set.")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }

    payload = {
        "author": author_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": post_text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }

    response = requests.post(
        f"{_LINKEDIN_API_BASE}/ugcPosts",
        headers=headers,
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    return response.json() if response.text else {"status": "created"}


def process_approved() -> None:
    """Find all approved LinkedIn posts and publish them (or dry-run log them)."""
    approved_files = sorted(_APPROVED_DIR.glob("LINKEDIN_*.md"))
    if not approved_files:
        logger.debug("No approved LinkedIn posts found.")
        return

    dry_run = _is_dry_run()

    for approval_file in approved_files:
        content = approval_file.read_text(encoding="utf-8")
        post_text = _extract_post_text(content)

        if not post_text:
            logger.warning("Could not extract post text from %s — skipping.", approval_file.name)
            continue

        logger.info("Processing approved post: %s", approval_file.name)

        if dry_run:
            logger.info("[DRY RUN] Would post to LinkedIn:\n%s", post_text)
            _log_event(
                "DRY_RUN_POST",
                f"File: {approval_file.name}\n\n{post_text}",
            )
        else:
            try:
                result = post_to_linkedin(post_text)
                post_id = result.get("id", "unknown")
                logger.info("Posted to LinkedIn. Post ID: %s", post_id)
                _log_event(
                    "POST_PUBLISHED",
                    f"File: {approval_file.name}\nLinkedIn Post ID: {post_id}\n\n{post_text}",
                )
            except Exception as exc:
                logger.error("LinkedIn API error for %s: %s", approval_file.name, exc)
                _log_event(
                    "POST_FAILED",
                    f"File: {approval_file.name}\nError: {exc}\n\n{post_text}",
                )
                continue  # leave file in Approved/ so it can be retried

        # Move processed file to Done/ (create Done/ if needed)
        done_dir = _VAULT_ROOT / "Done"
        done_dir.mkdir(parents=True, exist_ok=True)
        approval_file.rename(done_dir / approval_file.name)
        logger.info("Moved %s → Done/", approval_file.name)


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------

def generate_and_queue(style_hint: str = "") -> None:
    """Full generate → save-pending flow. Called to create a new post for review."""
    _ensure_dirs()
    goals = _read_business_goals()
    post_text = generate_post(goals, style_hint=style_hint)
    save_pending(post_text)


def publish_approved() -> None:
    """Process all files sitting in Approved/. Called on a schedule or manually."""
    _ensure_dirs()
    process_approved()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    import argparse

    parser = argparse.ArgumentParser(
        description="LinkedIn auto-poster for the AI Employee Vault."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    gen_parser = subparsers.add_parser(
        "generate", help="Generate a new post and save it to Pending_Approval/."
    )
    gen_parser.add_argument(
        "--style",
        default="",
        metavar="HINT",
        help="Optional style guidance passed to Claude (e.g. 'be more concise').",
    )

    subparsers.add_parser(
        "publish", help="Publish all approved posts from Approved/."
    )

    subparsers.add_parser(
        "run",
        help=(
            "Continuous mode: generate a post (if none pending today), "
            "then poll Approved/ every CHECK_INTERVAL seconds."
        ),
    )

    args = parser.parse_args()

    if args.command == "generate":
        generate_and_queue(style_hint=args.style)

    elif args.command == "publish":
        publish_approved()

    elif args.command == "run":
        interval = int(os.getenv("CHECK_INTERVAL", "3600"))
        print(f"Running in continuous mode (interval={interval}s). Ctrl-C to stop.")
        # Generate once on startup if no pending file exists for today
        today_pattern = f"LINKEDIN_{_date_slug()}"
        has_pending = any(_PENDING_DIR.glob(f"{today_pattern}*.md"))
        if not has_pending:
            generate_and_queue()
        while True:
            publish_approved()
            time.sleep(interval)
