"""
GmailWatcher — polls Gmail for new unread important emails and creates
action files in Needs_Action/ for each one.

Imports get_gmail_service() from gmail_auth.py, so OAuth2 credentials
and token management are handled there.
"""

import json
import logging
import time
from base64 import urlsafe_b64decode
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from googleapiclient.errors import HttpError

from Watchers.base_watcher import BaseWatcher
from Watchers.gmail_auth import get_gmail_service

# ---------------------------------------------------------------------------
# Retry / backoff constants
# ---------------------------------------------------------------------------
_MAX_RETRIES = 5
_BACKOFF_BASE = 2          # seconds; actual wait = _BACKOFF_BASE ** attempt
_RETRYABLE_CODES = {429, 500, 502, 503, 504}

# Gmail search query — CATEGORY_PERSONAL keeps it focused on real mail
_GMAIL_QUERY = "is:unread is:important newer_than:1d"


class GmailWatcher(BaseWatcher):
    """
    Polls Gmail every *check_interval* seconds for new unread important emails.

    For each unseen message a YAML-frontmatter .md file is written into
    Needs_Action/ and the message ID is recorded in a JSON sidecar so
    the same email is never processed twice.
    """

    def __init__(self, vault_path: str, check_interval: int = 120):
        super().__init__(vault_path, check_interval)

        # Processed-IDs store lives at vault root so all watchers can share it
        self._seen_path: Path = self.vault_path / ".gmail_seen_ids.json"
        self._seen_ids: set[str] = self._load_seen_ids()

        # Gmail service — lazily initialised on first use so the constructor
        # does not block on OAuth during import.
        self._service = None

    # ------------------------------------------------------------------
    # Seen-ID persistence
    # ------------------------------------------------------------------

    def _load_seen_ids(self) -> set[str]:
        if self._seen_path.exists():
            try:
                data = json.loads(self._seen_path.read_text(encoding="utf-8"))
                return set(data)
            except (json.JSONDecodeError, TypeError):
                self.logger.warning(
                    "Could not parse %s — starting with empty seen-ID set.",
                    self._seen_path,
                )
        return set()

    def _save_seen_ids(self) -> None:
        self._seen_path.write_text(
            json.dumps(sorted(self._seen_ids), indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Service accessor (lazy init + re-auth on invalid grant)
    # ------------------------------------------------------------------

    def _get_service(self):
        if self._service is None:
            self._service = get_gmail_service()
        return self._service

    # ------------------------------------------------------------------
    # Retry helper with exponential backoff
    # ------------------------------------------------------------------

    def _call_with_backoff(self, fn, *args, **kwargs):
        """Call *fn* with retries and exponential backoff on transient errors."""
        last_exc = None
        for attempt in range(_MAX_RETRIES):
            try:
                return fn(*args, **kwargs)
            except HttpError as exc:
                status = int(exc.resp.status)
                if status not in _RETRYABLE_CODES:
                    raise
                wait = _BACKOFF_BASE ** attempt
                self.logger.warning(
                    "Gmail API HTTP %s (attempt %d/%d) — retrying in %ss",
                    status, attempt + 1, _MAX_RETRIES, wait,
                )
                last_exc = exc
                time.sleep(wait)
            except Exception as exc:
                wait = _BACKOFF_BASE ** attempt
                self.logger.warning(
                    "Transient error (attempt %d/%d): %s — retrying in %ss",
                    attempt + 1, _MAX_RETRIES, exc, wait,
                )
                last_exc = exc
                time.sleep(wait)
        raise last_exc  # exhausted retries

    # ------------------------------------------------------------------
    # Email parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_body(part: dict) -> str:
        """Decode a message part body from base64url."""
        data = part.get("body", {}).get("data", "")
        if not data:
            return ""
        try:
            return urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        except Exception:
            return ""

    def _extract_text(self, payload: dict) -> str:
        """Recursively extract plain-text from a message payload."""
        mime = payload.get("mimeType", "")
        if mime == "text/plain":
            return self._decode_body(payload)
        if mime.startswith("multipart/"):
            for part in payload.get("parts", []):
                text = self._extract_text(part)
                if text:
                    return text
        return ""

    @staticmethod
    def _header(headers: list[dict], name: str) -> str:
        name_lower = name.lower()
        for h in headers:
            if h.get("name", "").lower() == name_lower:
                return h.get("value", "")
        return ""

    def _parse_date(self, raw_date: str) -> str:
        """Return an ISO-8601 UTC string from an RFC-2822 Date header."""
        if not raw_date:
            return datetime.now(timezone.utc).isoformat()
        try:
            dt = parsedate_to_datetime(raw_date)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # BaseWatcher abstract-method implementations
    # ------------------------------------------------------------------

    def check_for_updates(self) -> list[dict]:
        """Query Gmail and return a list of new message dicts."""
        service = self._get_service()
        new_messages = []

        try:
            response = self._call_with_backoff(
                service.users().messages().list,
                userId="me",
                q=_GMAIL_QUERY,
                maxResults=50,
            )
            result = response.execute()
        except HttpError as exc:
            self.logger.error("Gmail list error: %s", exc)
            return []
        except Exception as exc:
            self.logger.error("Unexpected error listing Gmail messages: %s", exc)
            return []

        messages = result.get("messages", [])
        if not messages:
            self.logger.debug("No new important unread emails.")
            return []

        for stub in messages:
            msg_id = stub["id"]
            if msg_id in self._seen_ids:
                continue

            try:
                full = self._call_with_backoff(
                    service.users().messages().get,
                    userId="me",
                    id=msg_id,
                    format="full",
                )
                msg = full.execute()
            except Exception as exc:
                self.logger.error("Could not fetch message %s: %s", msg_id, exc)
                continue

            payload = msg.get("payload", {})
            headers = payload.get("headers", [])

            from_raw = self._header(headers, "From")
            _, from_addr = parseaddr(from_raw)
            subject = self._header(headers, "Subject") or "(no subject)"
            date_raw = self._header(headers, "Date")
            received = self._parse_date(date_raw)

            body = self._extract_text(payload)
            snippet = msg.get("snippet", "")
            content = body.strip() if body.strip() else snippet

            new_messages.append(
                {
                    "id": msg_id,
                    "from": from_addr or from_raw,
                    "subject": subject,
                    "received": received,
                    "content": content,
                }
            )

        return new_messages

    def create_action_file(self, data: list[dict]) -> None:
        """Write a Needs_Action .md file for each email in *data*."""
        for email in data:
            msg_id = email["id"]
            safe_subject = "".join(
                c if c.isalnum() or c in " -_" else "_" for c in email["subject"]
            )[:60].strip()
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            filename = f"EMAIL_{timestamp}_{safe_subject}.md"
            dest = self.needs_action / filename

            content = (
                "---\n"
                "type: email\n"
                f"from: {email['from']}\n"
                f"subject: {email['subject']}\n"
                f"received: {email['received']}\n"
                "priority: high\n"
                "status: pending\n"
                f"message_id: {msg_id}\n"
                "---\n"
                "\n"
                "## Email Content\n"
                "\n"
                f"{email['content']}\n"
                "\n"
                "## Suggested Actions\n"
                "- [ ] Reply to sender\n"
                "- [ ] Forward to relevant party\n"
                "- [ ] Archive after processing\n"
            )

            dest.write_text(content, encoding="utf-8")

            self._seen_ids.add(msg_id)
            self._save_seen_ids()

            self.logger.info(
                "New email queued → %s  (from=%s, subject=%s)",
                filename,
                email["from"],
                email["subject"],
            )

if __name__ == "__main__":
    import sys
    
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    
    # Get vault path (parent of Watchers directory)
    vault_path = str(Path(__file__).parent.parent)
    
    # Create and start the watcher
    watcher = GmailWatcher(vault_path=vault_path, check_interval=120)
    
    print("\n" + "="*60)
    print("Gmail Watcher Started")
    print("="*60)
    print(f"Vault Path: {vault_path}")
    print(f"Check Interval: 120 seconds (2 minutes)")
    print(f"Monitoring: Unread important emails")
    print("Press Ctrl+C to stop")
    print("="*60 + "\n")
    
    try:
        watcher.run()
    except KeyboardInterrupt:
        print("\n\nStopping Gmail Watcher...")
        watcher.stop()
        print("Gmail Watcher stopped.")