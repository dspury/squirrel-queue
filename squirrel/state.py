"""State machine enforcement. Loads transitions from state_machine.json
and validates every status change."""

import json
from datetime import datetime, timezone

from squirrel import SCHEMAS


def _load_machine():
    with open(SCHEMAS / "state_machine.json") as f:
        return json.load(f)


_MACHINE = None


def _get_machine():
    global _MACHINE
    if _MACHINE is None:
        _MACHINE = _load_machine()
    return _MACHINE


def valid_transitions():
    """Return list of (from, to, trigger) tuples."""
    return [
        (t["from"], t["to"], t["trigger"])
        for t in _get_machine()["transitions"]
    ]


def can_transition(current_state: str, target_state: str) -> bool:
    """Check if a transition is legal."""
    return any(
        t[0] == current_state and t[1] == target_state
        for t in valid_transitions()
    )


def transition(task: dict, target_state: str, trigger: str) -> dict:
    """Apply a state transition. Raises ValueError if illegal."""
    current = task["status"]
    if not can_transition(current, target_state):
        raise ValueError(
            f"Illegal transition: {current} -> {target_state}. "
            f"Check state_machine.json for valid paths."
        )
    # Verify trigger matches
    legal = [
        t for t in valid_transitions()
        if t[0] == current and t[1] == target_state
    ]
    triggers = [t[2] for t in legal]
    if trigger not in triggers:
        raise ValueError(
            f"Trigger '{trigger}' not valid for {current} -> {target_state}. "
            f"Expected one of: {triggers}"
        )
    # Record transition in persistent history
    if "transitions" not in task:
        task["transitions"] = []
    task["transitions"].append({
        "from": current,
        "to": target_state,
        "trigger": trigger,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    task["status"] = target_state
    return task


def max_retries() -> int:
    return _get_machine().get("rules", {}).get("max_retries", 3)
