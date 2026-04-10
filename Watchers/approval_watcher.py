"""
ApprovalWatcher — event-driven monitor for Approved/ and Rejected/ folders.

Workflow:
  Human moves a file from Pending_Approval/ →
    Approved/  → parse action type → dispatch handler → log → Done/
    Rejected/  → log rejection → Done/
  Both paths update Dashboard.md when complete.

Action types dispatched (frontmatter `action` field):
  linkedin_post → LinkedIn API via linkedin_poster.post_to_linkedin()
  email_send    → Gmail API via gmail_auth.get_gmail_service()
  payment       → Placeholder (Gold tier)
  *             → Unknown action logged and moved to Done/

Environment variables (loaded from .env at vault root):
  DRY_RUN   — "true" → log without calling external APIs
"""

from __future__ import annotations

import base64
import logging
import re
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from watchdog.events import FileCreatedEvent, FileMovedEvent, FileSystemEventHandler
from watchdog.observers.polling import PollingObserver as Observer

from Watchers.base_watcher import BaseWatcher

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_VAULT_ROOT = Path(__file__).parent.parent

_APPROVED_DIR = _VAULT_ROOT / "Approved"
_REJECTED_DIR = _VAULT_ROOT / "Rejected"
_DONE_DIR = _VAULT_ROOT / "Done"
_LOGS_DIR = _VAULT_ROOT / "Logs"
_DASHBOARD = _VAULT_ROOT / "Dashboard.md"
_PENDING_DIR = _VAULT_ROOT / "Pending_Approval"


# ---------------------------------------------------------------------------
# Helpers — frontmatter parsing
# ---------------------------------------------------------------------------
_FM_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, body_text). Body is everything after the closing ---."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    return fm, text[m.end():]


def _extract_section(text: str, start_header: str, end_header: str | None = None) -> str:
    """Pull content between two markdown section headers (exclusive of headers)."""
    start_idx = text.find(start_header)
    if start_idx == -1:
        return ""
    start_idx += len(start_header)
    if end_header:
        end_idx = text.find(end_header, start_idx)
        if end_idx != -1:
            return text[start_idx:end_idx].strip()
    return text[start_idx:].strip()


# ---------------------------------------------------------------------------
# Helpers — Gmail raw message builder
# ---------------------------------------------------------------------------

def _build_raw_email(
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
) -> str:
    """Encode an RFC 2822 message to base64url for the Gmail API."""
    lines = [
        f"To: {to}",
        f"Subject: {subject}",
        "MIME-Version: 1.0",
        'Content-Type: text/plain; charset="UTF-8"',
        "Content-Transfer-Encoding: 7bit",
    ]
    if cc:
        lines.append(f"Cc: {cc}")
    if bcc:
        lines.append(f"Bcc: {bcc}")
    raw = "\r\n".join(lines) + "\r\n\r\n" + body
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode().rstrip("=")


# ---------------------------------------------------------------------------
# Dispatch handlers
# one per action type — each returns (success: bool, detail: str)
# ---------------------------------------------------------------------------

def _is_dry_run() -> bool:
    import os
    return os.getenv("DRY_RUN", "false").strip().lower() in ("1", "true", "yes")


def _dispatch_linkedin(
    filepath: Path,
    fm: dict[str, Any],
    body: str,
    logger: logging.Logger,
) -> tuple[bool, str]:
    """Post an approved LinkedIn draft via the LinkedIn API."""
    try:
        # linkedin_poster lives at the same level as this file
        from Watchers.linkedin_poster import _extract_post_text, post_to_linkedin, _log_event
    except ImportError as exc:
        return False, f"Cannot import linkedin_poster: {exc}"

    # The body still contains the full approval-file markdown (## Proposed Post, etc.)
    full_text = filepath.read_text(encoding="utf-8")
    post_text = _extract_post_text(full_text)

    if not post_text:
        return False, "Could not extract post text from approval file."

    if _is_dry_run():
        logger.info("[DRY RUN] Would post to LinkedIn:\n%s", post_text)
        _log_event("DRY_RUN_POST", f"ApprovalWatcher | File: {filepath.name}\n\n{post_text}")
        return True, "DRY RUN — LinkedIn post logged, not sent."

    try:
        result = post_to_linkedin(post_text)
        post_id = result.get("id", "unknown")
        _log_event(
            "POST_PUBLISHED",
            f"ApprovalWatcher | File: {filepath.name}\nLinkedIn Post ID: {post_id}\n\n{post_text}",
        )
        return True, f"LinkedIn post published (ID: {post_id})"
    except Exception as exc:
        return False, f"LinkedIn API error: {exc}"


