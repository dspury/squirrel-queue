#!/usr/bin/env python3
"""Squirrel v1.5 CLI — operator interface for the execution pipeline.

Usage:
    squirrel submit "objective" --criteria "..." [--priority high] [--constraint "..."]
    squirrel status [task_id]
    squirrel run [--agent claude] [--dry-run]
    squirrel watch [--tail N]
    squirrel lanes
    squirrel events [--tail N]
    squirrel task <task_id>
    squirrel retry <task_id>
    squirrel cancel <task_id>
    squirrel purge [target]
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from squirrel import INBOX, REGISTRY, OUTBOX, CONTROL, LANES


def _next_task_id() -> str:
    """Generate the next task ID by scanning inbox + registry."""
    year = datetime.now().year
    existing = set()
    for d in [INBOX, REGISTRY]:
        for f in d.glob("sq_*.json"):
            parts = f.stem.split("_")
            if len(parts) == 3:
                try:
                    existing.add(int(parts[2]))
                except ValueError:
                    pass
    seq = max(existing, default=0) + 1
    return f"sq_{year}_{seq:04d}"


def _load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


# ── interactive input ──────────────────────────────────────────────

def _read_multiline() -> str:
    """Read multi-line input from the terminal. Paste-friendly.
    Submit with two consecutive empty lines or Ctrl-D."""
    print("Paste your objective below. Press Enter twice to submit.\n")
    lines = []
    empty_count = 0
    try:
        while True:
            line = input()
            if line == "":
                empty_count += 1
                if empty_count >= 2:
                    break
                lines.append(line)
            else:
                empty_count = 0
                lines.append(line)
    except EOFError:
        pass

    text = "\n".join(lines).strip()
    if not text:
        print("Aborted: empty objective.")
        sys.exit(1)
    return text


# ── submit ──────────────────────────────────────────────────────────

def cmd_submit(args):
    objective = args.objective if args.objective else _read_multiline()

    task_id = _next_task_id()
    task = {
        "task_id": task_id,
        "title": objective.split("\n", 1)[0].strip()[:80],
        "objective": objective,
        "priority": args.priority,
        "owner": "user",
        "source": "vos",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "queued",
        "constraints": args.constraint or [],
        "success_criteria": args.criteria or ["Objective completed as described"],
        "context_files": args.context or [],
    }

    dest = INBOX / f"{task_id}.json"
    with open(dest, "w") as f:
        json.dump(task, f, indent=2)

    print(f"Submitted: {task_id}")
    print(f"  Title:    {task['title']}")
    print(f"  Priority: {task['priority']}")
    print(f"  Criteria: {len(task['success_criteria'])}")
    print(f"  File:     {dest}")


# ── status ──────────────────────────────────────────────────────────

def cmd_status(args):
    if args.task_id:
        # Show detail for one task
        found = False
        for d in [REGISTRY, INBOX]:
            p = d / f"{args.task_id}.json"
            if p.exists():
                task = _load_json(p)
                _print_task_detail(task)
                # Show receipt if exists
                receipt_path = OUTBOX / f"{args.task_id}_receipt.json"
                if receipt_path.exists():
                    receipt = _load_json(receipt_path)
                    print(f"\n  Receipt:")
                    print(f"    Validation: {receipt.get('validation_result', '?').upper()}")
                    print(f"    Notes:      {receipt.get('validation_notes', '')}")
                    if receipt.get("artifacts"):
                        print(f"    Artifacts:  {', '.join(receipt['artifacts'])}")
                    if receipt.get("errors"):
                        print(f"    Errors:     {', '.join(receipt['errors'])}")
                found = True
                break
        if not found:
            print(f"Task {args.task_id} not found.")
            sys.exit(1)
    else:
        # List all tasks
        tasks = []
        for p in sorted(INBOX.glob("sq_*.json")):
            tasks.append(("inbox", _load_json(p)))
        for p in sorted(REGISTRY.glob("sq_*.json")):
            tasks.append(("registry", _load_json(p)))

        if not tasks:
            print("No tasks.")
            return

        print(f"{'ID':<18} {'STATUS':<12} {'PRIORITY':<10} {'TITLE'}")
        print("-" * 70)
        for location, task in tasks:
            tid = task.get("task_id", "?")
            status = task.get("status", "?")
            priority = task.get("priority", "?")
            title = task.get("title", "?")[:35]
            print(f"{tid:<18} {status:<12} {priority:<10} {title}")


def _print_task_detail(task):
    print(f"  ID:          {task.get('task_id')}")
    print(f"  Title:       {task.get('title')}")
    print(f"  Status:      {task.get('status')}")
    print(f"  Priority:    {task.get('priority')}")
    print(f"  Objective:   {task.get('objective')}")
    print(f"  Created:     {task.get('created_at')}")
    if task.get("constraints"):
        print(f"  Constraints: {', '.join(task['constraints'])}")
    if task.get("success_criteria"):
        for i, c in enumerate(task["success_criteria"], 1):
            print(f"  Criterion {i}: {c}")
    if task.get("context_files"):
        print(f"  Context:     {', '.join(task['context_files'])}")
    if task.get("transitions"):
        print(f"\n  Transitions:")
        for t in task["transitions"]:
            print(f"    {t['from']} -> {t['to']} ({t['trigger']}) at {t['timestamp']}")


# ── run ─────────────────────────────────────────────────────────────

def cmd_run(args):
    from squirrel.runner import run_once

    handler = None
    handler_factory = None

    if args.agent:
        from squirrel.lane_codex_queue import create_handler

        def handler_factory(task):
            return create_handler(
                agent=args.agent,
                dry_run=args.dry_run,
                timeout_ms=args.timeout,
                cwd=args.cwd,
                task=task,
                tmux=args.tmux,
            )

    if args.tmux and not args.agent:
        print("WARNING: --tmux has no effect without --agent.")

    if args.tmux and args.agent:
        import subprocess as _sp
        # Ensure the tmux session exists
        _sp.run(["tmux", "new-session", "-d", "-s", "squirrel-lanes"], capture_output=True)
        # Open a new Terminal.app window attached to it
        _sp.run(["osascript", "-e",
                 'tell application "Terminal" to do script "tmux attach -t squirrel-lanes"'],
                capture_output=True)

    run_once(handler=handler, handler_factory=handler_factory, cwd=args.cwd)


# ── watch ──────────────────────────────────────────────────────────

def cmd_watch(args):
    """Live system state — commander + lanes + recent events."""
    from squirrel import events

    interval = args.interval
    try:
        while True:
            # Clear screen
            print("\033[2J\033[H", end="")
            print("=" * 60)
            print("  SQUIRREL — Live System State")
            print("=" * 60)

            # Commander state
            commander = events.read_commander()
            if commander:
                print(f"\n  Commander: {commander.get('phase', '?')}")
                detail = commander.get("detail", "")
                if detail:
                    print(f"  Detail:    {detail}")
                task_id = commander.get("task_id", "")
                if task_id:
                    print(f"  Task:      {task_id}")
                print(f"  Updated:   {commander.get('updated_at', '?')}")
            else:
                print("\n  Commander: not running")

            # Lane states
            all_lanes = events.read_all_lanes()
            if all_lanes:
                print(f"\n  {'LANE':<12} {'ROLE':<12} {'STATUS':<10} {'TASK':<18} {'ACTION'}")
                print("  " + "-" * 56)
                for lane in all_lanes:
                    lid = lane.get("lane_id", "?")
                    role = lane.get("role", "?")
                    status = lane.get("status", "?")
                    tid = lane.get("task_id", "")
                    action = lane.get("current_action", "")[:30]
                    print(f"  {lid:<12} {role:<12} {status:<10} {tid:<18} {action}")
            else:
                print("\n  Lanes: none active")

            # Recent events
            tail = args.tail or 10
            lines = events.read_log(tail=tail)
            if lines:
                print(f"\n  Recent Events (last {len(lines)}):")
                for line in lines:
                    # Truncate for display
                    print(f"  {line[:78]}")
            else:
                print("\n  Events: none")

            print(f"\n  Refreshing every {interval}s. Ctrl-C to exit.")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")


# ── lanes ──────────────────────────────────────────────────────────

def cmd_lanes(args):
    """Show current lane states."""
    from squirrel import events

    all_lanes = events.read_all_lanes()
    if not all_lanes:
        print("No active lanes.")
        return

    print(f"{'LANE':<12} {'ROLE':<12} {'STATUS':<10} {'TASK':<18} {'PACKET':<22} {'ACTION'}")
    print("-" * 90)
    for lane in all_lanes:
        lid = lane.get("lane_id", "?")
        role = lane.get("role", "?")
        status = lane.get("status", "?")
        tid = lane.get("task_id", "")
        pid = lane.get("packet_id", "")
        action = lane.get("current_action", "")[:25]
        print(f"{lid:<12} {role:<12} {status:<10} {tid:<18} {pid:<22} {action}")

    if args.verbose:
        for lane in all_lanes:
            if lane.get("last_error"):
                print(f"\n  {lane['lane_id']} error: {lane['last_error']}")
            if lane.get("artifact_path"):
                print(f"  {lane['lane_id']} artifact: {lane['artifact_path']}")


# ── events ─────────────────────────────────────────────────────────

def cmd_events(args):
    """Show the event log."""
    from squirrel import events

    tail = args.tail or 0

    if args.follow:
        # Tail -f mode
        last_count = 0
        try:
            while True:
                lines = events.read_log()
                if len(lines) > last_count:
                    for line in lines[last_count:]:
                        print(line)
                    last_count = len(lines)
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        lines = events.read_log(tail=tail)
        if not lines:
            print("No events.")
            return
        for line in lines:
            print(line)


# ── task (inspect) ─────────────────────────────────────────────────

def cmd_task(args):
    """Deep inspect a single task — state, transitions, receipt, lanes."""
    from squirrel import events

    task_id = args.task_id

    # Find the task
    task = None
    for d in [REGISTRY, INBOX]:
        p = d / f"{task_id}.json"
        if p.exists():
            task = _load_json(p)
            break

    if not task:
        print(f"Task {task_id} not found.")
        sys.exit(1)

    _print_task_detail(task)

    # Receipt
    receipt_path = OUTBOX / f"{task_id}_receipt.json"
    if receipt_path.exists():
        receipt = _load_json(receipt_path)
        print(f"\n  Receipt:")
        print(f"    Status:     {receipt.get('status', '?')}")
        print(f"    Validation: {receipt.get('validation_result', '?').upper()}")
        print(f"    Notes:      {receipt.get('validation_notes', '')}")
        if receipt.get("artifacts"):
            print(f"    Artifacts:  {', '.join(receipt['artifacts'])}")
        if receipt.get("actions_taken"):
            print(f"    Actions:")
            for a in receipt["actions_taken"]:
                print(f"      - {a}")
        if receipt.get("errors"):
            print(f"    Errors:")
            for e in receipt["errors"]:
                print(f"      - {e}")
        print(f"    Started:    {receipt.get('started_at', '?')}")
        print(f"    Completed:  {receipt.get('completed_at', '?')}")

    # Related lane states
    all_lanes = events.read_all_lanes()
    related = [l for l in all_lanes if l.get("task_id") == task_id]
    if related:
        print(f"\n  Lane Activity:")
        for lane in related:
            status = lane.get("status", "?")
            lid = lane.get("lane_id", "?")
            role = lane.get("role", "?")
            print(f"    {lid} ({role}): {status}")
            if lane.get("last_error"):
                print(f"      Error: {lane['last_error']}")

    # Related events
    log_lines = events.read_log()
    related_events = [l for l in log_lines if task_id in l]
    if related_events:
        print(f"\n  Events ({len(related_events)}):")
        for line in related_events[-10:]:
            print(f"    {line[:76]}")


# ── retry ───────────────────────────────────────────────────────────

def cmd_retry(args):
    from squirrel import state

    reg_path = REGISTRY / f"{args.task_id}.json"
    if not reg_path.exists():
        print(f"Task {args.task_id} not found in registry.")
        sys.exit(1)

    task = _load_json(reg_path)
    if task.get("status") != "failed":
        print(f"Task {args.task_id} is '{task.get('status')}', not 'failed'. Cannot retry.")
        sys.exit(1)

    retry_count = task.get("retry_count", 0)
    max_retries = state.max_retries()
    if retry_count >= max_retries:
        print(f"Task {args.task_id} has exhausted retries ({retry_count}/{max_retries}).")
        sys.exit(1)

    task["status"] = "queued"
    task["retry_count"] = retry_count + 1

    failed = task.get("failed_criteria", [])

    if args.full:
        task.pop("failed_criteria", None)
        print(f"  Mode: full retry (all criteria)")
    elif failed:
        print(f"  Mode: partial retry ({len(failed)} failed criteria only)")

    with open(reg_path, "w") as f:
        json.dump(task, f, indent=2)

    print(f"Re-queued: {args.task_id} (retry {task['retry_count']}/{max_retries})")


# ── cancel ──────────────────────────────────────────────────────────

def cmd_cancel(args):
    # Check inbox first
    inbox_path = INBOX / f"{args.task_id}.json"
    if inbox_path.exists():
        inbox_path.unlink()
        print(f"Removed {args.task_id} from inbox.")
        return

    # Check registry
    reg_path = REGISTRY / f"{args.task_id}.json"
    if not reg_path.exists():
        print(f"Task {args.task_id} not found.")
        sys.exit(1)

    task = _load_json(reg_path)
    if task.get("status") in ("complete", "failed"):
        print(f"Task {args.task_id} already '{task['status']}'. Nothing to cancel.")
        return

    task["status"] = "failed"
    with open(reg_path, "w") as f:
        json.dump(task, f, indent=2)

    # Write a cancellation receipt
    from squirrel import receipts
    receipt = {
        "task_id": args.task_id,
        "lane_id": "",
        "status": "failed",
        "artifacts": [],
        "actions_taken": ["Cancelled by operator."],
        "validation_result": "fail",
        "validation_notes": "Task cancelled.",
        "errors": ["Operator cancellation."],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    receipt_path = OUTBOX / f"{args.task_id}_receipt.json"
    with open(receipt_path, "w") as f:
        json.dump(receipt, f, indent=2)

    print(f"Cancelled: {args.task_id}")


# ── purge ──────────────────────────────────────────────────────────

def cmd_purge(args):
    from squirrel import events

    dirs = {"inbox": INBOX, "registry": REGISTRY, "outbox": OUTBOX, "lanes": LANES}

    if args.target == "all":
        targets = list(dirs.values())
    else:
        targets = [dirs[args.target]]

    count = 0
    for d in targets:
        for f in d.glob("*.json"):
            f.unlink()
            count += 1

    # Also clear runtime state and lock on full purge
    if args.target == "all":
        lock = CONTROL / "runner.lock"
        if lock.exists():
            lock.unlink()
        events.clear_log()
        events.clear_lanes()
        commander = events._COMMANDER_PATH
        if commander.exists():
            commander.unlink()

    print(f"Purged {count} file(s).")
    if args.target == "all":
        print("Cleared runtime state.")


# ── main ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="squirrel",
        description="Squirrel v1.5 — task execution pipeline",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # submit
    p_submit = sub.add_parser("submit", help="Submit a new task")
    p_submit.add_argument("objective", nargs="?", default=None,
                           help="What needs to be accomplished (omit to open editor)")
    p_submit.add_argument("--priority", choices=["critical", "high", "normal", "low"], default="normal")
    p_submit.add_argument("--criteria", action="append", help="Success criterion (repeatable, optional)")
    p_submit.add_argument("--constraint", action="append", help="Constraint (repeatable)")
    p_submit.add_argument("--context", action="append", help="Context file path (repeatable)")
    p_submit.set_defaults(func=cmd_submit)

    # status
    p_status = sub.add_parser("status", help="Show task status")
    p_status.add_argument("task_id", nargs="?", help="Specific task ID (omit for all)")
    p_status.set_defaults(func=cmd_status)

    # run
    p_run = sub.add_parser("run", help="Process inbox tasks")
    p_run.add_argument("--agent", choices=["codex", "claude", "gemini"])
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--timeout", type=int, default=0, help="Agent timeout in ms")
    p_run.add_argument("--cwd", default=None, help="Working directory for agent")
    p_run.add_argument("--tmux", action="store_true", help="Show agent lanes in tmux panes")
    p_run.set_defaults(func=cmd_run)

    # watch
    p_watch = sub.add_parser("watch", help="Live system state (refreshing)")
    p_watch.add_argument("--interval", type=int, default=2, help="Refresh interval in seconds")
    p_watch.add_argument("--tail", type=int, default=10, help="Number of recent events to show")
    p_watch.set_defaults(func=cmd_watch)

    # lanes
    p_lanes = sub.add_parser("lanes", help="Show current lane states")
    p_lanes.add_argument("-v", "--verbose", action="store_true", help="Show errors and artifacts")
    p_lanes.set_defaults(func=cmd_lanes)

    # events
    p_events = sub.add_parser("events", help="Show the event log")
    p_events.add_argument("--tail", type=int, default=0, help="Show last N events (0 = all)")
    p_events.add_argument("-f", "--follow", action="store_true", help="Follow mode (like tail -f)")
    p_events.set_defaults(func=cmd_events)

    # task (inspect)
    p_task = sub.add_parser("task", help="Deep inspect a task")
    p_task.add_argument("task_id", help="Task ID to inspect")
    p_task.set_defaults(func=cmd_task)

    # retry
    p_retry = sub.add_parser("retry", help="Re-queue a failed task")
    p_retry.add_argument("task_id", help="Task ID to retry")
    p_retry.add_argument("--full", action="store_true", help="Retry all criteria, not just failed ones")
    p_retry.set_defaults(func=cmd_retry)

    # cancel
    p_cancel = sub.add_parser("cancel", help="Cancel a task")
    p_cancel.add_argument("task_id", help="Task ID to cancel")
    p_cancel.set_defaults(func=cmd_cancel)

    # purge
    p_purge = sub.add_parser("purge", help="Clear task files from the pipeline")
    p_purge.add_argument("target", nargs="?", default="all",
                         choices=["all", "inbox", "registry", "outbox", "lanes"],
                         help="What to purge (default: all)")
    p_purge.set_defaults(func=cmd_purge)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
