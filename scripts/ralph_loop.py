"""
scripts/ralph_loop.py — Ralph Wiggum Loop

A persistent execution loop that keeps invoking Claude until a task is
declared complete or a safety limit is reached.

Named after Ralph Wiggum's stubborn optimism: "I'm still trying!"

Usage:
    python scripts/ralph_loop.py "Process all files in Needs_Action and create plans"
    python scripts/ralph_loop.py "..." --max-iterations 5
    python scripts/ralph_loop.py "..." --iteration-timeout 120 --total-timeout 600
    python scripts/ralph_loop.py "..." --dry-run

Emergency stop:
    touch STOP_RALPH          # in the vault root — halts the loop cleanly
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

VAULT = Path(__file__).resolve().parent.parent

ACTIVE_PROJECTS_DIR = VAULT / "Active_Projects"
DONE_DIR            = VAULT / "Done"
LOGS_DIR            = VAULT / "Logs"
DASHBOARD           = VAULT / "Dashboard.md"
STOP_FILE           = VAULT / "STOP_RALPH"

COMPLETION_PROMISE      = "TASK_COMPLETE"
DEFAULT_MAX_ITERATIONS  = 10
DEFAULT_ITER_TIMEOUT    = 300    # seconds per Claude invocation
DEFAULT_TOTAL_TIMEOUT   = 1800   # seconds for the whole loop


# ---------------------------------------------------------------------------
# Task file helpers
# ---------------------------------------------------------------------------

def make_task_id() -> str:
    ts  = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    uid = uuid.uuid4().hex[:6].upper()
    return f"{ts}_{uid}"


def create_task_file(task_id: str, description: str, max_iterations: int) -> Path:
    ACTIVE_PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ACTIVE_PROJECTS_DIR / f"TASK_{task_id}.md"
    now  = datetime.now(timezone.utc).isoformat()
    content = (
        f"---\n"
        f"task: \"{description}\"\n"
        f"status: in_progress\n"
        f"created: {now}\n"
        f"max_iterations: {max_iterations}\n"
        f"current_iteration: 0\n"
        f"completion_promise: \"{COMPLETION_PROMISE}\"\n"
        f"---\n\n"
        f"# Task: {description}\n\n"
        f"**Status:** In Progress  \n"
        f"**Created:** {now}  \n"
        f"**Task ID:** {task_id}\n\n"
        f"## Iteration Log\n\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def _update_frontmatter_field(content: str, field: str, value: str) -> str:
    """Replace a YAML frontmatter field value in-place."""
    return re.sub(
        rf"^({re.escape(field)}:\s*).*$",
        rf"\g<1>{value}",
        content,
        flags=re.MULTILINE,
    )


def update_task_file(path: Path, iteration: int, note: str = "") -> None:
    if not path.exists():
        return
    content = path.read_text(encoding="utf-8")
    content = _update_frontmatter_field(content, "current_iteration", str(iteration))
    if note:
        content += f"\n### Iteration {iteration}\n{note}\n"
    path.write_text(content, encoding="utf-8")


def mark_task_done(task_path: Path, outcome: str, summary: str) -> Path:
    """Set final status in frontmatter, append outcome block, move to Done/."""
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    content = task_path.read_text(encoding="utf-8")
    content = _update_frontmatter_field(content, "status", outcome)
    now = datetime.now(timezone.utc).isoformat()
    content += (
        f"\n## Final Outcome\n\n"
        f"**Status:** {outcome}  \n"
        f"**Completed:** {now}  \n"
        f"**Summary:** {summary}\n"
    )
    done_path = DONE_DIR / task_path.name
    done_path.write_text(content, encoding="utf-8")
    task_path.unlink()
    return done_path


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def build_initial_prompt(task_id: str, description: str, task_path: Path) -> str:
    return f"""You are the AI Employee for this vault. Your vault root is: {VAULT}

## Task
{description}

## Your Instructions
- Work autonomously on this task. Do not ask for clarification.
- Read Company_Handbook.md and Business_Goals.md for context and rules.
- Use any Skills in Skills/ that are relevant.
- When you are completely and fully finished with ALL work, you MUST print this
  exact text on its own line:
  {COMPLETION_PROMISE}
- You may optionally move your task state file to Done/ when complete, but
  printing {COMPLETION_PROMISE} is required regardless.
- Your task state file is: {task_path}
  Update it with progress notes as you work.

## Task ID
{task_id}

