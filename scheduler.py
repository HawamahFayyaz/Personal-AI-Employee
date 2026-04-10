"""
scheduler.py — AI Employee Vault task scheduler.

Reads Config/schedule.md, registers each enabled task with the `schedule`
library, and runs the poll loop.  Claude Code tasks are invoked via
subprocess (same pattern as orchestrator.py); Python module tasks run the
module directly.

Usage:
    python scheduler.py                  # start scheduler (blocking)
    python scheduler.py --list           # print schedule and next run times, then exit
    python scheduler.py --run-now <name> # run one task immediately, then exit
    python scheduler.py --dry-run        # start loop but only log; no real calls

Cron alternative (see Config/schedule.md for full crontab snippet):
    0 8 * * *     python scheduler.py --run-now daily_dashboard_update
    0 9 * * 1,3,5 python -m Watchers.linkedin_poster generate
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import schedule
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
VAULT      = Path(__file__).resolve().parent
CONFIG     = VAULT / "Config" / "schedule.md"
LOGS_DIR   = VAULT / "Logs"
SKILLS_DIR = VAULT / "Skills"

LOGS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging — console + daily file (shared with watchers)
# ---------------------------------------------------------------------------
_log_file = LOGS_DIR / f"watcher_{datetime.now().strftime('%Y-%m-%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)-20s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_log_file, encoding="utf-8"),
    ],
)
logger = logging.getLogger("scheduler")

# ---------------------------------------------------------------------------
# Load .env
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv(VAULT / ".env")
except ImportError:
    pass

_DRY_RUN_ENV = __import__("os").getenv("DRY_RUN", "false").strip().lower() in (
    "1", "true", "yes"
)

# ---------------------------------------------------------------------------
# Schedule config parser
# ---------------------------------------------------------------------------
_FM_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)

_DAY_ALIASES: dict[str, list[str]] = {
    "weekdays": ["monday", "tuesday", "wednesday", "thursday", "friday"],
    "weekends": ["saturday", "sunday"],
}

_VALID_DAYS = {
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
}


def load_config(path: Path) -> list[dict[str, Any]]:
    """Parse YAML frontmatter from *path* and return the task list."""
    if not path.exists():
        logger.error("Schedule config not found: %s", path)
        return []

    text = path.read_text(encoding="utf-8")
    m = _FM_RE.match(text)
    if not m:
        logger.error("No YAML frontmatter found in %s", path)
        return []

    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        logger.error("YAML parse error in %s: %s", path, exc)
        return []

    tasks = fm.get("tasks", [])
    if not isinstance(tasks, list):
        logger.error("'tasks' in %s must be a list", path)
        return []

    return tasks


def expand_days(schedule_str: str) -> list[str]:
    """Expand a schedule string into a list of lowercase day names.

    Examples:
        "daily"                     → ["daily"]
        "monday"                    → ["monday"]
        "monday,wednesday,friday"   → ["monday", "wednesday", "friday"]
        "weekdays"                  → ["monday","tuesday","wednesday","thursday","friday"]
    """
    raw = schedule_str.strip().lower()

    if raw == "daily":
        return ["daily"]

    if raw in _DAY_ALIASES:
        return _DAY_ALIASES[raw]

    # Comma-separated list of days
    parts = [p.strip() for p in raw.split(",")]
    result = []
    for p in parts:
        if p in _VALID_DAYS:
            result.append(p)
        elif p in _DAY_ALIASES:
            result.extend(_DAY_ALIASES[p])
        else:
            logger.warning("Unknown schedule token '%s' — skipped.", p)
    return result


# ---------------------------------------------------------------------------
# Built-in Claude prompts
# ---------------------------------------------------------------------------

def _prompt_dashboard_update() -> str:
    return (
        f"You are the AI Employee for this vault. Your vault root is: {VAULT}\n\n"
        "Read your skill instructions from: Skills/update_dashboard/SKILL.md\n\n"
        "Apply the 'Update Dashboard' skill in full — work through all phases and "
        "rewrite Dashboard.md in the canonical format.\n\n"
        "Work autonomously. Print the dashboard summary when done."
    )


def _prompt_process_inbox() -> str:
    return (
        f"You are the AI Employee for this vault. Your vault root is: {VAULT}\n\n"
        "Read your skill instructions from:\n"
        "  - Skills/reasoning_loop/SKILL.md\n"
        "  - Skills/update_dashboard/SKILL.md\n\n"
        "Apply the Reasoning Loop skill to every file in Needs_Action/. "
        "Work through all six phases (PERCEIVE → CONSULT → REASON → PLAN → ACT → REPORT). "
        "Update Dashboard.md when done.\n\n"
        "Work autonomously. Cite handbook rule IDs in every routing decision."
    )


_PROMPTS: dict[str, Any] = {
    "dashboard_update": _prompt_dashboard_update,
    "process_inbox":    _prompt_process_inbox,
}

# ---------------------------------------------------------------------------
# JSON activity log
# ---------------------------------------------------------------------------

def _append_log(entry: dict) -> None:
    log_file = LOGS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.json"
    if log_file.exists():
        try:
            existing = json.loads(log_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            recovery = LOGS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}_recovery.json"
            recovery.write_text(json.dumps([entry], indent=2), encoding="utf-8")
            return
        existing.append(entry)
        log_file.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    else:
        log_file.write_text(json.dumps([entry], indent=2), encoding="utf-8")

# ---------------------------------------------------------------------------
# Task runners
# ---------------------------------------------------------------------------

def _run_claude_task(task: dict, dry_run: bool) -> None:
    """Invoke Claude Code CLI with the prompt for this task."""
    prompt_key = task.get("prompt_key", "")
    prompt_fn  = _PROMPTS.get(prompt_key)

    if prompt_fn is None:
        logger.error(
            "Task '%s': unknown prompt_key '%s'. "
            "Valid keys: %s",
            task["name"], prompt_key, list(_PROMPTS),
        )
        _append_log({
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "action_type": "scheduler_task",
            "task":        task["name"],
            "result":      "error",
            "summary":     f"Unknown prompt_key '{prompt_key}'",
        })
        return

    prompt = prompt_fn()

    if dry_run:
        logger.info(
            "[DRY RUN] Task '%s' — would invoke claude with prompt_key='%s'",
            task["name"], prompt_key,
        )
        _append_log({
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "action_type": "scheduler_task",
            "task":        task["name"],
            "result":      "dry_run",
            "summary":     f"DRY RUN — claude would run with prompt_key={prompt_key}",
        })
        return

    logger.info("Task '%s' starting — invoking Claude Code…", task["name"])
    cmd = ["claude", "--print", "--dangerously-skip-permissions", "-p", prompt]

    try:
        result = subprocess.run(cmd, cwd=str(VAULT), text=True)
        success = result.returncode == 0
    except FileNotFoundError:
        logger.error(
            "Task '%s': 'claude' CLI not found on PATH. "
            "Install Claude Code: https://claude.ai/download",
            task["name"],
        )
        _append_log({
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "action_type": "scheduler_task",
            "task":        task["name"],
            "result":      "error",
            "summary":     "claude CLI not found on PATH",
        })
        return

    status  = "success" if success else "error"
    summary = (
        f"Claude completed task '{task['name']}'."
        if success
        else f"Claude exited with code {result.returncode} for task '{task['name']}'."
    )
    logger.info("Task '%s' finished — %s", task["name"], status)
    _append_log({
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "action_type": "scheduler_task",
        "task":        task["name"],
        "result":      status,
        "summary":     summary,
    })


def _run_python_task(task: dict, dry_run: bool) -> None:
    """Run a Python module task via subprocess."""
    module = task.get("module", "")
    args   = task.get("args", "")

    if not module:
        logger.error("Task '%s': 'module' field is required for command: python", task["name"])
        return

    # args can be a string ("generate") or a list (["generate", "--style", "brief"])
    if isinstance(args, list):
        arg_parts = [str(a) for a in args]
    elif args:
        arg_parts = str(args).split()
    else:
        arg_parts = []

    cmd = [sys.executable, "-m", module] + arg_parts

    if dry_run:
        logger.info(
            "[DRY RUN] Task '%s' — would run: %s",
            task["name"], " ".join(cmd),
        )
        _append_log({
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "action_type": "scheduler_task",
            "task":        task["name"],
            "result":      "dry_run",
            "summary":     f"DRY RUN — would run: {' '.join(cmd)}",
        })
        return

    logger.info("Task '%s' starting — %s", task["name"], " ".join(cmd))

    try:
        result = subprocess.run(cmd, cwd=str(VAULT), text=True)
        success = result.returncode == 0
    except FileNotFoundError as exc:
        logger.error("Task '%s' failed: %s", task["name"], exc)
        _append_log({
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "action_type": "scheduler_task",
            "task":        task["name"],
            "result":      "error",
            "summary":     str(exc),
        })
        return

    status  = "success" if success else "error"
    summary = (
        f"Task '{task['name']}' completed." if success
        else f"Task '{task['name']}' exited with code {result.returncode}."
    )
    logger.info("Task '%s' finished — %s", task["name"], status)
    _append_log({
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "action_type": "scheduler_task",
        "task":        task["name"],
        "result":      status,
        "summary":     summary,
    })


_COMMAND_RUNNERS = {
    "claude": _run_claude_task,
    "python": _run_python_task,
}

# ---------------------------------------------------------------------------
# Overlap guard — wraps a task function so concurrent triggers are skipped
# ---------------------------------------------------------------------------

def _make_threaded_job(task: dict, dry_run: bool):
    """Return a schedule-compatible callable that runs *task* in a daemon thread.

    A per-task Semaphore(1) prevents overlap: if the previous run is still in
    progress when the next trigger fires, the new trigger is logged and skipped.
    """
    sem     = threading.Semaphore(1)
    command = task.get("command", "")
    runner  = _COMMAND_RUNNERS.get(command)

    if runner is None:
        logger.error(
            "Task '%s': unknown command '%s'. Valid: %s",
            task["name"], command, list(_COMMAND_RUNNERS),
        )
        return lambda: None  # no-op

    def _body():
        try:
            runner(task, dry_run)
        finally:
            sem.release()

    def _trigger():
        acquired = sem.acquire(blocking=False)
        if not acquired:
            logger.warning(
                "Task '%s' is still running from the previous trigger — skipping.",
                task["name"],
            )
            return
        t = threading.Thread(target=_body, name=f"task:{task['name']}", daemon=True)
        t.start()

    return _trigger

# ---------------------------------------------------------------------------
# Schedule registration
# ---------------------------------------------------------------------------

def register_tasks(tasks: list[dict], dry_run: bool) -> int:
    """Register all enabled tasks with the `schedule` library.

    Returns the number of jobs registered.
    """
    registered = 0

    for task in tasks:
        if not task.get("enabled", True):
            logger.info("Task '%s' is disabled — skipped.", task.get("name", "?"))
            continue

        name     = task.get("name", f"task_{registered}")
        sched    = str(task.get("schedule", "daily")).strip()
        run_time = str(task.get("time", "08:00")).strip()

        # Validate time format
        if not re.match(r"^\d{2}:\d{2}$", run_time):
            logger.error(
                "Task '%s': invalid time '%s' — must be HH:MM. Skipping.",
                name, run_time,
            )
            continue

        days = expand_days(sched)
        if not days:
            logger.error("Task '%s': empty schedule after expansion — skipped.", name)
            continue

        job_fn = _make_threaded_job(task, dry_run)

        for day in days:
            if day == "daily":
                job = schedule.every().day.at(run_time).do(job_fn)
            else:
                day_scheduler = getattr(schedule.every(), day, None)
                if day_scheduler is None:
                    logger.error("Task '%s': unrecognised day '%s'.", name, day)
                    continue
                job = day_scheduler.at(run_time).do(job_fn)

            job.tag(name)  # tag so we can find jobs by task name later
            registered += 1
            logger.info(
                "Registered: %-30s  %s at %s UTC",
                f"'{name}'",
                day.capitalize() if day != "daily" else "Daily",
                run_time,
            )

    return registered

# ---------------------------------------------------------------------------
# CLI actions: --list and --run-now
# ---------------------------------------------------------------------------

def print_schedule(tasks: list[dict]) -> None:
    """Print a human-readable table of the configured schedule."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\nSchedule as of {now}")
    print(f"Config : {CONFIG}")
    print()
    print(f"  {'NAME':<32}  {'SCHEDULE':<28}  {'TIME':>5}  {'CMD':<10}  STATUS")
    print("  " + "─" * 90)

    for task in tasks:
        name    = task.get("name", "?")
        sched   = task.get("schedule", "?")
        t       = task.get("time", "?")
        cmd     = task.get("command", "?")
        enabled = task.get("enabled", True)
        status  = "enabled" if enabled else "DISABLED"
        print(f"  {name:<32}  {sched:<28}  {t:>5}  {cmd:<10}  {status}")

    # Show next run times if the library has any jobs registered
    jobs = schedule.get_jobs()
    if jobs:
        print()
        print("  Next scheduled runs:")
        seen: set[str] = set()
        for job in sorted(jobs, key=lambda j: j.next_run or datetime.max):
            tags = list(job.tags)
            tag  = tags[0] if tags else "?"
            if tag in seen:
                continue
            seen.add(tag)
            next_run = job.next_run.strftime("%Y-%m-%d %H:%M UTC") if job.next_run else "unknown"
            print(f"    {tag:<32}  →  {next_run}")

    print()


