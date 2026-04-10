"""
retry_handler.py — Retry logic with exponential backoff for the AI Employee.

Error categories and their handling:
  TRANSIENT  — network errors, rate limits: retry up to 3× with backoff
  AUTH       — authentication failures: alert human, pause operations
  LOGIC      — unexpected code errors: queue for human review
  DATA       — malformed/corrupt data: quarantine file + alert

Usage:
    from scripts.retry_handler import with_retry, ErrorCategory, RetryExhausted

    @with_retry(category=ErrorCategory.TRANSIENT)
    def call_gmail_api(): ...

    # or inline:
    result = with_retry(fn=my_fn, category=ErrorCategory.TRANSIENT)
"""

from __future__ import annotations

import functools
import json
import logging
import shutil
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
VAULT_ROOT   = _SCRIPTS_DIR.parent
LOGS_DIR     = VAULT_ROOT / "Logs"
QUARANTINE   = VAULT_ROOT / "Quarantine"
REVIEW_QUEUE = VAULT_ROOT / "Review_Queue"

LOGS_DIR.mkdir(parents=True, exist_ok=True)
QUARANTINE.mkdir(parents=True, exist_ok=True)
REVIEW_QUEUE.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("retry_handler")

# ---------------------------------------------------------------------------
# Error categories
# ---------------------------------------------------------------------------

class ErrorCategory(str, Enum):
    TRANSIENT = "transient"   # network, rate limits → retry
    AUTH      = "auth"        # credentials expired → alert + pause
    LOGIC     = "logic"       # code bugs → queue for review
    DATA      = "data"        # bad data → quarantine + alert


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class RetryExhausted(Exception):
    """Raised when all retry attempts are consumed for a TRANSIENT error."""
    def __init__(self, operation: str, attempts: int, last_exc: Exception):
        super().__init__(
            f"{operation!r} failed after {attempts} attempts: {last_exc}"
        )
        self.operation  = operation
        self.attempts   = attempts
        self.last_exc   = last_exc


class AuthError(Exception):
    """Raised for authentication/authorization failures."""


class LogicError(Exception):
    """Raised for unexpected logic/code errors."""


class DataError(Exception):
    """Raised for malformed or corrupt data."""


# ---------------------------------------------------------------------------
# Alert helpers
# ---------------------------------------------------------------------------

def _write_alert(kind: str, message: str, context: dict | None = None) -> None:
    """Append a structured alert to Logs/alerts.json."""
    alert_file = LOGS_DIR / "alerts.json"
    alerts: list[dict] = []
    if alert_file.exists():
        try:
            alerts = json.loads(alert_file.read_text())
        except json.JSONDecodeError:
            alerts = []

    alerts.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "kind":      kind,
        "message":   message,
        "context":   context or {},
    })

    alert_file.write_text(json.dumps(alerts, indent=2))
    logger.warning("[ALERT:%s] %s", kind, message)


def _quarantine_file(path: Path, reason: str) -> Path:
    """Move a file to Quarantine/ and write a sidecar .reason file."""
    ts   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = QUARANTINE / f"{ts}_{path.name}"
    shutil.move(str(path), dest)
    dest.with_suffix(dest.suffix + ".reason").write_text(
        f"Quarantined: {datetime.now(timezone.utc).isoformat()}\n"
        f"Reason: {reason}\n"
        f"Original path: {path}\n"
    )
    logger.warning("Quarantined %s → %s  (%s)", path.name, dest, reason)
    return dest


def _queue_for_review(operation: str, exc: Exception, context: dict | None = None) -> None:
    """Write a review-request file to Review_Queue/."""
    ts       = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_file = REVIEW_QUEUE / f"{ts}_{operation[:40]}.json"
    out_file.write_text(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "operation": operation,
        "error":     str(exc),
        "error_type": type(exc).__name__,
        "context":   context or {},
    }, indent=2))
    logger.error("Queued for review: %s → %s", operation, out_file.name)


# ---------------------------------------------------------------------------
# Core retry logic
# ---------------------------------------------------------------------------

_T = TypeVar("_T")

_BACKOFF_BASE = 2.0   # seconds; delay = base^attempt  (2, 4, 8 …)
_MAX_ATTEMPTS = 3