def _dispatch_email_send(
    filepath: Path,
    fm: dict[str, Any],
    body: str,
    logger: logging.Logger,
) -> tuple[bool, str]:
    """Send an approved email draft via Gmail."""
    to = str(fm.get("to", "")).strip()
    subject = str(fm.get("subject", "(no subject)")).strip()

    if not to:
        return False, "Missing 'to' field in approval file frontmatter."

    # Extract email body — between '## Email Body' and '## To Approve'
    email_body = _extract_section(body, "## Email Body", "## To Approve")
    if not email_body:
        # Fallback: use everything in body before ## To Approve
        email_body = _extract_section(body, "", "## To Approve") or body.strip()

    cc = str(fm.get("cc", "")).strip() or None
    bcc = str(fm.get("bcc", "")).strip() or None

    if _is_dry_run():
        logger.info(
            "[DRY RUN] Would send email to=%s subject=%s\n%s",
            to, subject, email_body,
        )
        return True, f"DRY RUN — email to {to} logged, not sent."

    try:
        from Watchers.gmail_auth import get_gmail_service
        service = get_gmail_service()
        raw = _build_raw_email(to, subject, email_body, cc=cc, bcc=bcc)
        result = service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
        msg_id = result.get("id", "unknown")
        return True, f"Email sent (Gmail message ID: {msg_id}) to {to}"
    except Exception as exc:
        return False, f"Gmail send error: {exc}"


def _dispatch_payment(
    filepath: Path,
    fm: dict[str, Any],
    body: str,
    logger: logging.Logger,
) -> tuple[bool, str]:
    """Placeholder payment handler (Gold tier feature)."""
    amount = fm.get("amount", "unknown")
    recipient = fm.get("recipient", "unknown")
    logger.info(
        "Payment approval received — amount=%s recipient=%s — "
        "payment execution is a Gold tier feature (placeholder).",
        amount, recipient,
    )
    return True, (
        f"Payment approval logged (amount: {amount}, recipient: {recipient}). "
        "Payment execution is not yet implemented (Gold tier)."
    )


# Registry: action type → handler function
_DISPATCHERS: dict[str, Any] = {
    "linkedin_post": _dispatch_linkedin,
    "email_send": _dispatch_email_send,
    "payment": _dispatch_payment,
}


# ---------------------------------------------------------------------------
# Dashboard updater
# ---------------------------------------------------------------------------

