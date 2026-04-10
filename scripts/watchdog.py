"""
watchdog.py — Process watchdog for AI Employee vault services.

Monitors all running watchers and MCP servers, auto-restarts crashes,
alerts after 3 consecutive crashes, and updates Logs/health_status.json
every 60 seconds.

Usage:
    python scripts/watchdog.py
    python scripts/watchdog.py --dry-run   # log restarts, don't actually restart

Managed processes (defined in WATCHED_PROCS):
    FilesystemWatcher  — Watchers/run_watchers.py (all watchers in one process)
    Scheduler          — scheduler.py

The watchdog is intentionally separate from run_watchers.py so it can
restart the entire watcher process if it crashes.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
VAULT_ROOT   = _SCRIPTS_DIR.parent

if str(VAULT_ROOT) not in sys.path:
    sys.path.insert(0, str(VAULT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(VAULT_ROOT / ".env")
except ImportError:
    pass

LOGS_DIR    = VAULT_ROOT / "Logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

_today   = datetime.now().strftime("%Y-%m-%d")
LOG_FILE = LOGS_DIR / f"watchdog_{_today}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)-20s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger("watchdog")

HEALTH_FILE      = LOGS_DIR / "health_status.json"
ALERTS_FILE      = LOGS_DIR / "alerts.json"
MAX_CRASHES      = 3          # alert human after this many consecutive crashes
HEALTH_INTERVAL  = 60         # seconds between health file updates
RESTART_COOLDOWN = 5          # seconds to wait before restarting a crashed process


# ---------------------------------------------------------------------------
# Process definitions
# ---------------------------------------------------------------------------

@dataclass
class ManagedProcess:
    name:        str
    cmd:         list[str]                          # argv to launch
    proc:        Optional[subprocess.Popen] = None
    crash_count: int                         = 0
    alerted:     bool                        = False   # human already notified?
    last_start:  Optional[datetime]          = None
    last_crash:  Optional[datetime]          = None
    last_activity: Optional[datetime]        = None
    error_count: int                         = 0
    _lock:       threading.Lock              = field(default_factory=threading.Lock, repr=False)

    @property
    def pid(self) -> Optional[int]:
        return self.proc.pid if self.proc else None

    @property
    def is_running(self) -> bool:
        if self.proc is None:
            return False
        return self.proc.poll() is None

    def status(self) -> str:
        if self.is_running:
            return "running"
        if self.proc is None:
            return "not_started"
        return "crashed"


def _build_proc_list() -> list[ManagedProcess]:
    py = sys.executable
    return [
        ManagedProcess(
            name="WatcherRunner",
            cmd=[py, str(VAULT_ROOT / "Watchers" / "run_watchers.py")],
        ),
        ManagedProcess(
            name="Scheduler",
            cmd=[py, str(VAULT_ROOT / "scheduler.py")],
        ),
    ]


# ---------------------------------------------------------------------------
# Alert helper
# ---------------------------------------------------------------------------

def _write_alert(kind: str, message: str, context: dict | None = None) -> None:
    alerts: list[dict] = []
    if ALERTS_FILE.exists():
        try:
            alerts = json.loads(ALERTS_FILE.read_text())
        except json.JSONDecodeError:
            alerts = []
    alerts.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source":    "watchdog",
        "kind":      kind,
        "message":   message,
        "context":   context or {},
    })
    ALERTS_FILE.write_text(json.dumps(alerts, indent=2))
    logger.warning("[ALERT:%s] %s", kind, message)


# ---------------------------------------------------------------------------
# Health status writer
# ---------------------------------------------------------------------------

def _write_health(procs: list[ManagedProcess]) -> None:
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "processes": {},
        "summary": {
            "total":   len(procs),
            "running": sum(1 for p in procs if p.is_running),
            "crashed": sum(1 for p in procs if not p.is_running and p.proc is not None),
        },
    }
    for p in procs:
        payload["processes"][p.name] = {
            "status":        p.status(),
            "pid":           p.pid,
            "crash_count":   p.crash_count,
            "error_count":   p.error_count,
            "alerted":       p.alerted,
            "last_start":    p.last_start.isoformat()    if p.last_start    else None,
            "last_crash":    p.last_crash.isoformat()    if p.last_crash    else None,
            "last_activity": p.last_activity.isoformat() if p.last_activity else None,
        }
    HEALTH_FILE.write_text(json.dumps(payload, indent=2))


def _health_loop(procs: list[ManagedProcess], stop: threading.Event) -> None:
    """Background thread: update health_status.json every HEALTH_INTERVAL seconds."""
    while not stop.wait(timeout=HEALTH_INTERVAL):
        try:
            _write_health(procs)
            logger.debug("Health status updated.")
        except Exception as exc:
            logger.error("Failed to write health status: %s", exc)


# ---------------------------------------------------------------------------
# Restart logic
# ---------------------------------------------------------------------------

def _start_process(p: ManagedProcess, dry_run: bool) -> None:
    """Launch (or relaunch) a managed process."""
    if dry_run:
        logger.info("[DRY-RUN] Would start: %s  cmd=%s", p.name, p.cmd)
        p.last_start = datetime.now(timezone.utc)
        return

    log_path = LOGS_DIR / f"{p.name.lower()}_{datetime.now().strftime('%Y-%m-%d')}.proc.log"
    log_fh   = open(log_path, "a", encoding="utf-8")

    proc = subprocess.Popen(
        p.cmd,
        stdout=log_fh,
        stderr=log_fh,
        cwd=str(VAULT_ROOT),
        env=os.environ.copy(),
    )
    p.proc       = proc
    p.last_start = datetime.now(timezone.utc)
    logger.info("Started %s  pid=%d  log=%s", p.name, proc.pid, log_path.name)


def _monitor_process(p: ManagedProcess, dry_run: bool, stop: threading.Event) -> None:
    """Thread that watches one process and restarts it on crash."""
    # Initial start
    _start_process(p, dry_run)

    while not stop.is_set():
        time.sleep(RESTART_COOLDOWN)

        if stop.is_set():
            break

        if p.is_running:
            p.last_activity = datetime.now(timezone.utc)
            continue

        # Process has exited
        exit_code = p.proc.returncode if p.proc else -1
        p.last_crash  = datetime.now(timezone.utc)
        p.crash_count += 1
        p.error_count += 1

        logger.error(
            "%s crashed (exit_code=%s, consecutive_crashes=%d)",
            p.name, exit_code, p.crash_count,
        )

        # Log restart
        restart_log = LOGS_DIR / "restart_log.json"
        restarts: list[dict] = []
        if restart_log.exists():
            try:
                restarts = json.loads(restart_log.read_text())
            except json.JSONDecodeError:
                restarts = []
        restarts.append({
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "process":     p.name,
            "exit_code":   exit_code,
            "crash_count": p.crash_count,
        })
        restart_log.write_text(json.dumps(restarts, indent=2))

        # Alert after MAX_CRASHES consecutive crashes
        if p.crash_count >= MAX_CRASHES and not p.alerted:
            _write_alert(
                "repeated_crashes",
                f"{p.name} has crashed {p.crash_count} consecutive times — human review required",
                {"process": p.name, "crash_count": p.crash_count, "exit_code": exit_code},
            )
            p.alerted = True
            logger.critical(
                "HUMAN ALERT: %s crashed %d times — automatic restarts will continue",
                p.name, p.crash_count,
            )

        if not stop.is_set():
            logger.info("Restarting %s in %ds…", p.name, RESTART_COOLDOWN)
            time.sleep(RESTART_COOLDOWN)
            _start_process(p, dry_run)
            # Reset consecutive crash count on successful restart
            if p.is_running:
                logger.info("%s restarted successfully (pid=%d)", p.name, p.pid or -1)
                p.crash_count = 0
                p.alerted     = False


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------

def _shutdown(procs: list[ManagedProcess], stop: threading.Event) -> None:
    logger.info("Watchdog shutting down — terminating managed processes…")
    stop.set()

    for p in procs:
        if p.is_running and p.proc:
            try:
                p.proc.terminate()
                p.proc.wait(timeout=5)
                logger.info("Terminated %s", p.name)
            except Exception as exc:
                logger.warning("Could not terminate %s: %s", p.name, exc)

    logger.info("Watchdog shutdown complete.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="AI Employee Watchdog")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log restarts without launching real processes")
    args = parser.parse_args()

    dry_run = args.dry_run or os.getenv("DRY_RUN", "").strip().lower() in ("1", "true", "yes")

    procs    = _build_proc_list()
    stop_evt = threading.Event()

    # Write initial health file
    _write_health(procs)

    # Start per-process monitor threads
    monitor_threads: list[threading.Thread] = []
    for p in procs:
        t = threading.Thread(
            target=_monitor_process,
            args=(p, dry_run, stop_evt),
            name=f"monitor_{p.name}",
            daemon=True,
        )
        t.start()
        monitor_threads.append(t)
        logger.info("Monitoring: %s", p.name)

    # Start health writer thread
    health_thread = threading.Thread(
        target=_health_loop,
        args=(procs, stop_evt),
        name="HealthWriter",
        daemon=True,
    )
    health_thread.start()

    logger.info(
        "Watchdog active — managing %d processes.  Health: %s",
        len(procs), HEALTH_FILE.name,
    )

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        _shutdown(procs, stop_evt)


if __name__ == "__main__":
    main()