def call_with_retry(
    fn:           Callable[..., _T],
    *args:        Any,
    category:     ErrorCategory = ErrorCategory.TRANSIENT,
    operation:    str           = "",
    max_attempts: int           = _MAX_ATTEMPTS,
    quarantine_path: Optional[Path] = None,
    context:      dict | None   = None,
    **kwargs:     Any,
) -> _T:
    """
    Call *fn* with retry semantics dictated by *category*.

    Parameters
    ----------
    fn              — the callable to invoke
    category        — governs retry/alert/quarantine behavior
    operation       — human-readable name for logging / alerts
    max_attempts    — max tries for TRANSIENT errors (default 3)
    quarantine_path — file to quarantine on DATA errors
    context         — extra info saved to alerts / review queue
    """
    op = operation or getattr(fn, "__name__", str(fn))

    if category == ErrorCategory.TRANSIENT:
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                if attempt < max_attempts:
                    delay = _BACKOFF_BASE ** attempt
                    logger.warning(
                        "[TRANSIENT] %s failed (attempt %d/%d): %s — retrying in %.0fs",
                        op, attempt, max_attempts, exc, delay,
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        "[TRANSIENT] %s exhausted %d attempts: %s",
                        op, max_attempts, exc,
                    )
        _write_alert("transient_exhausted", f"{op} failed after {max_attempts} retries", context)
        raise RetryExhausted(op, max_attempts, last_exc)  # type: ignore[arg-type]

    elif category == ErrorCategory.AUTH:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            _write_alert("auth_failure", f"Auth error in {op}: {exc}", context)
            logger.critical("[AUTH] %s: %s — operations paused, human review required", op, exc)
            raise AuthError(f"Auth failure in {op}: {exc}") from exc

    elif category == ErrorCategory.LOGIC:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            _queue_for_review(op, exc, context)
            _write_alert("logic_error", f"Logic error in {op}: {exc}", context)
            raise LogicError(f"Logic error in {op}: {exc}") from exc

    elif category == ErrorCategory.DATA:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if quarantine_path and quarantine_path.exists():
                _quarantine_file(quarantine_path, str(exc))
            _write_alert("data_error", f"Data error in {op}: {exc}", context)
            raise DataError(f"Data error in {op}: {exc}") from exc

    else:
        return fn(*args, **kwargs)


# ---------------------------------------------------------------------------
# Decorator form
# ---------------------------------------------------------------------------

def with_retry(
    category:     ErrorCategory = ErrorCategory.TRANSIENT,
    operation:    str           = "",
    max_attempts: int           = _MAX_ATTEMPTS,
    context:      dict | None   = None,
) -> Callable:
    """
    Decorator that wraps a function with call_with_retry semantics.

    Example
    -------
    @with_retry(category=ErrorCategory.TRANSIENT, max_attempts=3)
    def send_email(payload): ...
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return call_with_retry(
                fn, *args,
                category=category,
                operation=operation or fn.__name__,
                max_attempts=max_attempts,
                context=context,
                **kwargs,
            )
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Graceful-degradation helpers
# ---------------------------------------------------------------------------

def queue_email_locally(to: str, subject: str, body: str) -> Path:
    """Persist an outgoing email to Logs/email_queue/ when Gmail is down."""
    queue_dir = LOGS_DIR / "email_queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    ts   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    out  = queue_dir / f"queued_{ts}.json"
    out.write_text(json.dumps({
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "to": to, "subject": subject, "body": body,
    }, indent=2))
    logger.info("Email queued locally: %s", out.name)
    return out


def log_odoo_transaction(payload: dict) -> Path:
    """Log an Odoo transaction to markdown when Odoo is unavailable."""
    queue_dir = LOGS_DIR / "odoo_queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    ts  = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    out = queue_dir / f"odoo_{ts}.md"
    lines = [
        f"# Odoo Transaction — {datetime.now(timezone.utc).isoformat()}",
        "",
        "**Status:** queued (Odoo unavailable)",
        "",
        "```json",
        json.dumps(payload, indent=2),
        "```",
    ]
    out.write_text("\n".join(lines))
    logger.info("Odoo transaction queued: %s", out.name)
    return out


def write_to_tmp_vault(filename: str, content: str) -> Path:
    """Write vault content to /tmp when Obsidian vault is locked."""
    tmp_dir = Path("/tmp/ai_employee_vault_buffer")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out = tmp_dir / filename
    out.write_text(content)
    logger.info("Vault locked — wrote to tmp: %s", out)
    return out