class _DashboardUpdater:
    """Thread-safe targeted updater for Dashboard.md.

    Only touches three sections: Awaiting Approval, Recent Activity, Quick Stats.
    Full rewrites are left to the Claude update_dashboard skill.
    """

    def __init__(self, vault_path: Path):
        self._vault = vault_path
        self._lock = threading.Lock()
        self._dashboard = vault_path / "Dashboard.md"
        self._pending_dir = vault_path / "Pending_Approval"
        self._done_dir = vault_path / "Done"

    def record_event(self, description: str) -> None:
        """Prepend *description* to Recent Activity and refresh counts."""
        with self._lock:
            try:
                self._write(description)
            except Exception as exc:
                logging.getLogger("DashboardUpdater").warning(
                    "Dashboard update failed: %s", exc
                )

    def _write(self, event_line: str) -> None:
        if not self._dashboard.exists():
            return  # skip if dashboard hasn't been created yet

        text = self._dashboard.read_text(encoding="utf-8")
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y-%m-%d %H:%M")

        # --- Update frontmatter timestamp ---
        text = re.sub(
            r"(last_updated:\s*)[\d-]+",
            f"\\g<1>{now.strftime('%Y-%m-%d')}",
            text,
        )
        text = re.sub(
            r"(last_updated_time:\s*)[^\n]+",
            f"\\g<1>{now.strftime('%H:%M')} UTC",
            text,
        )

        # --- Recent Activity: prepend new entry ---
        activity_marker = "## Recent Activity"
        activity_entry = f"- `{ts}` — [ApprovalWatcher] {event_line}"
        if activity_marker in text:
            insert_at = text.index(activity_marker) + len(activity_marker)
            # skip blank line after header
            text = (
                text[:insert_at]
                + "\n"
                + activity_entry
                + "\n"
                + text[insert_at:].lstrip("\n")
            )

        # --- Awaiting Approval section: rebuild count line ---
        pending_files = list(self._pending_dir.glob("*.md")) if self._pending_dir.exists() else []
        n_pending = len(pending_files)

        if "## Awaiting Approval" in text:
            # Replace the entire section content (up to next ##)
            def rebuild_approval_section(m: re.Match) -> str:
                if n_pending == 0:
                    return m.group(0).split("\n")[0] + "\n_No items awaiting approval_\n\n"
                items = "\n".join(
                    f"- `{p.name}` — awaiting approval"
                    for p in sorted(pending_files)
                )
                return m.group(0).split("\n")[0] + "\n" + items + "\n\n"

            text = re.sub(
                r"## Awaiting Approval\n(?:(?!## ).*\n)*",
                rebuild_approval_section,
                text,
            )

        # --- Quick Stats: update Approvals Waiting count ---
        text = re.sub(
            r"(- Approvals Waiting:\s*)\d+",
            f"\\g<1>{n_pending}",
            text,
        )

        # Count Done/ files modified today for "Tasks Completed Today"
        done_today = 0
        if self._done_dir.exists():
            today = now.date()
            for f in self._done_dir.iterdir():
                mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).date()
                if mtime == today:
                    done_today += 1

        text = re.sub(
            r"(- Tasks Completed Today:\s*)\d+",
            f"\\g<1>{done_today}",
            text,
        )

        self._dashboard.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Watchdog event handler
# ---------------------------------------------------------------------------

class _ApprovalEventHandler(FileSystemEventHandler):
    """Handles file creation events in Approved/ or Rejected/."""

    def __init__(self, watcher: "ApprovalWatcher", folder_type: str):
        super().__init__()
        self._watcher = watcher
        self._folder_type = folder_type  # "approved" or "rejected"

    def _handle(self, path: Path) -> None:
        # Wait briefly for the file to be fully written/moved
        time.sleep(0.25)
        if not path.exists() or not path.is_file():
            return
        # Only process .md files
        if path.suffix.lower() != ".md":
            self._watcher.logger.debug("Skipping non-.md file: %s", path.name)
            return
        if self._folder_type == "approved":
            self._watcher.process_approved(path)
        else:
            self._watcher.process_rejected(path)

    def on_created(self, event: FileCreatedEvent) -> None:
        if not event.is_directory:
            self._handle(Path(event.src_path))

    def on_moved(self, event: FileMovedEvent) -> None:
        # Catches moves INTO a watched folder (PollingObserver may fire this)
        if not event.is_directory:
            self._handle(Path(event.dest_path))


# ---------------------------------------------------------------------------
# ApprovalWatcher
# ---------------------------------------------------------------------------

