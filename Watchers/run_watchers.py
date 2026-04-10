"""
run_watchers.py — start all vault watchers in parallel daemon threads.

Watchers launched:
  FilesystemWatcher  — event-driven, watches Inbox/ for new file drops
  GmailWatcher       — polls Gmail API every 2 minutes for important email
  ApprovalWatcher    — event-driven, watches Approved/ and Rejected/ folders

Usage:
    python Watchers/run_watchers.py
    python Watchers/run_watchers.py --no-gmail    # skip Gmail watcher
    python Watchers/run_watchers.py --dry-run     # log what would happen, no API calls

Logs:
    Logs/watcher_YYYY-MM-DD.log  (stdout + file, rotated daily)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Resolve paths and patch sys.path before any vault imports
# ---------------------------------------------------------------------------
WATCHERS_DIR = Path(__file__).resolve().parent
VAULT_ROOT   = WATCHERS_DIR.parent

if str(VAULT_ROOT) not in sys.path:
    sys.path.insert(0, str(VAULT_ROOT))

# ---------------------------------------------------------------------------
# Load .env from vault root
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
    _ENV_LOADED = load_dotenv(VAULT_ROOT / ".env")
except ImportError:
    _ENV_LOADED = False

ENV_FILE = VAULT_ROOT / ".env"

# ---------------------------------------------------------------------------
# Logging — console + daily rotating file at Logs/watcher_YYYY-MM-DD.log
# ---------------------------------------------------------------------------
LOGS_DIR = VAULT_ROOT / "Logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

_today        = datetime.now().strftime("%Y-%m-%d")
LOG_FILE      = LOGS_DIR / f"watcher_{_today}.log"
LOG_FORMAT    = "%(asctime)s  %(levelname)-8s  %(name)-22s  %(message)s"
LOG_DATE_FMT  = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt=LOG_DATE_FMT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)

logger = logging.getLogger("run_watchers")

# ---------------------------------------------------------------------------
# Watcher imports — deferred so logging is configured first
# ---------------------------------------------------------------------------
from Watchers.filesystem_watcher import FilesystemWatcher  # noqa: E402
from Watchers.approval_watcher   import ApprovalWatcher    # noqa: E402


def _try_import_twitter() -> tuple[type | None, str]:
    """Return (TwitterWatcher class, skip_reason)."""
    try:
        from Watchers.twitter_watcher import TwitterWatcher
    except ImportError as exc:
        return None, f"import failed — {exc}"

    token_file = VAULT_ROOT / ".twitter_token.json"
    bearer     = os.getenv("TWITTER_BEARER_TOKEN", "")

    if not token_file.exists() and not bearer:
        return None, (
            ".twitter_token.json not found and TWITTER_BEARER_TOKEN not set.\n"
            "      Run: python Watchers/twitter_auth.py"
        )
    if not os.getenv("TWITTER_USER_ID", ""):
        return None, "TWITTER_USER_ID not set in .env — skipping Twitter watcher"

    return TwitterWatcher, ""


def _try_import_social() -> tuple[type | None, str]:
    """Return (SocialMediaWatcher class, skip_reason)."""
    try:
        from Watchers.social_media_watcher import SocialMediaWatcher
    except ImportError as exc:
        return None, f"import failed — {exc}"

    token   = os.getenv("META_PAGE_ACCESS_TOKEN", "")
    page_id = os.getenv("META_PAGE_ID", "")

    if not token:
        return None, "META_PAGE_ACCESS_TOKEN not set in .env — skipping social watcher"
    if not page_id:
        return None, "META_PAGE_ID not set in .env — skipping social watcher"

    return SocialMediaWatcher, ""


def _try_import_gmail() -> tuple[type | None, str]:
    """Return (GmailWatcher class, reason_string).

    reason_string is empty on success, or a human-readable skip reason.
    """
    try:
        from Watchers.gmail_watcher import GmailWatcher
    except ImportError as exc:
        return None, f"import failed — {exc}"

    creds = VAULT_ROOT / "credentials.json"
    token = VAULT_ROOT / "token.json"

    if not creds.exists():
        return None, f"credentials.json not found at {creds}"
    if not token.exists():
        return None, (
            f"token.json not found — run the OAuth flow first:\n"
            f"      python -c \"from Watchers.gmail_auth import get_gmail_service; "
            f"get_gmail_service()\""
        )

    return GmailWatcher, ""


# ---------------------------------------------------------------------------
# WatcherSpec — metadata for each watcher instance
# ---------------------------------------------------------------------------
@dataclass
class WatcherSpec:
    """Holds a watcher instance, its thread, and display metadata."""

    watcher:       object
    label:         str          # display name for banner
    monitors:      list[str]    # human-readable list of what it watches
    interval_desc: str          # e.g. "event-driven" or "every 2 min"
    thread:        Optional[threading.Thread] = field(default=None, repr=False)
    skip_reason:   str = ""     # non-empty → watcher was not started

    @property
    def active(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    @property
    def skipped(self) -> bool:
        return bool(self.skip_reason)


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
_BOX_W = 62  # inner width (between the │ characters)


def _line(text: str = "", pad: int = 2) -> str:
    """Return a box row, truncating content that exceeds the inner width."""
    content = " " * pad + text
    if len(content) > _BOX_W:
        content = content[: _BOX_W - 1] + "…"
    return f"│{content:<{_BOX_W}}│"


def _divider(left: str = "├", right: str = "┤", fill: str = "─") -> str:
    return left + fill * _BOX_W + right


def print_banner(
    specs:    list[WatcherSpec],
    dry_run:  bool,
    env_ok:   bool,
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d  %H:%M:%S UTC")

    # Shorten vault root for display (keep last 3 path segments if long)
    vault_str = str(VAULT_ROOT)
    if len(vault_str) > 44:
        parts = Path(vault_str).parts
        vault_str = "…/" + "/".join(parts[-3:])

    log_name  = LOG_FILE.name   # e.g. watcher_2026-04-08.log
    env_state = "loaded" if env_ok else "not found"

    print()
    print("┌" + "─" * _BOX_W + "┐")
    title = "AI Employee Vault — Watcher Runner"
    print(_line(title.center(_BOX_W - 2)))
    print(_line(f"Started: {now}".center(_BOX_W - 2)))
    print(_divider())

    # --- Active watchers ---
    print(_line("WATCHERS"))
    print(_line())

    for spec in specs:
        if spec.skipped:
            print(_line(f"  ✗  SKIPPED      {spec.label}"))
            for rl in spec.skip_reason.split("\n"):
                print(_line(f"                   ↳ {rl}"))
        else:
            print(_line(f"  ✓  {spec.label:<22}  {spec.interval_desc}"))
            for m in spec.monitors:
                print(_line(f"                   ↳ {m}"))
        print(_line())

    print(_divider())

    # --- Configuration ---
    print(_line("CONFIGURATION"))
    print(_line())
    print(_line(f"  Vault root : {vault_str}"))
    print(_line(f"  Env file   : .env  ({env_state})"))
    print(_line(f"  Log file   : Logs/{log_name}"))
    dry_label = "true  (no external API calls)" if dry_run else "false"
    print(_line(f"  DRY_RUN    : {dry_label}"))
    print(_line())
    print(_divider())
    print(_line("  Press Ctrl+C to stop all watchers"))
    print("└" + "─" * _BOX_W + "┘")
    print()


# ---------------------------------------------------------------------------
# Health monitor — background thread that checks watcher liveness
# ---------------------------------------------------------------------------

def _health_monitor(specs: list[WatcherSpec], stop_event: threading.Event) -> None:
    """Log a warning if any watcher thread has unexpectedly died."""
    check_interval = 60  # seconds between health checks

    while not stop_event.wait(timeout=check_interval):
        for spec in specs:
            if spec.skipped:
                continue
            if spec.thread and not spec.thread.is_alive():
                logger.error(
                    "Watcher thread DIED unexpectedly: %s — "
                    "restart the runner to recover.",
                    spec.label,
                )
            else:
                logger.debug("Health check OK: %s", spec.label)


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------

def _shutdown(specs: list[WatcherSpec], stop_event: threading.Event) -> None:
    """Signal all watchers to stop and wait for their threads to exit."""
    stop_event.set()  # tells health monitor to exit
    logger.info("Shutdown signal sent — stopping watchers…")
    print()

    # Stop watchdog-Observer-based watchers first
    for spec in specs:
        if spec.skipped or spec.thread is None:
            continue
        observer = getattr(spec.watcher, "_observer", None)
        if observer is not None:
            try:
                observer.stop()
                logger.info("Observer stopped: %s", spec.label)
            except Exception as exc:
                logger.warning("Could not stop observer for %s: %s", spec.label, exc)

    # Join all threads with a short timeout each
    for spec in specs:
        if spec.thread is None or spec.skipped:
            continue
        spec.thread.join(timeout=3)
        status = "exited cleanly" if not spec.thread.is_alive() else "still running (daemon — will die on exit)"
        logger.info("%-22s  %s", spec.label, status)

    logger.info("Shutdown complete.")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI Employee Vault — run all watchers."
    )
    parser.add_argument(
        "--no-gmail",
        action="store_true",
        help="Skip the Gmail watcher (use if OAuth credentials are not set up).",
    )
    parser.add_argument(
        "--no-social",
        action="store_true",
        help="Skip the Social Media watcher (use if Meta tokens are not set up).",
    )
    parser.add_argument(
        "--no-twitter",
        action="store_true",
        help="Skip the Twitter watcher (use if Twitter tokens are not set up).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Set DRY_RUN=true for all watchers (log without calling external APIs).",
    )
    args = parser.parse_args()

    if args.dry_run:
        os.environ["DRY_RUN"] = "true"

    dry_run = os.getenv("DRY_RUN", "false").strip().lower() in ("1", "true", "yes")

    # -----------------------------------------------------------------------
    # Build watcher specs (attempt to construct each; record skips)
    # -----------------------------------------------------------------------
    specs: list[WatcherSpec] = []

    # 1. FilesystemWatcher
    specs.append(WatcherSpec(
        watcher=FilesystemWatcher(vault_path=str(VAULT_ROOT), check_interval=60),
        label="FilesystemWatcher",
        monitors=["Inbox/  (new file drops → Needs_Action/)"],
        interval_desc="event-driven",
    ))

    # 2. GmailWatcher (optional — skip if credentials are missing)
    if args.no_gmail:
        specs.append(WatcherSpec(
            watcher=None,
            label="GmailWatcher",
            monitors=[],
            interval_desc="",
            skip_reason="--no-gmail flag passed",
        ))
    else:
        GmailWatcher, skip_reason = _try_import_gmail()
        if GmailWatcher is None:
            specs.append(WatcherSpec(
                watcher=None,
                label="GmailWatcher",
                monitors=[],
                interval_desc="",
                skip_reason=skip_reason,
            ))
        else:
            specs.append(WatcherSpec(
                watcher=GmailWatcher(vault_path=str(VAULT_ROOT), check_interval=120),
                label="GmailWatcher",
                monitors=["Gmail API  (is:unread is:important newer_than:1d)"],
                interval_desc="every 2 min",
            ))

    # 3. SocialMediaWatcher (optional)
    social_interval = int(os.getenv("SOCIAL_CHECK_INTERVAL", "300"))
    if args.no_social:
        specs.append(WatcherSpec(
            watcher=None,
            label="SocialMediaWatcher",
            monitors=[],
            interval_desc="",
            skip_reason="--no-social flag passed",
        ))
    else:
        SocialMediaWatcher, social_skip = _try_import_social()
        if SocialMediaWatcher is None:
            specs.append(WatcherSpec(
                watcher=None,
                label="SocialMediaWatcher",
                monitors=[],
                interval_desc="",
                skip_reason=social_skip,
            ))
        else:
            specs.append(WatcherSpec(
                watcher=SocialMediaWatcher(vault_path=str(VAULT_ROOT), check_interval=social_interval),
                label="SocialMediaWatcher",
                monitors=[
                    "Meta Graph API  (FB messages, IG DMs, comments, mentions)",
                    f"Daily summary at {os.getenv('SOCIAL_SUMMARY_HOUR', '8')}:00 UTC",
                ],
                interval_desc=f"every {social_interval}s",
            ))

    # 4. TwitterWatcher (optional)
    twitter_interval = int(os.getenv("TWITTER_CHECK_INTERVAL", "300"))
    if args.no_twitter:
        specs.append(WatcherSpec(
            watcher=None,
            label="TwitterWatcher",
            monitors=[],
            interval_desc="",
            skip_reason="--no-twitter flag passed",
        ))
    else:
        TwitterWatcher, twitter_skip = _try_import_twitter()
        if TwitterWatcher is None:
            specs.append(WatcherSpec(
                watcher=None,
                label="TwitterWatcher",
                monitors=[],
                interval_desc="",
                skip_reason=twitter_skip,
            ))
        else:
            specs.append(WatcherSpec(
                watcher=TwitterWatcher(vault_path=str(VAULT_ROOT), check_interval=twitter_interval),
                label="TwitterWatcher",
                monitors=[
                    "Twitter/X API v2  (mentions, DMs)",
                    f"Daily summary at {os.getenv('TWITTER_SUMMARY_HOUR', '8')}:00 UTC",
                ],
                interval_desc=f"every {twitter_interval}s",
            ))

    # 5. ApprovalWatcher
    specs.append(WatcherSpec(
        watcher=ApprovalWatcher(vault_path=str(VAULT_ROOT), check_interval=10),
        label="ApprovalWatcher",
        monitors=[
            "Approved/  (dispatch → LinkedIn / Gmail / Facebook / Instagram / Twitter / payment)",
            "Rejected/  (log & archive)",
        ],
        interval_desc="event-driven",
    ))

    # -----------------------------------------------------------------------
    # Print startup banner BEFORE starting threads so output isn't interleaved
    # -----------------------------------------------------------------------
    print_banner(specs, dry_run=dry_run, env_ok=_ENV_LOADED)

    # -----------------------------------------------------------------------
    # Start threads for non-skipped watchers
    # -----------------------------------------------------------------------
    for spec in specs:
        if spec.skipped:
            continue
        t = threading.Thread(
            target=spec.watcher.run,
            name=spec.label,
            daemon=True,
        )
        t.start()
        spec.thread = t
        logger.info("Started: %-22s  [thread=%s]", spec.label, t.name)

    # -----------------------------------------------------------------------
    # Start health monitor
    # -----------------------------------------------------------------------
    stop_event = threading.Event()
    health_thread = threading.Thread(
        target=_health_monitor,
        args=(specs, stop_event),
        name="HealthMonitor",
        daemon=True,
    )
    health_thread.start()

    # -----------------------------------------------------------------------
    # Block main thread — join with short timeout so Ctrl+C is responsive
    # -----------------------------------------------------------------------
    active_threads = [spec.thread for spec in specs if spec.thread is not None]
    try:
        while True:
            for t in active_threads:
                t.join(timeout=1)
    except KeyboardInterrupt:
        _shutdown(specs, stop_event)


if __name__ == "__main__":
    main()
