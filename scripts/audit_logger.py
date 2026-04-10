"""
audit_logger.py — Structured audit logging for the AI Employee.

Every action the vault takes is appended as a JSON object to the daily log
file at Logs/YYYY-MM-DD.json.  The schema is a superset of the existing log
entries so old files remain readable.

Public API
----------
    from scripts.audit_logger import log_action, audit

    # Fire-and-forget
    log_action(
        action_type="gmail_check",
        actor="watcher",
        target="inbox",
        params={"query": "is:unread is:important"},
        result="success",
    )

    # Auto-timed context manager
    with audit("email_send", actor="mcp_server", target="alice@example.com") as a:
        send_email(...)
        a.set_result("success")

Helper queries
--------------
    from scripts.audit_logger import get_daily_summary, get_weekly_summary, search_logs
    from scripts.audit_logger import rotate_logs

Log schema
----------
    {
      "timestamp":       "<ISO 8601 UTC>",
      "action_type":     "<type>",
      "actor":           "claude_code | watcher | mcp_server | scheduler | watchdog",
      "target":          "<what was acted on>",
      "parameters":      {},
      "approval_status": "auto | approved | pending | rejected",
      "approved_by":     "human | auto | null",
      "result":          "success | failure | error | dry_run | skipped",
      "error_message":   "<string or null>",
      "duration_ms":     <int or null>,
      ... any extra fields from older entries are preserved ...
    }

Retention / rotation
--------------------
    rotate_logs()          # archive files older than 90 days
    rotate_logs(days=30)   # custom retention

Thread safety: one threading.Lock per calendar-date file (within a process).
Cross-process safety: write uses an atomic read-modify-write guarded by
fcntl.flock on Linux/WSL so concurrent processes do not corrupt the array.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import shutil
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generator, Iterator, Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
VAULT_ROOT   = _SCRIPTS_DIR.parent
LOGS_DIR     = VAULT_ROOT / "Logs"
ARCHIVE_DIR  = LOGS_DIR / "Archive"

LOGS_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("audit_logger")

# ---------------------------------------------------------------------------
# Valid field values (used for documentation; not enforced at runtime)
# ---------------------------------------------------------------------------
ACTORS    = ("claude_code", "watcher", "mcp_server", "scheduler", "watchdog")
RESULTS   = ("success", "failure", "error", "dry_run", "skipped", "pending")
APPROVALS = ("auto", "approved", "pending", "rejected")

# Default retention window
DEFAULT_RETENTION_DAYS = 90

# ---------------------------------------------------------------------------
# Per-date file locks (in-process serialisation)
# ---------------------------------------------------------------------------
_file_locks: dict[str, threading.Lock] = {}
_meta_lock  = threading.Lock()


def _get_lock(date_str: str) -> threading.Lock:
    """Return (creating if needed) the per-date threading.Lock."""
    with _meta_lock:
        if date_str not in _file_locks:
            _file_locks[date_str] = threading.Lock()
        return _file_locks[date_str]


# ---------------------------------------------------------------------------
# Low-level read/write with fcntl for cross-process safety
# ---------------------------------------------------------------------------

def _log_file(for_date: date | None = None) -> Path:
    d = for_date or datetime.now(timezone.utc).date()
    return LOGS_DIR / f"{d.isoformat()}.json"


def _read_entries(path: Path) -> list[dict]:
    """Read existing JSON array from *path*; return [] on missing / corrupt."""
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8").strip()
        return json.loads(text) if text else []
    except (json.JSONDecodeError, OSError):
        logger.warning("Could not parse %s — starting fresh list", path.name)
        return []


def _append_entry(entry: dict, for_date: date | None = None) -> None:
    """
    Append *entry* to the daily log file in an atomic, cross-process-safe way.

    Uses:
      1. threading.Lock (per date) — prevents races within one process
      2. fcntl.flock LOCK_EX       — prevents races across OS processes
    """
    path     = _log_file(for_date)
    date_str = (for_date or datetime.now(timezone.utc).date()).isoformat()
    tlock    = _get_lock(date_str)

    with tlock:
        # Open (creating if needed) with exclusive flock
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            with os.fdopen(fd, "r+", encoding="utf-8") as fh:
                text     = fh.read().strip()
                entries  = json.loads(text) if text else []
                entries.append(entry)
                fh.seek(0)
                fh.write(json.dumps(entries, indent=2, ensure_ascii=False))
                fh.truncate()
        except Exception:
            os.close(fd)
            raise
        # flock released automatically when fd closes (fdopen took ownership)


# ---------------------------------------------------------------------------
# Public: log_action
# ---------------------------------------------------------------------------

def log_action(
    action_type:     str,
    actor:           str                 = "claude_code",
    target:          str                 = "",
    params:          dict | None         = None,
    result:          str                 = "success",
    approval_status: str                 = "auto",
    approved_by:     str | None          = "auto",
    error_message:   str | None          = None,
    duration_ms:     int | None          = None,
    for_date:        date | None         = None,
    **extra:         Any,
) -> dict:
    """
    Append one audit entry to today's log file and return it.

    Parameters
    ----------
    action_type     : verb describing what happened, e.g. "gmail_check"
    actor           : which system component acted (see ACTORS)
    target          : resource acted on, e.g. "inbox", "alice@example.com"
    params          : input parameters / context dict
    result          : outcome (see RESULTS)
    approval_status : approval state (see APPROVALS)
    approved_by     : "human" | "auto" | None
    error_message   : error string if result is "failure" or "error"
    duration_ms     : elapsed time in milliseconds
    for_date        : override calendar date (defaults to today UTC)
    **extra         : any additional fields (preserved verbatim)
    """
    entry: dict[str, Any] = {
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "action_type":     action_type,
        "actor":           actor,
        "target":          target,
        "parameters":      params or {},
        "approval_status": approval_status,
        "approved_by":     approved_by,
        "result":          result,
        "error_message":   error_message,
        "duration_ms":     duration_ms,
    }
    entry.update(extra)   # merge caller-supplied extra fields

    _append_entry(entry, for_date=for_date)
    logger.debug(
        "[audit] %s  actor=%s  target=%s  result=%s",
        action_type, actor, target, result,
    )
    return entry


# ---------------------------------------------------------------------------
# Public: audit() context manager — auto-times the block
# ---------------------------------------------------------------------------

class _AuditEntry:
    """Mutable state object passed to the `audit` context manager caller."""

    def __init__(self) -> None:
        self._result:        str       = "success"
        self._error_message: str | None = None
        self._extra:         dict      = {}

    def set_result(self, result: str, error_message: str | None = None) -> None:
        self._result        = result
        self._error_message = error_message

    def set_extra(self, **kwargs: Any) -> None:
        self._extra.update(kwargs)


@contextmanager
def audit(
    action_type:     str,
    actor:           str          = "claude_code",
    target:          str          = "",
    params:          dict | None  = None,
    approval_status: str          = "auto",
    approved_by:     str | None   = "auto",
    for_date:        date | None  = None,
    **extra:         Any,
) -> Generator[_AuditEntry, None, None]:
    """
    Context manager that auto-times a block and writes one audit entry.

    with audit("email_send", actor="mcp_server", target="client@x.com") as a:
        send_email(payload)
        a.set_result("success")
    # entry written with measured duration_ms
    """
    entry_obj   = _AuditEntry()
    entry_obj.set_extra(**extra)
    start_ms    = time.monotonic() * 1000

    try:
        yield entry_obj
    except Exception as exc:
        if entry_obj._result == "success":
            entry_obj.set_result("error", str(exc))
        raise
    finally:
        duration_ms = int(time.monotonic() * 1000 - start_ms)
        merged_extra = {**extra, **entry_obj._extra}
        log_action(
            action_type     = action_type,
            actor           = actor,
            target          = target,
            params          = params,
            result          = entry_obj._result,
            approval_status = approval_status,
            approved_by     = approved_by,
            error_message   = entry_obj._error_message,
            duration_ms     = duration_ms,
            for_date        = for_date,
            **merged_extra,
        )


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------

def _load_day(d: date) -> list[dict]:
    return _read_entries(_log_file(d))


def _iter_date_range(start: date, end: date) -> Iterator[date]:
    """Yield each date in [start, end] inclusive."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def get_daily_summary(for_date: date | None = None) -> dict:
    """
    Return aggregate statistics for one calendar day.

    Returns
    -------
    {
      "date":          "YYYY-MM-DD",
      "total_actions": int,
      "by_actor":      {"claude_code": int, ...},
      "by_result":     {"success": int, "error": int, ...},
      "by_type":       {"gmail_check": int, ...},
      "error_rate":    float,          # 0.0 – 1.0
      "errors":        [entry, ...],   # entries where result in ("error","failure")
      "avg_duration_ms": float | None,
    }
    """
    d       = for_date or datetime.now(timezone.utc).date()
    entries = _load_day(d)

    by_actor:  dict[str, int] = {}
    by_result: dict[str, int] = {}
    by_type:   dict[str, int] = {}
    errors:    list[dict]     = []
    durations: list[int]      = []

    for e in entries:
        actor  = e.get("actor",       "unknown")
        result = e.get("result",      "unknown")
        atype  = e.get("action_type", "unknown")
        dur    = e.get("duration_ms")

        by_actor[actor]   = by_actor.get(actor, 0)   + 1
        by_result[result] = by_result.get(result, 0) + 1
        by_type[atype]    = by_type.get(atype, 0)    + 1

        if result in ("error", "failure"):
            errors.append(e)
        if isinstance(dur, (int, float)):
            durations.append(dur)

    total      = len(entries)
    error_count = len(errors)

    return {
        "date":            d.isoformat(),
        "total_actions":   total,
        "by_actor":        by_actor,
        "by_result":       by_result,
        "by_type":         by_type,
        "error_rate":      round(error_count / total, 4) if total else 0.0,
        "errors":          errors,
        "avg_duration_ms": round(sum(durations) / len(durations), 1) if durations else None,
    }


