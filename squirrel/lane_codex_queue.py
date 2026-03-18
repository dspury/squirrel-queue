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


def _assemble_prompt(packet: dict, cwd: str = None) -> str:
    """Build the full prompt sent to the agent.

    The agent receives only what it needs to do the work:
      1. Context files (injected content)
      2. Constraints
      3. Objective
      4. Success criteria (so the agent knows what will be verified)
      5. Expected artifact (if any)

    ROLE.md and CONSTITUTION.md are Squirrel system docs — they are
    NOT injected into agent prompts. The agent is a worker, not the
    Commander.
    """
    parts = []

    # Inject context files
    for filepath in packet.get("context_files", []):
        resolve_from = Path(cwd) if cwd else _PROJECT_ROOT
        p = resolve_from / filepath
        if p.exists():
            content = p.read_text().strip()
            parts.append(f"--- File: {filepath} ---\n{content}")

    for constraint in packet.get("constraints", []):
        parts.append(f"Constraint: {constraint}")

    parts.append(f"Objective:\n{packet['objective']}")

    # Include success criteria so the agent knows what will be checked
    criteria = packet.get("criteria", [])
    if criteria:
        criteria_block = "\n".join(f"  - {c}" for c in criteria)
        parts.append(f"Success criteria (your work will be verified against these):\n{criteria_block}")
    else:
        criterion = packet.get("criterion")
        if criterion and criterion != "Objective completed as described":
            parts.append(f"Success criterion for this step: {criterion}")

    artifact = packet.get("expected_artifact")
    if artifact:
        parts.append(f"Expected output: {artifact}")

    prompt = "\n\n".join(parts)
    return prompt


def build_request(
    packet: dict,
    task: dict,
    agent: str = "codex",
    execution_mode: str = "local",
    timeout_ms: int = 0,
    cwd: str = None,
) -> dict:
    """Translate a Squirrel work packet + parent task into a codex-queue request."""
    request = {
        "schema": "codex-queue-request@v1",
        "task_type": task.get("title", "squirrel-lane-task")[:80],
        "repo": ".",
        "prompt": _assemble_prompt(packet, cwd=cwd),
        "execution_mode": execution_mode,
        "return_contract": "squirrel-lane-result",
        "priority": task.get("priority", "normal"),
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
        result = dispatch_via_codex_queue(request, cwd=cwd, dry_run=dry_run, tmux=tmux)

        return {
            "success": result.get("ok", False),
            "artifact": result.get("deferred_request_path", ""),
            "notes": result.get("summary", "No summary returned."),
        }

    return handler
