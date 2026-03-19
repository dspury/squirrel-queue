"""Squirrel lane handler that dispatches work packets through codex-queue.

This is the bridge between Squirrel's execution pipeline and CLI agents
(codex, claude, gemini). It translates work packets into codex-queue
requests, shells out to the codex-queue CLI, and maps results back.

Usage:
    from squirrel.lane_codex_queue import create_handler

    handler = create_handler(agent="claude")
    # Pass to runner:
    from squirrel.runner import run_once
    run_once(handler=handler)
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from squirrel import CONFIG

# Resolve codex-queue binary relative to the project root.
# The codex-queue package lives at squirrel_v1/codex-queue/bin/codex-queue.js.
# Override with CODEX_QUEUE_BIN env var if needed.
import os as _os

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BUNDLED_BIN = _PROJECT_ROOT / "codex-queue" / "bin" / "codex-queue.js"
_env_bin = _os.environ.get("CODEX_QUEUE_BIN", "")

if _env_bin:
    CODEX_QUEUE_BIN = _env_bin
elif _BUNDLED_BIN.exists():
    CODEX_QUEUE_BIN = str(_BUNDLED_BIN)
else:
    CODEX_QUEUE_BIN = shutil.which("codex-queue") or "codex-queue"

# Default subprocess timeout (seconds). Separate from codex-queue's
# internal timeout_ms which kills the *agent* process.
SUBPROCESS_TIMEOUT = 600


def _read_config_file(name: str) -> str:
    """Read a file from .squirrel/config/ if it exists."""
    p = CONFIG / name
    if p.exists():
        return p.read_text().strip()
    return ""


# Maximum bytes of context file content to inject into the prompt.
# The codex-queue hard limit is 16,000 chars for the full prompt.
# Reserve ~4KB for system framing, objective, criteria, and constraints.
CONTEXT_BUDGET_BYTES = 12000

# Role-aware prefix lines. These replace the codex-queue DEFAULT_PREFIX_LINE
# so the agent gets task-appropriate framing instead of a blanket constraint.
_ROLE_PREFIX = {
    "builder": "You are a Squirrel worker agent. Produce working code artifacts that satisfy the objective. Make the smallest production-credible change.",
    "researcher": "You are a Squirrel research agent. Investigate the topic and produce a structured, factual report. Cite sources where possible.",
    "reviewer": "You are a Squirrel review agent. Audit the code or artifact and identify issues, risks, and improvements. Be specific and actionable.",
    "operator": "You are a Squirrel operations agent. Execute the deployment, migration, or configuration task precisely. Verify each step before proceeding.",
}

_SYSTEM_FRAMING = (
    "You are executing a work packet dispatched by the Squirrel task pipeline. "
    "Your output will be programmatically validated against the success criteria listed below. "
    "Focus on producing artifacts (files, diffs, reports) — not conversation. "
    "Do not explain what you plan to do; just do it."
)


def _assemble_prompt(packet: dict, cwd: str = None) -> str:
    """Build the full prompt sent to the agent.

    Structure:
      1. System framing (who you are, how output is validated)
      2. Role-specific prefix
      3. Context files (with size guard)
      4. Constraints
      5. Objective
      6. Success criteria
      7. Expected artifact (if any)
    """
    role = packet.get("role", "builder")
    parts = []

    # 1. System framing
    parts.append(_SYSTEM_FRAMING)

    # 2. Role-specific prefix
    prefix = _ROLE_PREFIX.get(role, _ROLE_PREFIX["builder"])
    parts.append(prefix)

    # 3. Inject context files with size guard
    context_bytes_used = 0
    skipped_files = []
    for filepath in packet.get("context_files", []):
        resolve_from = Path(cwd) if cwd else _PROJECT_ROOT
        p = resolve_from / filepath
        if p.exists():
            try:
                content = p.read_text().strip()
            except (OSError, UnicodeDecodeError):
                skipped_files.append(f"{filepath} (unreadable)")
                continue
            content_size = len(content.encode("utf-8"))
            if context_bytes_used + content_size > CONTEXT_BUDGET_BYTES:
                skipped_files.append(f"{filepath} ({content_size:,}B, over budget)")
                continue
            context_bytes_used += content_size
            parts.append(f"--- File: {filepath} ---\n{content}")

    if skipped_files:
        parts.append(
            f"Note: {len(skipped_files)} context file(s) skipped due to size limits: "
            + ", ".join(skipped_files)
        )

    # 4. Constraints
    for constraint in packet.get("constraints", []):
        parts.append(f"Constraint: {constraint}")

    # 5. Objective
    parts.append(f"Objective:\n{packet['objective']}")

    # 6. Success criteria
    criteria = packet.get("criteria", [])
    if criteria:
        criteria_block = "\n".join(f"  - {c}" for c in criteria)
        parts.append(f"Success criteria (your work will be verified against these):\n{criteria_block}")
    else:
        criterion = packet.get("criterion")
        if criterion and criterion != "Objective completed as described":
            parts.append(f"Success criterion for this step: {criterion}")

    # 7. Expected artifact
    artifact = packet.get("expected_artifact")
    if artifact:
        parts.append(f"Expected output: {artifact}")

    prompt = "\n\n".join(parts)
    return prompt


_PRIORITY_MAP = {"critical": "urgent", "high": "high", "normal": "normal", "low": "low"}


def build_request(
    packet: dict,
    task: dict,
    agent: str = "codex",
    execution_mode: str = "local",
    timeout_ms: int = 0,
    cwd: str = None,
) -> dict:
    """Translate a Squirrel work packet + parent task into a codex-queue request."""
    raw_priority = task.get("priority", "normal")
    cq_priority = _PRIORITY_MAP.get(raw_priority, "normal")

    request = {
        "schema": "codex-queue-request@v1",
        "task_type": task.get("title", "squirrel-lane-task")[:80],
        "repo": ".",
        "prompt": _assemble_prompt(packet, cwd=cwd),
        "execution_mode": execution_mode,
        "return_contract": "squirrel-lane-result",
        "priority": cq_priority,
        "origin": "squirrel",
        "agent": agent,
    }
    if timeout_ms > 0:
        request["timeout_ms"] = timeout_ms
    dedupe = f"{task.get('task_id', '')}:{packet.get('packet_id', '')}"
    if dedupe != ":":
        request["dedupe_key"] = dedupe
    return request


def dispatch_via_codex_queue(
    request: dict,
    cwd: str = None,
    dry_run: bool = False,
    tmux: bool = False,
) -> dict:
    """Shell out to codex-queue and parse the result JSON."""
    cmd = [CODEX_QUEUE_BIN, "--payload", json.dumps(request)]
    # Disable codex-queue's default prefix line — Squirrel bakes role-aware
    # framing directly into the prompt via _assemble_prompt().
    cmd.extend(["--prefix-line", ""])
    if cwd:
        cmd.extend(["--cwd", str(cwd)])
    if dry_run:
        cmd.append("--dry-run")
    if tmux:
        cmd.append("--tmux")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "status": "failed",
            "summary": f"codex-queue binary not found at: {CODEX_QUEUE_BIN}",
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "status": "failed",
            "summary": f"codex-queue subprocess timed out after {SUBPROCESS_TIMEOUT}s.",
        }

    stdout = result.stdout.strip()
    if not stdout:
        return {
            "ok": False,
            "status": "failed",
            "summary": f"codex-queue produced no output. stderr: {result.stderr.strip()[:200]}",
        }

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "status": "failed",
            "summary": f"codex-queue returned invalid JSON: {stdout[:200]}",
        }


def create_handler(
    agent: str = "codex",
    execution_mode: str = "local",
    timeout_ms: int = 0,
    cwd: str = None,
    dry_run: bool = False,
    task: dict = None,
    tmux: bool = False,
):
    """Create a lane handler function for use with squirrel.lanes.dispatch().

    Args:
        agent: CLI agent to use — "codex", "claude", or "gemini"
        execution_mode: "local", "cloud", or "defer"
        timeout_ms: Kill agent process after this many ms (0 = no timeout)
        cwd: Working directory for agent execution
        dry_run: Validate without executing
        task: Parent task dict (provides title, priority for the request).
              Can be overridden per-call if the handler is reused across tasks.
        tmux: Run agent in a visible tmux pane.

    Returns:
        A handler function with signature: handler(packet) -> dict
    """
    def handler(packet: dict) -> dict:
        # Use the task passed at creation, or fall back to a minimal stub
        parent_task = task or {"title": "squirrel-task", "priority": "normal"}

        request = build_request(
            packet=packet,
            task=parent_task,
            agent=agent,
            execution_mode=execution_mode,
            timeout_ms=timeout_ms,
            cwd=cwd,
        )

        # On dry-run, print the full assembled prompt for operator inspection.
        if dry_run:
            prompt_text = request.get("prompt", "")
            print(f"\n{'─' * 60}")
            print(f"PROMPT PREVIEW ({len(prompt_text)} chars) — {packet.get('packet_id', '?')}")
            print(f"{'─' * 60}")
            print(prompt_text)
            print(f"{'─' * 60}\n")

        result = dispatch_via_codex_queue(request, cwd=cwd, dry_run=dry_run, tmux=tmux)

        return {
            "success": result.get("ok", False),
            "artifact": result.get("deferred_request_path", ""),
            "notes": result.get("summary", "No summary returned."),
        }

    return handler