def get_weekly_summary(
    start_date: date | None = None,
    end_date:   date | None = None,
) -> dict:
    """
    Aggregate statistics across a date range (default: last 7 days).

    Returns
    -------
    {
      "start_date":      "YYYY-MM-DD",
      "end_date":        "YYYY-MM-DD",
      "days_with_data":  int,
      "total_actions":   int,
      "by_actor":        {...},
      "by_result":       {...},
      "by_type":         {...},
      "error_rate":      float,
      "busiest_day":     "YYYY-MM-DD" | null,
      "daily_counts":    {"YYYY-MM-DD": int, ...},
      "avg_duration_ms": float | None,
    }
    """
    today = datetime.now(timezone.utc).date()
    end   = end_date   or today
    start = start_date or (end - timedelta(days=6))

    by_actor:     dict[str, int] = {}
    by_result:    dict[str, int] = {}
    by_type:      dict[str, int] = {}
    daily_counts: dict[str, int] = {}
    durations:    list[int]      = []
    error_count   = 0
    total         = 0

    for d in _iter_date_range(start, end):
        entries = _load_day(d)
        daily_counts[d.isoformat()] = len(entries)
        total += len(entries)

        for e in entries:
            actor  = e.get("actor",       "unknown")
            result = e.get("result",      "unknown")
            atype  = e.get("action_type", "unknown")
            dur    = e.get("duration_ms")

            by_actor[actor]   = by_actor.get(actor, 0)   + 1
            by_result[result] = by_result.get(result, 0) + 1
            by_type[atype]    = by_type.get(atype, 0)    + 1

            if result in ("error", "failure"):
                error_count += 1
            if isinstance(dur, (int, float)):
                durations.append(dur)

    days_with_data = sum(1 for c in daily_counts.values() if c > 0)
    busiest        = max(daily_counts, key=daily_counts.get) if daily_counts else None

    return {
        "start_date":      start.isoformat(),
        "end_date":        end.isoformat(),
        "days_with_data":  days_with_data,
        "total_actions":   total,
        "by_actor":        by_actor,
        "by_result":       by_result,
        "by_type":         by_type,
        "error_rate":      round(error_count / total, 4) if total else 0.0,
        "busiest_day":     busiest,
        "daily_counts":    daily_counts,
        "avg_duration_ms": round(sum(durations) / len(durations), 1) if durations else None,
    }


