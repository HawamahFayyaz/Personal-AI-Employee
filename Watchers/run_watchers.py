"""
run_watchers.py — start all vault watchers.

Usage:
    python Watchers/run_watchers.py
"""

import logging
import sys
import threading
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve paths before any relative-import work
# ---------------------------------------------------------------------------
WATCHERS_DIR = Path(__file__).resolve().parent
VAULT_ROOT = WATCHERS_DIR.parent

# Add vault root to sys.path so "from Watchers.x import y" works when the
# script is invoked as  python Watchers/run_watchers.py  from any cwd.
if str(VAULT_ROOT) not in sys.path:
    sys.path.insert(0, str(VAULT_ROOT))

# ---------------------------------------------------------------------------
# Load .env from vault root (silent if missing)
# ---------------------------------------------------------------------------
from dotenv import load_dotenv  # noqa: E402  (after sys.path fix)

env_file = VAULT_ROOT / ".env"
loaded = load_dotenv(env_file)

# ---------------------------------------------------------------------------
# Logging: console + daily rotating log file
# ---------------------------------------------------------------------------
LOGS_DIR = VAULT_ROOT / "Logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

log_filename = LOGS_DIR / f"watcher_{datetime.now().strftime('%Y-%m-%d')}.log"

log_format = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
date_format = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    datefmt=date_format,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_filename, encoding="utf-8"),
    ],
)

logger = logging.getLogger("run_watchers")

# ---------------------------------------------------------------------------
# Import watchers (after logging is configured)
# ---------------------------------------------------------------------------
from Watchers.filesystem_watcher import FilesystemWatcher  # noqa: E402

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print()
    print("=" * 60)
    print("  AI Employee Vault — Watcher Runner")
    print("=" * 60)
    print(f"  Vault root : {VAULT_ROOT}")
    print(f"  Env file   : {env_file}  ({'loaded' if loaded else 'not found'})")
    print(f"  Log file   : {log_filename}")
    print("=" * 60)
    print()

    logger.info("Vault root  : %s", VAULT_ROOT)
    logger.info("Env file    : %s (%s)", env_file, "loaded" if loaded else "not found")
    logger.info("Log file    : %s", log_filename)

    # -----------------------------------------------------------------------
    # Instantiate watchers
    # -----------------------------------------------------------------------
    fs_watcher = FilesystemWatcher(vault_path=str(VAULT_ROOT), check_interval=60)

    watchers = [fs_watcher]

    # -----------------------------------------------------------------------
    # Start each watcher in its own daemon thread
    # -----------------------------------------------------------------------
    threads = []
    for w in watchers:
        t = threading.Thread(target=w.run, name=w.__class__.__name__, daemon=True)
        t.start()
        threads.append((w, t))
        logger.info("Started watcher thread: %s", w.__class__.__name__)

    print()
    print("  Monitoring:")
    print(f"    Inbox/  →  {VAULT_ROOT / 'Inbox'}")
    print()
    print("  Press Ctrl+C to stop.\n")

    # -----------------------------------------------------------------------
    # Block main thread; handle graceful shutdown on Ctrl+C
    # -----------------------------------------------------------------------
    try:
        while True:
            # Keep main thread alive; join with a timeout so KeyboardInterrupt
            # is processed promptly even on Windows/WSL.
            for _, t in threads:
                t.join(timeout=1)
    except KeyboardInterrupt:
        print()
        logger.info("Shutdown requested — stopping watchers…")

        for w, _ in threads:
            try:
                w._observer.stop()
            except AttributeError:
                pass  # watcher has no Observer (e.g. poll-based)

        for _, t in threads:
            t.join(timeout=5)

        logger.info("All watchers stopped. Goodbye.")
        print()


if __name__ == "__main__":
    main()
