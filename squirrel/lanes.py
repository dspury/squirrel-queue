"""Lane dispatch and execution.

Lanes are persistent work cells — they receive a work packet, execute it,
and return a result.

v1.5 lane model:
  - Fixed lane IDs (lane_01, lane_02, ...)
  - Assigned role per packet (builder, researcher, reviewer, operator)
  - One packet at a time per lane
  - Heartbeat timestamp for liveness
  - State tracked in .squirrel/runtime/lanes/
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from squirrel import LANES

# Valid roles for lane assignment
VALID_ROLES = {"builder", "researcher", "reviewer", "operator"}

# Type for lane handler functions
# handler(packet) -> {"success": bool, "artifact": str, "notes": str}
LaneHandler = Callable[[dict], dict]


class BlockedError(Exception):
    """Raised when a packet cannot proceed due to missing context."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(f"Missing context files: {', '.join(missing)}")


class NoHandlerError(Exception):
    """Raised when dispatch is called without a handler configured."""

    def __init__(self):
        super().__init__(
            "No agent handler configured. Use --agent to specify an agent "
            "(e.g. squirrel run --agent claude). Running without an agent "
            "produces no real work."
        )


def check_context_files(packet: dict, cwd: Path = None) -> list[str]:
    """Verify all context_files referenced in a packet exist.
    Returns list of missing file paths (empty = all present)."""
    resolve_from = cwd if cwd else Path.cwd()
    missing = []
    for cf in packet.get("context_files", []):
        target = resolve_from / cf
        if not target.exists():
            missing.append(cf)
    return missing


def validate_role(packet: dict):
    """Verify the packet has a valid role assignment."""
    role = packet.get("role", "builder")
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role '{role}'. Must be one of: {VALID_ROLES}")


def dispatch(packet: dict, handler: LaneHandler = None, cwd: Path = None) -> dict:
    """Send a work packet to a lane for execution.

    Raises BlockedError if context files are missing — the caller
    (runner) should transition the parent task to blocked state.

    Returns the lane run result.
    """
    if handler is None:
        raise NoHandlerError()

    # Pre-flight: verify role and context files
    validate_role(packet)

    missing = check_context_files(packet, cwd=cwd)
    if missing:
        raise BlockedError(missing)

    lane_id = packet["lane_id"]
    role = packet.get("role", "builder")
    started = datetime.now(timezone.utc).isoformat()

    # Write active packet to lanes/ for observability
    lane_file = LANES / f"{packet['packet_id']}.json"
    packet["status"] = "active"
    with open(lane_file, "w") as f:
        json.dump(packet, f, indent=2)

    # Execute
    try:
        result = handler(packet)
        packet["status"] = "complete" if result["success"] else "failed"
    except Exception as e:
        result = {"success": False, "artifact": "", "notes": f"Lane error: {e}"}
        packet["status"] = "failed"

    completed = datetime.now(timezone.utc).isoformat()

    # Update lane file with final state
    with open(lane_file, "w") as f:
        json.dump(packet, f, indent=2)

    return {
        "packet_id": packet["packet_id"],
        "lane_id": lane_id,
        "role": role,
        "success": result["success"],
        "artifact": result.get("artifact", ""),
        "notes": result.get("notes", ""),
        "started_at": started,
        "completed_at": completed,
    }