def search_logs(
    query:      str | None             = None,
    action_type: str | None            = None,
    actor:      str | None             = None,
    result:     str | None             = None,
    start_date: date | None            = None,
    end_date:   date | None            = None,
    limit:      int                    = 200,
) -> list[dict]:
    """
    Search log entries across a date range.

    Parameters
    ----------
    query       : free-text substring search across all string fields
    action_type : exact match on action_type
    actor       : exact match on actor
    result      : exact match on result
    start_date  : inclusive start (default: 30 days ago)
    end_date    : inclusive end (default: today)
    limit       : max entries returned (newest first)

    Returns
    -------
    List of matching entries, sorted newest-first, capped at *limit*.
    """
    today = datetime.now(timezone.utc).date()
    end   = end_date   or today
    start = start_date or (end - timedelta(days=29))

    matches: list[dict] = []

    for d in _iter_date_range(start, end):
        for entry in _load_day(d):
            # Structured filters
            if action_type and entry.get("action_type") != action_type:
                continue
            if actor and entry.get("actor") != actor:
                continue
            if result and entry.get("result") != result:
                continue
            # Free-text: any string field contains query (case-insensitive)
            if query:
                haystack = " ".join(
                    str(v) for v in entry.values() if isinstance(v, (str, int, float))
                ).lower()
                if query.lower() not in haystack:
                    continue
            matches.append(entry)

    # Sort newest-first by timestamp, cap at limit
    matches.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return matches[:limit]


