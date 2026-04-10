"""
orchestrator.py — Bronze-tier AI Employee orchestrator.

Wakes up Claude Code, points it at the vault's skill library, and asks it to
process anything waiting in Needs_Action/.

Usage:
    python orchestrator.py               # run once
    python orchestrator.py --dry-run     # show prompt, skip claude call

Cron example (every 15 minutes):
    */15 * * * * /usr/bin/python3 /path/to/vault/orchestrator.py
"""

import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# The vault root is wherever this script lives.
VAULT = Path(__file__).resolve().parent

NEEDS_ACTION = VAULT / "Needs_Action"
SKILLS_DIR   = VAULT / "Skills"
LOGS_DIR     = VAULT / "Logs"


# ---------------------------------------------------------------------------
# Logging — console + daily .log file
# ---------------------------------------------------------------------------

def setup_logging() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    log_file = LOGS_DIR / f"watcher_{datetime.now().strftime('%Y-%m-%d')}.log"
    fmt = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"

    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    return logging.getLogger("orchestrator")


# ---------------------------------------------------------------------------
# JSON activity log
# ---------------------------------------------------------------------------

def append_json_log(entry: dict) -> None:
    """Append *entry* to today's Logs/YYYY-MM-DD.json activity log."""
    log_file = LOGS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.json"

    if log_file.exists():
        try:
            existing = json.loads(log_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Malformed file — write to a recovery log instead.
            recovery = LOGS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}_recovery.json"
            recovery.write_text(json.dumps([entry], indent=2), encoding="utf-8")
            return
        existing.append(entry)
        log_file.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    else:
        log_file.write_text(json.dumps([entry], indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_prompt(pending_files: list[Path]) -> str:
    """
    Construct the prompt that tells Claude exactly what to do.
    References skill files by path so Claude can read them directly.
    """
    file_list = "\n".join(f"  - {f.name}" for f in pending_files)

    # Point Claude at each relevant skill by its vault-relative path.
    reasoning_skill = "Skills/reasoning_loop/SKILL.md"
    dashboard_skill = "Skills/update_dashboard/SKILL.md"

    prompt = f"""You are the AI Employee for this vault. Your vault root is: {VAULT}

You have {len(pending_files)} file(s) waiting in Needs_Action/:
{file_list}

Follow these steps in order:

1. Read your skill instructions from:
   - {reasoning_skill}
   - {dashboard_skill}

2. Apply the "Reasoning Loop" skill to every file listed above.
   The skill defines six phases — work through all of them:

   PERCEIVE  — read and triage every item in Needs_Action/ and Pending_Approval/
   CONSULT   — load Company_Handbook.md rules and Business_Goals.md context
   REASON    — for each item, produce an explicit reasoning trace citing rule IDs
   PLAN      — write a self-contained Plan file for every item in Plans/
   ACT       — execute all full_auto plans immediately; queue the rest
   REPORT    — update Dashboard.md and write a cycle summary

3. Every Plan you write must pass the "cold-start test" defined in the skill:
   a fresh Claude instance must be able to execute it using only the plan file,
   Business_Goals.md, and access to the vault folders.

4. Print the structured cycle summary from Phase 6 of the skill.

Work autonomously. Cite handbook rule IDs in every routing decision.
Do not ask for clarification — apply the rules and use your best judgement
on anything not explicitly covered.
"""
    return prompt.strip()


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def run(dry_run: bool = False) -> None:
    logger = setup_logging()

    logger.info("Orchestrator starting — vault: %s", VAULT)

    # Step 1: Check for pending .md files in Needs_Action/.
    if not NEEDS_ACTION.exists():
        logger.info("Needs_Action/ does not exist — nothing to do.")
        return

    pending = sorted(NEEDS_ACTION.glob("*.md"))

    if not pending:
        logger.info("Needs_Action/ is empty — nothing to do.")
        append_json_log({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action_type": "orchestrator_run",
            "result": "no_action",
            "summary": "Needs_Action/ was empty — no Claude invocation needed.",
        })
        return

    logger.info("Found %d pending file(s):", len(pending))
    for f in pending:
        logger.info("  • %s", f.name)

    # Step 2: Build the prompt for Claude.
    prompt = build_prompt(pending)

    if dry_run:
        # Dry-run: print the prompt and stop — no Claude call made.
        print("\n" + "=" * 60)
        print("DRY RUN — prompt that would be sent to Claude:")
        print("=" * 60)
        print(prompt)
        print("=" * 60 + "\n")
        logger.info("Dry run complete — Claude was not invoked.")
        return

    # Step 3: Invoke Claude Code.
    logger.info("Invoking Claude Code…")
    cmd = ["claude", "--print", "--dangerously-skip-permissions", "-p", prompt]

    try:
        result = subprocess.run(
            cmd,
            cwd=str(VAULT),      # run from vault root so relative paths resolve
            capture_output=False, # let Claude's output stream to stdout in real time
            text=True,
        )
        success = result.returncode == 0
    except FileNotFoundError:
        logger.error(
            "claude CLI not found. Install Claude Code and ensure it is on PATH."
        )
        append_json_log({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action_type": "orchestrator_run",
            "result": "error",
            "summary": "claude CLI not found on PATH.",
        })
        sys.exit(1)

    # Step 4: Log what happened.
    status = "success" if success else "error"
    summary = (
        f"Claude processed {len(pending)} file(s) from Needs_Action/."
        if success
        else f"Claude exited with code {result.returncode}."
    )

    append_json_log({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action_type": "orchestrator_run",
        "result": status,
        "files_submitted": [f.name for f in pending],
        "summary": summary,
    })

    if success:
        logger.info("Claude finished successfully.")
    else:
        logger.error("Claude exited with code %d.", result.returncode)
        sys.exit(result.returncode)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AI Employee orchestrator — wake up Claude to process Needs_Action/."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the prompt Claude would receive without actually calling it.",
    )
    args = parser.parse_args()

    run(dry_run=args.dry_run)
