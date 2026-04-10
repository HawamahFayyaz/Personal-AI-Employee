#!/usr/bin/env python3
"""
generate_briefing.py — CEO Monday Briefing data gatherer and generator.

Collects and structures data from vault files for the past 7 days, then
invokes Claude to analyse it and write a Monday CEO briefing to Briefings/.

Usage:
    python scripts/generate_briefing.py               # last full week (Sun → Sat)
    python scripts/generate_briefing.py --dry-run     # print data packet, skip Claude
    python scripts/generate_briefing.py --date 2026-04-13     # report dated this Monday
    python scripts/generate_briefing.py --from 2026-04-07 --to 2026-04-13
    python scripts/generate_briefing.py --output Briefings/custom.md

Cron (every Sunday at 20:00 UTC):
    0 20 * * 0  cd ~/Projects/AI_Employee_Vault && python scripts/generate_briefing.py >> Logs/cron.log 2>&1
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Optional dotenv
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
VAULT       = Path(__file__).resolve().parent.parent
DONE_DIR    = VAULT / "Done"
LOGS_DIR    = VAULT / "Logs"
BRIEFINGS   = VAULT / "Briefings"
NEEDS_ACT   = VAULT / "Needs_Action"
PENDING_DIR = VAULT / "Pending_Approval"
INBOX_DIR   = VAULT / "Inbox"
PLANS_DIR   = VAULT / "Plans"

BRIEFINGS.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_log_file = LOGS_DIR / f"watcher_{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_log_file, encoding="utf-8"),
    ],
)
logger = logging.getLogger("generate_briefing")

# ---------------------------------------------------------------------------
# Frontmatter parser (shared pattern across vault)
# ---------------------------------------------------------------------------
_FM_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    try:
        import yaml
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception:
        fm = {}
    return fm, text[m.end():]


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# 1. Business Goals
# ---------------------------------------------------------------------------

def gather_business_goals() -> dict[str, Any]:
    goals_path = VAULT / "Business_Goals.md"
    result: dict[str, Any] = {
        "revenue_target_monthly": None,
        "revenue_mtd": None,
        "kpis": [],
        "subscription_rules": [],
        "active_projects": [],
        "raw_available": goals_path.exists(),
    }
    if not goals_path.exists():
        return result

    text = goals_path.read_text(encoding="utf-8")

    # Revenue target
    m = re.search(r"Monthly goal:\s*\$?([\d,]+)", text)
    if m:
        result["revenue_target_monthly"] = int(m.group(1).replace(",", ""))

    m = re.search(r"Current MTD:\s*\$?([\d,]+)", text)
    if m:
        result["revenue_mtd"] = int(m.group(1).replace(",", ""))

    # KPI table rows: | Metric | Target | Alert Threshold |
    for row in re.finditer(r"\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", text):
        metric, target, alert = row.group(1).strip(), row.group(2).strip(), row.group(3).strip()
        if metric.lower() in ("metric", "---", ":---"):
            continue
        result["kpis"].append({"metric": metric, "target": target, "alert_threshold": alert})

    # Subscription audit rules (bulleted list under "Flag for review if:")
    in_subs = False
    for line in text.splitlines():
        if "Flag for review if:" in line or "Subscription Audit" in line:
            in_subs = True
            continue
        if in_subs:
            stripped = line.strip().lstrip("-").strip()
            if stripped and not stripped.startswith("#"):
                result["subscription_rules"].append(stripped)
            elif line.startswith("#"):
                in_subs = False

    return result


# ---------------------------------------------------------------------------
# 2. Completed tasks in Done/ for the period
# ---------------------------------------------------------------------------

def _turnaround_hours(fm: dict, body: str, fpath: Path) -> float | None:
    """Return hours between item creation and resolution, or None."""
    # Creation time: frontmatter `dropped_at`, `created`, `received`
    created_str = fm.get("dropped_at") or fm.get("created") or fm.get("received")
    created_dt  = _parse_iso(str(created_str)) if created_str else None

    # Resolution time: ## Resolution section
    m = re.search(r"\*\*Resolved\*\*:\s*(\S+)", body)
    resolved_str  = m.group(1) if m else None
    resolved_dt   = _parse_iso(resolved_str) if resolved_str else None

    # Fallback: file mtime
    if resolved_dt is None:
        resolved_dt = datetime.fromtimestamp(fpath.stat().st_mtime, tz=timezone.utc)

    if created_dt is None or resolved_dt is None:
        return None
    delta = resolved_dt - created_dt
    if delta.total_seconds() < 0:
        return None
    return round(delta.total_seconds() / 3600, 1)


def gather_completed_tasks(period_start: date, period_end: date) -> dict[str, Any]:
    result: dict[str, Any] = {
        "total": 0,
        "by_priority": {},
        "by_type": {},
        "by_outcome": {},
        "items": [],
    }
    if not DONE_DIR.exists():
        return result

    start_dt = datetime.combine(period_start, datetime.min.time()).replace(tzinfo=timezone.utc)
    end_dt   = datetime.combine(period_end,   datetime.max.time()).replace(tzinfo=timezone.utc)

    for fpath in sorted(DONE_DIR.glob("*.md")):
        mtime = datetime.fromtimestamp(fpath.stat().st_mtime, tz=timezone.utc)
        if not (start_dt <= mtime <= end_dt):
            continue

        try:
            text     = fpath.read_text(encoding="utf-8")
            fm, body = _parse_frontmatter(text)
        except OSError:
            continue

        t_hours   = _turnaround_hours(fm, body, fpath)
        task_type = str(fm.get("type", "unknown"))
        priority  = str(fm.get("priority", "medium"))

        # Outcome: from Resolution section or status field
        m_outcome = re.search(r"\*\*Outcome\*\*:\s*(\w+)", body)
        outcome   = m_outcome.group(1) if m_outcome else str(fm.get("status", "completed"))

        result["total"] += 1
        result["by_priority"][priority] = result["by_priority"].get(priority, 0) + 1
        result["by_type"][task_type]    = result["by_type"].get(task_type, 0) + 1
        result["by_outcome"][outcome]   = result["by_outcome"].get(outcome, 0) + 1
        result["items"].append({
            "name":             fpath.name,
            "type":             task_type,
            "priority":         priority,
            "outcome":          outcome,
            "turnaround_hours": t_hours,
            "resolved_date":    mtime.strftime("%Y-%m-%d"),
        })

    return result


# ---------------------------------------------------------------------------
# 3. Bottleneck detection
# ---------------------------------------------------------------------------

# Company_Handbook.md priority SLAs
_SLA_HOURS: dict[str, float] = {
    "critical": 0.25,   # P0: immediate
    "high":     1.0,    # P1: within 1 hour
    "medium":   4.0,    # P2: within 4 hours
    "low":      24.0,   # P3: daily batch
}


def detect_bottlenecks(tasks: dict[str, Any]) -> dict[str, Any]:
    bottlenecks = []
    for item in tasks.get("items", []):
        prio     = item.get("priority", "medium").lower()
        actual   = item.get("turnaround_hours")
        expected = _SLA_HOURS.get(prio, 4.0)
        if actual is not None and actual > expected:
            bottlenecks.append({
                "task":           item["name"],
                "priority":       prio,
                "expected_hours": expected,
                "actual_hours":   actual,
                "delay_hours":    round(actual - expected, 1),
            })
    bottlenecks.sort(key=lambda x: x["delay_hours"], reverse=True)
    return {"total": len(bottlenecks), "items": bottlenecks}


# ---------------------------------------------------------------------------
# 4. Log activity summary
# ---------------------------------------------------------------------------

def gather_log_activity(period_start: date, period_end: date) -> dict[str, Any]:
    result: dict[str, Any] = {
        "days_with_logs": 0,
        "total_entries": 0,
        "success_count": 0,
        "error_count": 0,
        "dry_run_count": 0,
        "by_action_type": {},
        "orchestrator_runs": 0,
        "reasoning_loops": 0,
        "watcher_errors": [],
        "summaries": [],
    }

    current = period_start
    while current <= period_end:
        day_str  = current.strftime("%Y-%m-%d")
        json_log = LOGS_DIR / f"{day_str}.json"

        if json_log.exists():
            result["days_with_logs"] += 1
            try:
                entries = json.loads(json_log.read_text(encoding="utf-8"))
                if not isinstance(entries, list):
                    entries = [entries]
                for entry in entries:
                    result["total_entries"] += 1
                    atype  = entry.get("action_type", "unknown")
                    status = entry.get("result", "")

                    result["by_action_type"][atype] = (
                        result["by_action_type"].get(atype, 0) + 1
                    )
                    if status == "success":
                        result["success_count"] += 1
                    elif status in ("error", "failed"):
                        result["error_count"] += 1
                    elif status == "dry_run":
                        result["dry_run_count"] += 1

                    if atype == "orchestrator_run":
                        result["orchestrator_runs"] += 1
                    elif atype == "reasoning_loop":
                        result["reasoning_loops"] += 1
                        summary = entry.get("summary", "")
                        if summary:
                            result["summaries"].append(f"{day_str}: {summary}")
            except (json.JSONDecodeError, OSError):
                pass

        # Scan watcher .log for ERROR lines
        watcher_log = LOGS_DIR / f"watcher_{day_str}.log"
        if watcher_log.exists():
            try:
                for line in watcher_log.read_text(encoding="utf-8").splitlines():
                    if "  ERROR" in line or " ERROR " in line:
                        result["watcher_errors"].append(line.strip()[:120])
            except OSError:
                pass

        current += timedelta(days=1)

    # Trim to most recent 20 error lines to keep the packet manageable
    result["watcher_errors"] = result["watcher_errors"][-20:]
    result["summaries"]      = result["summaries"][-10:]
    return result


# ---------------------------------------------------------------------------
# 5. Accounting / Revenue data
# ---------------------------------------------------------------------------

def gather_accounting() -> dict[str, Any]:
    result: dict[str, Any] = {
        "has_data": False,
        "source": None,
        "revenue_this_week": None,
        "revenue_mtd": None,
        "expenses_mtd": None,
        "raw_excerpt": None,
    }

    candidates = [
        VAULT / "Accounting" / "Current_Month.md",
        VAULT / "Accounting" / "current_month.md",
    ]
    # Also look for any *.md in Accounting/ sorted newest-first
    acc_dir = VAULT / "Accounting"
    if acc_dir.exists():
        candidates += sorted(acc_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)

    for path in candidates:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue

        result["has_data"] = True
        result["source"]   = str(path.relative_to(VAULT))

        # Try to extract revenue and expense numbers from common patterns
        for pattern, key in [
            (r"[Rr]evenue[^$\n]*\$?([\d,]+(?:\.\d{2})?)", "revenue_mtd"),
            (r"[Ee]xpenses?[^$\n]*\$?([\d,]+(?:\.\d{2})?)", "expenses_mtd"),
            (r"[Tt]otal[^$\n]*\$?([\d,]+(?:\.\d{2})?)", "revenue_mtd"),
        ]:
            m = re.search(pattern, text)
            if m and result[key] is None:
                raw = m.group(1).replace(",", "")
                try:
                    result[key] = float(raw)
                except ValueError:
                    pass

        result["raw_excerpt"] = text[:800]
        break

    return result


# ---------------------------------------------------------------------------
# 6. Social media summaries
# ---------------------------------------------------------------------------

def gather_social_metrics(period_start: date, period_end: date) -> dict[str, Any]:
    result: dict[str, Any] = {
        "has_data": False,
        "platforms": [],
        "summaries": [],
    }

    start_dt = datetime.combine(period_start, datetime.min.time()).replace(tzinfo=timezone.utc)
    end_dt   = datetime.combine(period_end,   datetime.max.time()).replace(tzinfo=timezone.utc)

    # Search in Needs_Action/ and Done/
    for search_dir in (NEEDS_ACT, DONE_DIR):
        if not search_dir.exists():
            continue
        for pattern in ("SOCIAL_SUMMARY_*.md", "TWITTER_SUMMARY_*.md"):
            for fpath in search_dir.glob(pattern):
                mtime = datetime.fromtimestamp(fpath.stat().st_mtime, tz=timezone.utc)
                if not (start_dt <= mtime <= end_dt):
                    continue
                try:
                    text     = fpath.read_text(encoding="utf-8")
                    fm, body = _parse_frontmatter(text)
                    platform = str(fm.get("platform", "social"))
                    result["has_data"] = True
                    for p in platform.split(","):
                        p = p.strip()
                        if p and p not in result["platforms"]:
                            result["platforms"].append(p)
                    result["summaries"].append({
                        "file":     fpath.name,
                        "platform": platform,
                        "date":     str(fm.get("date", mtime.strftime("%Y-%m-%d"))),
                        "excerpt":  body.strip()[:600],
                    })
                except OSError:
                    pass

    return result


# ---------------------------------------------------------------------------
# 7. Pending items snapshot
# ---------------------------------------------------------------------------

def gather_pending() -> dict[str, Any]:
    approvals = list(PENDING_DIR.glob("*.md")) if PENDING_DIR.exists() else []
    needs_act = list(NEEDS_ACT.glob("*.md"))   if NEEDS_ACT.exists()   else []
    inbox     = list(INBOX_DIR.iterdir())       if INBOX_DIR.exists()   else []

    oldest_days: int | None = None
    oldest_name: str | None = None
    if approvals:
        now = datetime.now(timezone.utc)
        for f in approvals:
            age = (now - datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)).days
            if oldest_days is None or age > oldest_days:
                oldest_days = age
                oldest_name = f.name

    return {
        "approvals_count":       len(approvals),
        "needs_action_count":    len(needs_act),
        "inbox_count":           len(inbox),
        "oldest_approval_days":  oldest_days,
        "oldest_approval_name":  oldest_name,
    }


# ---------------------------------------------------------------------------
# 8. Vault snapshot counts
# ---------------------------------------------------------------------------

def gather_vault_snapshot() -> dict[str, Any]:
    briefings = list(BRIEFINGS.glob("*.md")) if BRIEFINGS.exists() else []
    done      = list(DONE_DIR.glob("*.md"))  if DONE_DIR.exists()  else []
    plans     = list(PLANS_DIR.glob("*.md")) if PLANS_DIR.exists() else []

    return {
        "briefings_total": len([f for f in briefings if not f.name.startswith(".")]),
        "done_total":      len(done),
        "plans_active":    len(plans),
    }


# ---------------------------------------------------------------------------
# 9. Assemble full data packet
# ---------------------------------------------------------------------------

def build_data_packet(
    period_start: date,
    period_end:   date,
    report_date:  date,
) -> dict[str, Any]:
    logger.info("Gathering business goals…")
    goals = gather_business_goals()

    logger.info("Gathering completed tasks (Done/ %s → %s)…", period_start, period_end)
    tasks = gather_completed_tasks(period_start, period_end)

    logger.info("Detecting bottlenecks…")
    bottlenecks = detect_bottlenecks(tasks)

    logger.info("Gathering log activity…")
    logs = gather_log_activity(period_start, period_end)

    logger.info("Gathering accounting data…")
    accounting = gather_accounting()

    logger.info("Gathering social media metrics…")
    social = gather_social_metrics(period_start, period_end)

    logger.info("Gathering pending items snapshot…")
    pending = gather_pending()

    logger.info("Gathering vault snapshot…")
    vault_snap = gather_vault_snapshot()

    return {
        "period": {
            "start":       period_start.isoformat(),
            "end":         period_end.isoformat(),
            "report_date": report_date.isoformat(),
            "days":        (period_end - period_start).days + 1,
        },
        "business_goals": goals,
        "completed_tasks": tasks,
        "bottlenecks": bottlenecks,
        "log_activity": logs,
        "accounting": accounting,
        "social": social,
        "pending": pending,
        "vault_snapshot": vault_snap,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# 10. Claude prompt
# ---------------------------------------------------------------------------

def build_claude_prompt(
    data_packet: dict,
    output_path: Path,
    period_start: date,
    period_end:   date,
    report_date:  date,
) -> str:
    data_json = json.dumps(data_packet, indent=2, default=str)

    return f"""You are the AI Employee for this vault. Your vault root is: {VAULT}