Begin working on the task now. Be thorough and complete.
"""


def build_continuation_prompt(
    task_id: str,
    description: str,
    task_path: Path,
    iteration: int,
    max_iterations: int,
    prior_output: str,
) -> str:
    remaining = max_iterations - iteration
    # Truncate prior output to avoid very long prompts
    prior_snippet = prior_output.strip()[-3000:] if prior_output.strip() else "(no output captured)"

    return f"""You are the AI Employee for this vault. Your vault root is: {VAULT}

## Task (Iteration {iteration} of {max_iterations} — {remaining} remaining)
{description}

## What Happened in the Previous Iteration
The previous run did NOT complete the task (it did not print {COMPLETION_PROMISE}).
Last output (tail):
---
{prior_snippet}
---

## Your Instructions
- Review your task state file for progress notes: {task_path}
- Determine what was already done and what still needs to be done.
- Complete ALL remaining work now.
- Do not repeat work that was already successfully completed.
- When you are completely and fully finished, print this exact text on its own line:
  {COMPLETION_PROMISE}

## Task ID
{task_id}

Continue and finish the task now. You have {remaining} iteration(s) left.
"""


# ---------------------------------------------------------------------------
# Claude invocation
# ---------------------------------------------------------------------------

def invoke_claude(prompt: str, timeout: float) -> tuple[str, int]:
    """
    Run `claude --print --dangerously-skip-permissions -p <prompt>`.
    Returns (stdout+stderr, returncode).
    returncode -1 = timeout, -2 = claude not found.
    """
    try:
        result = subprocess.run(
            ["claude", "--print", "--dangerously-skip-permissions", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(VAULT),
        )
        return (result.stdout or "") + (result.stderr or ""), result.returncode
    except subprocess.TimeoutExpired:
        return f"(TIMEOUT after {timeout:.0f}s)", -1
    except FileNotFoundError:
        return "(ERROR: 'claude' CLI not found on PATH)", -2


# ---------------------------------------------------------------------------
# Completion checks
# ---------------------------------------------------------------------------

def output_has_promise(output: str) -> bool:
    return COMPLETION_PROMISE in output


def task_moved_to_done(task_id: str) -> bool:
    """Return True if Claude itself moved the task file to Done/."""
    return (DONE_DIR / f"TASK_{task_id}.md").exists()


# ---------------------------------------------------------------------------
# Logging & Dashboard
# ---------------------------------------------------------------------------

def append_json_log(entry: dict) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
    entries: list = []
    if log_path.exists():
        try:
            entries = json.loads(log_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    entries.append(entry)
    log_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def update_dashboard(task_id: str, description: str, outcome: str) -> None:
    if not DASHBOARD.exists():
        return
    content  = DASHBOARD.read_text(encoding="utf-8")
    now      = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    icon     = "✓" if outcome == "completed" else "⚠"
    short_desc = description[:70] + ("…" if len(description) > 70 else "")
    entry    = f"- `{now}` — [Ralph Loop] {icon} `{task_id}` {outcome}: {short_desc}\n"

    if "## Recent Activity" in content:
        content = content.replace(
            "## Recent Activity\n",
            f"## Recent Activity\n{entry}",
            1,
        )
        DASHBOARD.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ralph Wiggum Loop — keeps invoking Claude until the task is done.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("task", help="Task description for Claude")
    parser.add_argument(
        "--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS,
        metavar="N", help=f"Max Claude invocations (default: {DEFAULT_MAX_ITERATIONS})",
    )
    parser.add_argument(
        "--iteration-timeout", type=int, default=DEFAULT_ITER_TIMEOUT,
        metavar="SECS", help=f"Seconds per Claude invocation (default: {DEFAULT_ITER_TIMEOUT})",
    )
    parser.add_argument(
        "--total-timeout", type=int, default=DEFAULT_TOTAL_TIMEOUT,
        metavar="SECS", help=f"Total wall-clock seconds (default: {DEFAULT_TOTAL_TIMEOUT})",
    )
    parser.add_argument(
        "--id", default=None, metavar="ID",
        help="Custom task ID (default: auto-generated timestamp+uuid)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print prompts and simulate the loop without invoking Claude",
    )
    args = parser.parse_args()

    task_id    = args.id or make_task_id()
    description = args.task
    max_iter   = args.max_iterations
    iter_to    = args.iteration_timeout
    total_to   = args.total_timeout
    dry_run    = args.dry_run

    print(f"Ralph Loop starting | task_id={task_id} | max_iterations={max_iter}")
    if dry_run:
        print("DRY RUN mode — Claude will NOT be invoked")
    print(f"Task: {description}\n")

    task_path = create_task_file(task_id, description, max_iter)
    print(f"Task file created: {task_path.relative_to(VAULT)}")

    start_time  = time.monotonic()
    iteration   = 0
    outcome     = "max_iterations"
    summary     = f"Reached max iterations ({max_iter}) without completion signal"
    prior_output = ""

    try:
        while iteration < max_iter:

            # ── Safety checks ──────────────────────────────────────────────
            if STOP_FILE.exists():
                print(f"\nSTOP_RALPH file detected — halting cleanly.")
                outcome = "stopped"
                summary = f"Halted by STOP_RALPH at iteration {iteration}"
                break

            elapsed = time.monotonic() - start_time
            remaining_total = total_to - elapsed
            if remaining_total <= 0:
                print(f"\nTotal timeout ({total_to}s) reached after {iteration} iteration(s).")
                outcome = "timeout"
                summary = f"Total timeout ({total_to}s) reached at iteration {iteration}"
                break

            # ── Next iteration ─────────────────────────────────────────────
            iteration += 1
            print(f"\n{'─' * 60}")
            print(f"Iteration {iteration}/{max_iter}  |  elapsed={elapsed:.0f}s")

            # Update task file iteration counter
            update_task_file(task_path, iteration)

            # Build prompt
            if iteration == 1:
                prompt = build_initial_prompt(task_id, description, task_path)
            else:
                prompt = build_continuation_prompt(
                    task_id, description, task_path,
                    iteration, max_iter, prior_output,
                )

            # ── Invoke Claude (or simulate) ────────────────────────────────
            if dry_run:
                print(f"[DRY RUN] Prompt ({len(prompt)} chars):")
                print(prompt[:600] + ("\n…(truncated)" if len(prompt) > 600 else ""))
                output, rc = f"(dry run iteration {iteration})", 0
            else:
                this_timeout = min(iter_to, remaining_total)
                print(f"Invoking Claude (timeout={this_timeout:.0f}s)…")
                output, rc = invoke_claude(prompt, this_timeout)
                print(f"Claude finished  rc={rc}  output={len(output)} chars")
                if rc == -2:
                    # Fatal — claude not found, no point continuing
                    print(output)
                    outcome = "error"
                    summary = "claude CLI not found on PATH"
                    break

            prior_output = output

            # ── Completion checks ──────────────────────────────────────────
            if output_has_promise(output):
                print(f"✓ Completion promise '{COMPLETION_PROMISE}' found in output.")
                outcome = "completed"
                summary = f"Task completed in {iteration} iteration(s)"
                update_task_file(
                    task_path, iteration,
                    note=f"COMPLETED — promise found in Claude output.",
                )
                break

            if task_moved_to_done(task_id):
                print(f"✓ Task file moved to Done/ by Claude.")
                outcome = "completed"
                summary  = f"Task completed in {iteration} iteration(s) (file moved to Done)"
                # Claude already moved the file; don't call mark_task_done below.
                task_path = DONE_DIR / f"TASK_{task_id}.md"
                break

            # ── Not done yet ───────────────────────────────────────────────
            if rc == -1:
                note = f"Iteration {iteration}: Claude timed out after {iter_to}s"
            elif rc != 0:
                note = f"Iteration {iteration}: Claude exited with code {rc}"
            else:
                note = f"Iteration {iteration}: no completion signal — continuing."

            print(note)
            update_task_file(task_path, iteration, note=note)

    except KeyboardInterrupt:
        print("\nInterrupted.")
        outcome = "interrupted"
        summary = f"User interrupted at iteration {iteration}"

    # ── Archive task file if still in Active_Projects/ ────────────────────
    if task_path.exists() and task_path.parent == ACTIVE_PROJECTS_DIR:
        done_path = mark_task_done(task_path, outcome, summary)
        print(f"\nTask archived → {done_path.relative_to(VAULT)}")

    # ── Log ───────────────────────────────────────────────────────────────
    append_json_log({
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "action_type":     "ralph_loop",
        "result":          outcome,
        "task_id":         task_id,
        "iterations_used": iteration,
        "summary":         summary,
        "task":            description[:200],
    })

    # ── Dashboard ─────────────────────────────────────────────────────────
    update_dashboard(task_id, description, outcome)

    # ── Final report ──────────────────────────────────────────────────────
    elapsed_total = time.monotonic() - start_time
    print(f"\n{'═' * 60}")
    print(f"Ralph Loop done | outcome={outcome} | iterations={iteration}/{max_iter} | elapsed={elapsed_total:.0f}s")
    print(f"Task ID: {task_id}")

    return 0 if outcome == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
