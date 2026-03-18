#!/usr/bin/env python3
"""Squirrel v1 entry point. Runs one full execution cycle.

Usage:
    python run.py                          # default handler (stub)
    python run.py --agent codex            # dispatch via codex-queue → codex
    python run.py --agent claude           # dispatch via codex-queue → claude
    python run.py --agent gemini           # dispatch via codex-queue → gemini
    python run.py --agent claude --dry-run # validate without executing
"""

import argparse
from squirrel.runner import run_once


def main():
    parser = argparse.ArgumentParser(description="Squirrel v1 — Execution Loop")
    parser.add_argument(
        "--agent",
        choices=["codex", "claude", "gemini"],
        default=None,
        help="Route execution through codex-queue using this agent CLI.",
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

    handler = None
    if args.agent:
        from squirrel.lane_codex_queue import create_handler
        handler = create_handler(
            agent=args.agent,
            dry_run=args.dry_run,
            timeout_ms=args.timeout,
            cwd=args.cwd,
        )

    run_once(handler=handler)


if __name__ == "__main__":
    main()