class ApprovalWatcher(BaseWatcher):
    """
    Monitors Approved/ and Rejected/ for new markdown files.

    Approved files:
      - Frontmatter `action` field routes to a dispatcher.
      - On success: moved to Done/ with `status: approved`.
      - On failure: left in Approved/ with an error logged (so retries are possible).

    Rejected files:
      - Logged and moved to Done/ with `status: rejected`.

    Both paths update Dashboard.md.
    """

    def __init__(self, vault_path: str, check_interval: int = 10):
        """
        Args:
            vault_path:      Absolute path to the vault root.
            check_interval:  Polling interval in seconds for watchdog PollingObserver.
                             10 s gives near-immediate response to approvals.
        """
        super().__init__(vault_path, check_interval)

        _APPROVED_DIR.mkdir(parents=True, exist_ok=True)
        _REJECTED_DIR.mkdir(parents=True, exist_ok=True)
        _DONE_DIR.mkdir(parents=True, exist_ok=True)
        _LOGS_DIR.mkdir(parents=True, exist_ok=True)

        self._dashboard_updater = _DashboardUpdater(self.vault_path)

        # Two observers — one per folder
        self._approved_handler = _ApprovalEventHandler(self, "approved")
        self._rejected_handler = _ApprovalEventHandler(self, "rejected")

        self._observer = Observer()  # single observer, two schedules
        self._observer.schedule(
            self._approved_handler, str(_APPROVED_DIR), recursive=False
        )
        self._observer.schedule(
            self._rejected_handler, str(_REJECTED_DIR), recursive=False
        )

    # ------------------------------------------------------------------
    # BaseWatcher ABC stubs (logic is event-driven via watchdog)
    # ------------------------------------------------------------------

    def check_for_updates(self):
        return None

    def create_action_file(self, data) -> None:
        pass

    # ------------------------------------------------------------------
    # Core processing
    # ------------------------------------------------------------------

    def process_approved(self, filepath: Path) -> None:
        """Dispatch an approved file and move it to Done/."""
        self.logger.info("Approved file detected: %s", filepath.name)

        try:
            text = filepath.read_text(encoding="utf-8")
        except OSError as exc:
            self.logger.error("Cannot read %s: %s", filepath.name, exc)
            return

        fm, body = _parse_frontmatter(text)
        action = str(fm.get("action", "")).strip().lower()

        if not action:
            self.logger.warning(
                "%s has no 'action' field — routing as unknown.", filepath.name
            )
            action = "unknown"

        handler = _DISPATCHERS.get(action)
        if handler is None:
            self.logger.warning(
                "No dispatcher for action='%s' in %s — moving to Done/ unexecuted.",
                action, filepath.name,
            )
            self._finalise(
                filepath, "approved",
                detail=f"No dispatcher for action '{action}' — logged, not executed.",
                success=False,
            )
            return

        self.logger.info(
            "Dispatching action='%s' for %s", action, filepath.name
        )
        try:
            success, detail = handler(filepath, fm, body, self.logger)
        except Exception as exc:
            self.logger.exception(
                "Unhandled error in dispatcher for %s: %s", filepath.name, exc
            )
            success, detail = False, f"Unhandled dispatcher error: {exc}"

        if success:
            self.logger.info("Dispatch succeeded: %s — %s", filepath.name, detail)
            self._log_to_file("APPROVED_EXECUTED", filepath.name, action, detail)
            self._finalise(filepath, "approved", detail=detail, success=True)
        else:
            self.logger.error(
                "Dispatch failed for %s: %s — leaving in Approved/ for retry.",
                filepath.name, detail,
            )
            self._log_to_file("DISPATCH_FAILED", filepath.name, action, detail)
            # Do NOT move — leave in Approved/ so the user can retry or investigate
            self._dashboard_updater.record_event(
                f"FAILED: {filepath.name} — {action} dispatch error: {detail}"
            )

    def process_rejected(self, filepath: Path) -> None:
        """Log a rejection and archive the file to Done/."""
        self.logger.info("Rejected file detected: %s", filepath.name)

        try:
            text = filepath.read_text(encoding="utf-8")
        except OSError as exc:
            self.logger.error("Cannot read rejected file %s: %s", filepath.name, exc)
            return

        fm, _ = _parse_frontmatter(text)
        action = str(fm.get("action", "unknown")).strip()

        self._log_to_file("REJECTED", filepath.name, action, "Human rejected this request.")
        self._finalise(filepath, "rejected", detail="Rejected by human.", success=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _finalise(
        self, filepath: Path, outcome: str, detail: str, success: bool
    ) -> None:
        """Rewrite status in frontmatter, move file to Done/, update Dashboard."""
        try:
            text = filepath.read_text(encoding="utf-8")
        except OSError:
            text = ""

        # Update status in frontmatter
        now_iso = datetime.now(timezone.utc).isoformat()
        if "status:" in text:
            text = re.sub(r"status:\s*\S+", f"status: {outcome}", text, count=1)
        # Append resolution metadata as a new section
        text = text.rstrip("\n") + (
            f"\n\n## Resolution\n"
            f"- **Outcome**: {outcome}\n"
            f"- **Resolved**: {now_iso}\n"
            f"- **Detail**: {detail}\n"
        )

        dest = _DONE_DIR / filepath.name
        # Avoid overwriting an existing Done/ file
        if dest.exists():
            stem = filepath.stem
            suffix = filepath.suffix
            counter = 1
            while dest.exists():
                dest = _DONE_DIR / f"{stem}_{counter:02d}{suffix}"
                counter += 1

        try:
            dest.write_text(text, encoding="utf-8")
            filepath.unlink()
            self.logger.info("Moved %s → Done/%s", filepath.name, dest.name)
        except OSError as exc:
            self.logger.error(
                "Could not move %s to Done/: %s", filepath.name, exc
            )
            return

        dashboard_msg = f"{outcome.upper()}: {filepath.name} — {detail}"
        self._dashboard_updater.record_event(dashboard_msg)

    def _log_to_file(
        self, label: str, filename: str, action: str, detail: str
    ) -> None:
        """Append a structured log entry to Logs/approval_watcher.log."""
        log_file = _LOGS_DIR / "approval_watcher.log"
        ts = datetime.now(timezone.utc).isoformat()
        entry = (
            f"\n[{ts}] {label}\n"
            f"  file   : {filename}\n"
            f"  action : {action}\n"
            f"  detail : {detail}\n"
            f"{'—' * 60}\n"
        )
        try:
            with log_file.open("a", encoding="utf-8") as fh:
                fh.write(entry)
        except OSError as exc:
            self.logger.warning("Could not write to approval log: %s", exc)

    # ------------------------------------------------------------------
    # Startup scan — process files already present before watcher started
    # ------------------------------------------------------------------

    def _startup_scan(self) -> None:
        """Process any files already sitting in Approved/ or Rejected/ at launch."""
        approved = list(_APPROVED_DIR.glob("*.md"))
        rejected = list(_REJECTED_DIR.glob("*.md"))

        if approved or rejected:
            self.logger.info(
                "Startup scan: found %d approved, %d rejected files to process.",
                len(approved), len(rejected),
            )
        for f in sorted(approved):
            self.process_approved(f)
        for f in sorted(rejected):
            self.process_rejected(f)

    # ------------------------------------------------------------------
    # run() — override to use watchdog Observer
    # ------------------------------------------------------------------

    def run(self) -> None:
        self.logger.info(
            "ApprovalWatcher started — watching %s and %s (poll interval %ss)",
            _APPROVED_DIR, _REJECTED_DIR, self.check_interval,
        )
        print(
            f"\n[ApprovalWatcher] Monitoring started.\n"
            f"  Approved/  : {_APPROVED_DIR}\n"
            f"  Rejected/  : {_REJECTED_DIR}\n"
            f"  Done/      : {_DONE_DIR}\n"
            f"  DRY_RUN    : {_is_dry_run()}\n"
        )

        self._startup_scan()

        self._observer.start()
        try:
            while True:
                time.sleep(self.check_interval)
        except KeyboardInterrupt:
            self.logger.info("Shutdown requested — stopping ApprovalWatcher.")
        finally:
            self._observer.stop()
            self._observer.join()
            self.logger.info("ApprovalWatcher stopped.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os

    try:
        from dotenv import load_dotenv
        load_dotenv(_VAULT_ROOT / ".env")
    except ImportError:
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    print("\n" + "=" * 60)
    print("Approval Watcher")
    print("=" * 60)
    print(f"Vault Path   : {_VAULT_ROOT}")
    print(f"Poll Interval: 10 seconds")
    print(f"DRY_RUN      : {_is_dry_run()}")
    print("Move files to Approved/ or Rejected/ to trigger processing.")
    print("Press Ctrl+C to stop.")
    print("=" * 60 + "\n")

    watcher = ApprovalWatcher(vault_path=str(_VAULT_ROOT), check_interval=10)
    watcher.run()
