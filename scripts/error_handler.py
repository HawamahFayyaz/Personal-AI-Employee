"""
error_handler.py — Central error handling facade for the AI Employee.

Combines retry_handler and watchdog utilities into a single import point.
Also exposes graceful degradation helpers for each external service.

Usage:
    from scripts.error_handler import handle_error, ErrorCategory
    from scripts.error_handler import gmail_send, odoo_write, vault_write

    # Decorator form
    @handle_error(ErrorCategory.TRANSIENT)
    def call_api(): ...

    # Context-manager form
    with ErrorContext("send_email", ErrorCategory.TRANSIENT):
        gmail_service.send(...)

    # Graceful degradation
    gmail_send(service=svc, payload=p, fallback_to_queue=True)
"""

from __future__ import annotations

import json
import logging
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from scripts.retry_handler import (
    ErrorCategory,
    RetryExhausted,
    AuthError,
    LogicError,
    DataError,
    call_with_retry,
    with_retry,
    queue_email_locally,
    log_odoo_transaction,
    write_to_tmp_vault,
    _write_alert,
    LOGS_DIR,
    VAULT_ROOT,
)

__all__ = [
    "ErrorCategory",
    "RetryExhausted",
    "AuthError",
    "LogicError",
    "DataError",
    "handle_error",
    "ErrorContext",
    "call_with_retry",
    "with_retry",
    "gmail_send",
    "odoo_write",
    "vault_write",
    "record_error",
]

logger = logging.getLogger("error_handler")

# ---------------------------------------------------------------------------
# Error counter — persisted to Logs/error_counts.json
# ---------------------------------------------------------------------------
_ERROR_COUNTS_FILE = LOGS_DIR / "error_counts.json"


def _load_error_counts() -> dict:
    if _ERROR_COUNTS_FILE.exists():
        try:
            return json.loads(_ERROR_COUNTS_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def _save_error_counts(counts: dict) -> None:
    _ERROR_COUNTS_FILE.write_text(json.dumps(counts, indent=2))


def record_error(category: ErrorCategory, operation: str) -> None:
    """Increment the error counter for (category, operation) and persist it."""
    counts = _load_error_counts()
    key = f"{category.value}:{operation}"
    counts[key] = counts.get(key, 0) + 1
    counts["__last_updated"] = datetime.now(timezone.utc).isoformat()
    _save_error_counts(counts)


# ---------------------------------------------------------------------------
# Decorator shorthand
# ---------------------------------------------------------------------------

def handle_error(
    category:     ErrorCategory = ErrorCategory.TRANSIENT,
    operation:    str           = "",
    max_attempts: int           = 3,
    quarantine_path: Optional[Path] = None,
) -> Callable:
    """
    Decorator shorthand.  Equivalent to @with_retry but with record_error calls.

    @handle_error(ErrorCategory.TRANSIENT)
    def poll_gmail(): ...
    """
    def decorator(fn: Callable) -> Callable:
        import functools

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            op = operation or fn.__name__
            try:
                return call_with_retry(
                    fn, *args,
                    category=category,
                    operation=op,
                    max_attempts=max_attempts,
                    quarantine_path=quarantine_path,
                    **kwargs,
                )
            except (RetryExhausted, AuthError, LogicError, DataError) as exc:
                record_error(category, op)
                raise
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Context-manager form
# ---------------------------------------------------------------------------

@contextmanager
def ErrorContext(operation: str, category: ErrorCategory = ErrorCategory.TRANSIENT):
    """
    Context manager that converts any exception to the appropriate typed error.

    with ErrorContext("send_linkedin_post", ErrorCategory.TRANSIENT):
        linkedin_api.post(payload)
    """
    try:
        yield
    except (RetryExhausted, AuthError, LogicError, DataError):
        record_error(category, operation)
        raise
    except Exception as exc:
        record_error(category, operation)
        _write_alert(
            category.value,
            f"Unhandled error in {operation}: {exc}",
            {"traceback": traceback.format_exc()},
        )
        if category == ErrorCategory.AUTH:
            raise AuthError(str(exc)) from exc
        elif category == ErrorCategory.LOGIC:
            raise LogicError(str(exc)) from exc
        elif category == ErrorCategory.DATA:
            raise DataError(str(exc)) from exc
        else:
            raise RetryExhausted(operation, 1, exc) from exc


# ---------------------------------------------------------------------------
# Graceful degradation helpers
# ---------------------------------------------------------------------------

def gmail_send(
    service,
    payload: dict,
    fallback_to_queue: bool = True,
) -> bool:
    """
    Send an email via Gmail API with automatic fallback to local queue.

    Returns True on success, False if queued locally.
    """
    def _send():
        service.users().messages().send(userId="me", body=payload).execute()

    try:
        call_with_retry(
            _send,
            category=ErrorCategory.TRANSIENT,
            operation="gmail_send",
        )
        return True
    except RetryExhausted as exc:
        if fallback_to_queue:
            logger.warning("Gmail API down — queuing email locally: %s", exc)
            to      = payload.get("to", "unknown")
            subject = payload.get("subject", "(no subject)")
            body    = json.dumps(payload)
            queue_email_locally(to, subject, body)
            return False
        raise


def odoo_write(
    client,
    model: str,
    method: str,
    args: list,
    fallback_to_log: bool = True,
) -> Any:
    """
    Write to Odoo with automatic fallback to markdown log.

    Returns the Odoo response, or the Path to the queued file on failure.
    """
    def _write():
        return client.execute_kw(model, method, args)

    try:
        return call_with_retry(
            _write,
            category=ErrorCategory.TRANSIENT,
            operation=f"odoo_{model}_{method}",
        )
    except RetryExhausted as exc:
        if fallback_to_log:
            logger.warning("Odoo unavailable — logging transaction: %s", exc)
            return log_odoo_transaction({
                "model": model, "method": method, "args": args,
            })
        raise


def vault_write(filename: str, content: str, vault_path: Optional[Path] = None) -> Path:
    """
    Write a file to the vault, falling back to /tmp if the vault is locked.

    Returns the path actually written to.
    """
    target_dir = vault_path or VAULT_ROOT
    target     = target_dir / filename

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return target
    except (PermissionError, OSError) as exc:
        logger.warning("Vault locked (%s) — writing to /tmp", exc)
        return write_to_tmp_vault(filename, content)
