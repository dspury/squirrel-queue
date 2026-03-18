"""Append-only event log and runtime state tracking.

Events are the source of truth for what happened during execution.
Runtime state files provide live visibility into the system.

Event log:  .squirrel/runtime/events.log
Commander:  .squirrel/runtime/commander.json
Lane state: .squirrel/runtime/lanes/{lane_id}.json
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from squirrel import RUNTIME, RUNTIME_LANES


_LOG_PATH = RUNTIME / "events.log"
_COMMANDER_PATH = RUNTIME / "commander.json"


# ── event log ──────────────────────────────────────────────────────

def emit(event_type: str, detail: dict = None):
    """Append a structured event to the log.

    Format: [ISO timestamp] event_type {json detail}
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    payload = json.dumps(detail) if detail else "{}"
    line = f"[{ts}] {event_type} {payload}\n"
    with open(_LOG_PATH, "a") as f:
        f.write(line)


def read_log(tail: int = 0) -> list[str]:
    """Read the event log. If tail > 0, return only the last N lines."""
    if not _LOG_PATH.exists():
        return []
    lines = _LOG_PATH.read_text().splitlines()
    if tail > 0:
        return lines[-tail:]
    return lines


def clear_log():
    """Reset the event log (for purge operations)."""
    if _LOG_PATH.exists():
        _LOG_PATH.unlink()


# ── commander state ────────────────────────────────────────────────

def update_commander(phase: str, detail: dict = None):
    """Write the commander's current state for live observability.

    The commander state file is overwritten each time — it represents
    the current state, not history (that's what events.log is for).
    """
    state = {
        "phase": phase,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if detail:
        state.update(detail)
    tmp = _COMMANDER_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    tmp.rename(_COMMANDER_PATH)


def read_commander() -> dict:
    """Read the current commander state."""
    if not _COMMANDER_PATH.exists():
        return {}
    try:
        with open(_COMMANDER_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


# ── lane state ─────────────────────────────────────────────────────

def update_lane(lane_id: str, state_data: dict):
    """Write a lane's current state for live observability.

    Lane state schema:
        lane_id, role, status, task_id, packet_id,
        current_action, updated_at, artifact_path, last_error
    """
    state_data["lane_id"] = lane_id
    state_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = RUNTIME_LANES / f"{lane_id}.json"
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(state_data, f, indent=2)
    tmp.rename(path)


def read_lane(lane_id: str) -> dict:
    """Read a single lane's state."""
    path = RUNTIME_LANES / f"{lane_id}.json"
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def read_all_lanes() -> list[dict]:
    """Read all lane states, sorted by lane_id."""
    lanes = []
    for f in sorted(RUNTIME_LANES.glob("*.json")):
        try:
            with open(f) as fh:
                lanes.append(json.load(fh))
        except (json.JSONDecodeError, OSError):
            pass
    return lanes


def clear_lanes():
    """Remove all lane state files (for purge operations)."""
    for f in RUNTIME_LANES.glob("*.json"):
        f.unlink()
