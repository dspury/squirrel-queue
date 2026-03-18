"""Receipt generation. Every completed or failed task produces a receipt
in outbox/ conforming to receipt.schema.json."""

import json
from datetime import datetime, timezone

from squirrel import OUTBOX


def generate(
    task: dict,
    lane_results: list[dict],
    validation_passed: bool,
    validation_notes: str,
) -> dict:
    """Build a receipt dict from execution results."""
    all_started = [r["started_at"] for r in lane_results if r.get("started_at")]
    all_completed = [r["completed_at"] for r in lane_results if r.get("completed_at")]

    receipt = {
        "task_id": task["task_id"],
        "title": task.get("title", task["task_id"]),
        "lane_id": ", ".join(r["lane_id"] for r in lane_results),
        "status": "complete" if validation_passed else "failed",
        "artifacts": [r["artifact"] for r in lane_results if r.get("artifact")],
        "actions_taken": [r["notes"] for r in lane_results],
        "validation_result": "pass" if validation_passed else "fail",
        "validation_notes": validation_notes,
        "errors": [
            r["notes"] for r in lane_results if not r["success"]
        ],
        "started_at": min(all_started) if all_started else datetime.now(timezone.utc).isoformat(),
        "completed_at": max(all_completed) if all_completed else datetime.now(timezone.utc).isoformat(),
    }
    return receipt


def write(receipt: dict) -> str:
    """Write receipt to outbox/. Returns the file path."""
    filename = f"{receipt['task_id']}_receipt.json"
    path = OUTBOX / filename
    with open(path, "w") as f:
        json.dump(receipt, f, indent=2)
    return str(path)


def summary(receipt: dict) -> str:
    """Produce a 5-line concise summary for upstream consumption."""
    title = receipt.get("title", receipt["task_id"])
    return (
        f"Task: {title}\n"
        f"Status: {receipt['status'].upper()}\n"
        f"Artifacts: {', '.join(receipt['artifacts']) or 'none'}\n"
        f"Validation: {receipt['validation_result'].upper()}\n"
        f"Notes: {receipt['validation_notes']}"
    )