Read your CEO Briefing skill instructions from:
  Skills/ceo_briefing_skill/SKILL.md

I have pre-gathered the following structured data for the period
{period_start.isoformat()} to {period_end.isoformat()}.
The briefing should be dated {report_date.isoformat()} (Monday).

=== DATA PACKET (JSON) ===
{data_json}
=== END DATA PACKET ===

Your instructions:

1. Read Skills/ceo_briefing_skill/SKILL.md for the exact output format and
   analysis rules. The data packet above replaces any manual data-gathering
   steps in the skill — all raw data is already collected.

2. Analyse the data packet following the skill's Analysis phase:
   - Calculate revenue vs target and determine trend
   - Identify bottlenecks (compare turnaround_hours against SLA hours from skill)
   - Flag any pending approval items stale longer than 24 hours
   - Check subscription rules against Business_Goals (note if no live data)
   - Identify patterns in the log_activity (error spikes, dry-run-only days, etc.)
   - Pull upcoming deadlines from active plans in Plans/

3. Generate the complete Monday CEO Briefing and write it to:
   {output_path}
   Use the EXACT template format specified in the skill (YAML frontmatter + sections).

4. After writing the briefing, update Dashboard.md:
   - Add or update a "## Latest Briefing" section near the top (after ## Alerts)
     with a link: [{report_date.strftime("%B %d, %Y")} Briefing]({output_path.relative_to(VAULT)})
   - Prepend an entry to ## Recent Activity in this format:
     - `{datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")}` — [CEO Briefing] {report_date.isoformat()} briefing generated -> {output_path.name}

5. Write a JSON log entry to Logs/{datetime.now().strftime("%Y-%m-%d")}.json:
   {{
     "timestamp": "<ISO>",
     "action_type": "ceo_briefing",
     "result": "success",
     "output_file": "{output_path.name}",
     "period": "{period_start.isoformat()} to {period_end.isoformat()}",
     "summary": "Monday CEO briefing generated for {report_date.isoformat()}"
   }}

6. Print: BRIEFING COMPLETE → {output_path}

Work autonomously. Cite data from the packet by field name in your reasoning.
Do not ask for clarification. If a data field is null or has_data=false, note
the absence honestly in the briefing rather than inventing numbers.
""".strip()


# ---------------------------------------------------------------------------
# 11. Invoke Claude
# ---------------------------------------------------------------------------

def invoke_claude(prompt: str) -> bool:
    logger.info("Invoking Claude to generate briefing…")
    cmd = ["claude", "--print", "--dangerously-skip-permissions", "-p", prompt]
    try:
        result = subprocess.run(cmd, cwd=str(VAULT), text=True)
        return result.returncode == 0
    except FileNotFoundError:
        logger.error(
            "claude CLI not found. Install Claude Code and ensure it is on PATH."
        )
        return False


# ---------------------------------------------------------------------------
# 12. JSON activity log
# ---------------------------------------------------------------------------

def _append_json_log(entry: dict) -> None:
    log_file = LOGS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.json"
    try:
        existing: list = []
        if log_file.exists():
            existing = json.loads(log_file.read_text(encoding="utf-8"))
        existing.append(entry)
        log_file.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not write to JSON log: %s", exc)


# ---------------------------------------------------------------------------
# 13. Date helpers
# ---------------------------------------------------------------------------

def resolve_dates(args: argparse.Namespace) -> tuple[date, date, date]:
    """Return (period_start, period_end, report_date)."""
    today = date.today()

    if args.from_date and args.to_date:
        start = date.fromisoformat(args.from_date)
        end   = date.fromisoformat(args.to_date)
        # Next Monday on or after end:  (8 - isoweekday) % 7 → 0 if end IS Monday
        days_ahead  = (8 - end.isoweekday()) % 7
        report_date = end + timedelta(days=days_ahead)

    elif args.date:
        report_date = date.fromisoformat(args.date)
        end         = report_date - timedelta(days=1)   # Sunday before Monday
        start       = end - timedelta(days=6)            # previous Monday

    else:
        # Default: last complete Mon–Sun week (isoweekday: Mon=1…Sun=7)
        # days_since_sunday: Mon→1, Tue→2, …, Sat→6, Sun→0
        days_since_sunday = today.isoweekday() % 7
        end         = today - timedelta(days=days_since_sunday)   # most recent Sunday
        start       = end   - timedelta(days=6)                   # Monday 7 days before
        report_date = end   + timedelta(days=1)                   # Sun + 1 = Monday

    return start, end, report_date


# ---------------------------------------------------------------------------
# 14. Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="CEO Monday Briefing generator — gathers vault data and invokes Claude."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print gathered data and the Claude prompt; skip Claude invocation.",
    )
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="Monday report date. Period is Mon–Sun of the preceding week.",
    )
    parser.add_argument(
        "--from",
        dest="from_date",
        metavar="YYYY-MM-DD",
        help="Explicit period start (use with --to).",
    )
    parser.add_argument(
        "--to",
        dest="to_date",
        metavar="YYYY-MM-DD",
        help="Explicit period end (use with --from).",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        help="Override output path for the briefing (default: auto-generated).",
    )
    parser.add_argument(
        "--print-data",
        action="store_true",
        help="Print the raw data packet JSON to stdout and exit.",
    )
    args = parser.parse_args()

    # ── Resolve date range ────────────────────────────────────────────────
    try:
        period_start, period_end, report_date = resolve_dates(args)
    except ValueError as exc:
        logger.error("Invalid date: %s", exc)
        sys.exit(1)

    # ── Output path ───────────────────────────────────────────────────────
    if args.output:
        output_path = VAULT / args.output
    else:
        monday_str  = report_date.strftime("%Y-%m-%d")
        output_path = BRIEFINGS / f"{monday_str}_Monday_Briefing.md"

    logger.info(
        "CEO Briefing generator starting | period=%s → %s | report_date=%s | output=%s",
        period_start, period_end, report_date, output_path.relative_to(VAULT),
    )

    # ── Gather data ────────────────────────────────────────────────────────
    data_packet = build_data_packet(period_start, period_end, report_date)

    logger.info(
        "Data gathered: %d completed tasks, %d bottlenecks, %d log entries, "
        "accounting=%s, social=%s",
        data_packet["completed_tasks"]["total"],
        data_packet["bottlenecks"]["total"],
        data_packet["log_activity"]["total_entries"],
        "yes" if data_packet["accounting"]["has_data"] else "no",
        "yes" if data_packet["social"]["has_data"] else "no",
    )

    # ── --print-data mode ─────────────────────────────────────────────────
    if args.print_data:
        print(json.dumps(data_packet, indent=2, default=str))
        return

    # ── Build Claude prompt ────────────────────────────────────────────────
    prompt = build_claude_prompt(
        data_packet, output_path, period_start, period_end, report_date
    )

    # ── --dry-run mode ────────────────────────────────────────────────────
    if args.dry_run:
        print("\n" + "=" * 70)
        print("DRY RUN — Data packet that would be sent to Claude:")
        print("=" * 70)
        print(json.dumps(data_packet, indent=2, default=str))
        print("\n" + "=" * 70)
        print("Prompt that would be sent to Claude:")
        print("=" * 70)
        print(prompt)
        print("=" * 70 + "\n")
        logger.info("Dry run complete — Claude was not invoked.")
        return

    # ── Invoke Claude ─────────────────────────────────────────────────────
    success = invoke_claude(prompt)

    # ── Log result ────────────────────────────────────────────────────────
    _append_json_log({
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "action_type": "ceo_briefing",
        "result":      "success" if success else "error",
        "output_file": output_path.name,
        "period":      f"{period_start.isoformat()} to {period_end.isoformat()}",
        "summary":     (
            f"Monday CEO briefing generated for {report_date.isoformat()}"
            if success else
            "CEO briefing generation failed — see logs."
        ),
    })

    if success:
        logger.info("CEO briefing complete → %s", output_path.relative_to(VAULT))
    else:
        logger.error("CEO briefing failed — check logs above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