# ---------------------------------------------------------------------------
# Log rotation / retention
# ---------------------------------------------------------------------------

def rotate_logs(retention_days: int = DEFAULT_RETENTION_DAYS) -> list[Path]:
    """
    Move daily log files older than *retention_days* to Logs/Archive/.

    Returns the list of files archived.
    """
    cutoff   = datetime.now(timezone.utc).date() - timedelta(days=retention_days)
    archived: list[Path] = []

    for log_file in sorted(LOGS_DIR.glob("????-??-??.json")):
        try:
            file_date = date.fromisoformat(log_file.stem)
        except ValueError:
            continue

        if file_date < cutoff:
            dest = ARCHIVE_DIR / log_file.name
            shutil.move(str(log_file), dest)
            archived.append(dest)
            logger.info("Archived: %s → Archive/", log_file.name)

    if archived:
        logger.info("rotate_logs: archived %d file(s) older than %d days", len(archived), retention_days)
    else:
        logger.debug("rotate_logs: nothing to archive (retention=%d days)", retention_days)

    return archived


def prune_archive(max_age_days: int = 365) -> list[Path]:
    """
    Permanently delete archived files older than *max_age_days*.

    Returns the list of deleted files.
    """
    cutoff  = datetime.now(timezone.utc).date() - timedelta(days=max_age_days)
    deleted: list[Path] = []

    for arc_file in sorted(ARCHIVE_DIR.glob("????-??-??.json")):
        try:
            file_date = date.fromisoformat(arc_file.stem)
        except ValueError:
            continue
        if file_date < cutoff:
            arc_file.unlink()
            deleted.append(arc_file)
            logger.info("Pruned archive: %s", arc_file.name)

    return deleted


# ---------------------------------------------------------------------------
# CLI entry point — quick inspection tool
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AI Employee audit log inspector")
    sub    = parser.add_subparsers(dest="cmd")

    p_summary = sub.add_parser("summary", help="Daily or weekly summary")
    p_summary.add_argument("--date",  default=None, help="YYYY-MM-DD (default: today)")
    p_summary.add_argument("--week",  action="store_true", help="Last 7-day summary")

    p_search  = sub.add_parser("search", help="Search log entries")
    p_search.add_argument("query",          nargs="?", default=None)
    p_search.add_argument("--action-type",  default=None)
    p_search.add_argument("--actor",        default=None)
    p_search.add_argument("--result",       default=None)
    p_search.add_argument("--start",        default=None, help="YYYY-MM-DD")
    p_search.add_argument("--end",          default=None, help="YYYY-MM-DD")
    p_search.add_argument("--limit",        type=int, default=20)

    p_rotate  = sub.add_parser("rotate", help="Archive old log files")
    p_rotate.add_argument("--days", type=int, default=DEFAULT_RETENTION_DAYS)

    args = parser.parse_args()

    if args.cmd == "summary":
        if args.week:
            result = get_weekly_summary()
        else:
            d = date.fromisoformat(args.date) if args.date else None
            result = get_daily_summary(d)
        print(json.dumps(result, indent=2, default=str))

    elif args.cmd == "search":
        start = date.fromisoformat(args.start) if args.start else None
        end   = date.fromisoformat(args.end)   if args.end   else None
        hits  = search_logs(
            query       = args.query,
            action_type = args.action_type,
            actor       = args.actor,
            result      = args.result,
            start_date  = start,
            end_date    = end,
            limit       = args.limit,
        )
        print(json.dumps(hits, indent=2, default=str))
        print(f"\n{len(hits)} result(s)")

    elif args.cmd == "rotate":
        archived = rotate_logs(retention_days=args.days)
        print(f"Archived {len(archived)} file(s).")

    else:
        parser.print_help()
