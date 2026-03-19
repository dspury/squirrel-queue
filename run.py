#!/usr/bin/env python3
"""Squirrel v1 entry point. Runs one full execution cycle.

Usage:
    python run.py --agent codex            # dispatch via codex-queue → codex
    python run.py --agent claude           # dispatch via codex-queue → claude
    python run.py --agent gemini           # dispatch via codex-queue → gemini
    python run.py --agent claude --dry-run # validate without executing
"""

import argparse
import sys
from squirrel.runner import run_once


def main():
    parser = argparse.ArgumentParser(description="Squirrel v1 — Execution Loop")
    parser.add_argument(
        "--agent",
        choices=["codex", "claude", "gemini"],
        required=True,
        help="Agent CLI to use for execution (required).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Pass --dry-run to codex-queue (validate without executing).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=0,
        help="Kill agent process after this many milliseconds.",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="Working directory for agent execution.",
    )
    args = parser.parse_args()

    from squirrel.lane_codex_queue import create_handler

    def handler_factory(task):
        return create_handler(
            agent=args.agent,
            dry_run=args.dry_run,
            timeout_ms=args.timeout,
            cwd=args.cwd,
            task=task,
        )

    run_once(handler_factory=handler_factory, cwd=args.cwd)


if __name__ == "__main__":
    main()