def run_task_now(task_name: str, tasks: list[dict], dry_run: bool) -> None:
    """Find *task_name* in *tasks* and run it immediately in the foreground."""
    matches = [t for t in tasks if t.get("name") == task_name]

    if not matches:
        logger.error(
            "No task named '%s'. Available: %s",
            task_name, [t.get("name") for t in tasks],
        )
        sys.exit(1)

    task    = matches[0]
    command = task.get("command", "")
    runner  = _COMMAND_RUNNERS.get(command)

    if runner is None:
        logger.error("Unknown command '%s' for task '%s'.", command, task_name)
        sys.exit(1)

    logger.info("Running task '%s' immediately (foreground)…", task_name)
    runner(task, dry_run)

# ---------------------------------------------------------------------------
# Startup banner
# ---------------------------------------------------------------------------

def print_banner(tasks: list[dict], dry_run: bool, n_jobs: int) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d  %H:%M:%S UTC")
    W   = 60

    print()
    print("┌" + "─" * W + "┐")
    print(f"│{'AI Employee Vault — Scheduler':^{W}}│")
    print(f"│{('Started: ' + now):^{W}}│")
    print("├" + "─" * W + "┤")
    print(f"│{'  SCHEDULE':<{W}}│")
    print(f"│{' ':<{W}}│")

    for task in tasks:
        if not task.get("enabled", True):
            continue
        name  = task.get("name", "?")
        sched = task.get("schedule", "?")
        t     = task.get("time", "?")
        cmd   = task.get("command", "?")
        line  = f"  ✓  {name}"
        sub   = f"       {sched} at {t} UTC  [{cmd}]"
        if len(line) > W:
            line = line[:W - 1] + "…"
        if len(sub) > W:
            sub = sub[:W - 1] + "…"
        print(f"│{line:<{W}}│")
        print(f"│{sub:<{W}}│")
        print(f"│{' ':<{W}}│")

    print("├" + "─" * W + "┤")
    print(f"│{'  CONFIGURATION':<{W}}│")
    print(f"│{' ':<{W}}│")
    dry_label = "true  (no external calls)" if dry_run else "false"
    print(f"│{'  Config   : Config/schedule.md':<{W}}│")
    print(f"│{'  Log file : Logs/watcher-' + datetime.now().strftime('%Y-%m-%d') + '.log':<{W}}│")
    print(f"│{'  DRY_RUN  : ' + dry_label:<{W}}│")
    print(f"│{'  Jobs reg : ' + str(n_jobs):<{W}}│")
    print(f"│{' ':<{W}}│")
    print("├" + "─" * W + "┤")
    print(f"│{'  Press Ctrl+C to stop':<{W}}│")
    print("└" + "─" * W + "┘")
    print()

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI Employee Vault scheduler — run recurring tasks via the schedule library."
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the configured schedule and next run times, then exit.",
    )
    parser.add_argument(
        "--run-now",
        metavar="TASK_NAME",
        help="Run a named task immediately (foreground) and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Start the scheduler but only log what would happen — no real Claude/API calls.",
    )
    args = parser.parse_args()

    dry_run = args.dry_run or _DRY_RUN_ENV

    tasks = load_config(CONFIG)
    if not tasks:
        logger.error("No tasks loaded from %s — exiting.", CONFIG)
        sys.exit(1)

    # --list: register to get next_run times, print, exit
    if args.list:
        register_tasks(tasks, dry_run=True)
        print_schedule(tasks)
        return

    # --run-now: execute one task immediately, exit
    if args.run_now:
        run_task_now(args.run_now, tasks, dry_run=dry_run)
        return

    # Normal mode: register all tasks and start poll loop
    n_jobs = register_tasks(tasks, dry_run=dry_run)
    if n_jobs == 0:
        logger.error("No jobs registered — check Config/schedule.md. Exiting.")
        sys.exit(1)

    print_banner(tasks, dry_run=dry_run, n_jobs=n_jobs)

    _append_log({
        "timestamp":    datetime.now(timezone.utc).isoformat(),
        "action_type":  "scheduler_start",
        "jobs_registered": n_jobs,
        "dry_run":      dry_run,
        "summary":      f"Scheduler started with {n_jobs} job(s).",
    })

    logger.info("Scheduler running — poll interval 30 s. Press Ctrl+C to stop.")

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("Scheduler stopped.")
        _append_log({
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "action_type": "scheduler_stop",
            "summary":     "Scheduler stopped by user (KeyboardInterrupt).",
        })
        print()


if __name__ == "__main__":
    main()
