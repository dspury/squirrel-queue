"""Intake module. Validates incoming task JSON against the task schema
and moves valid tasks from inbox/ to registry/."""

import json
import shutil
from pathlib import Path

from jsonschema import validate, ValidationError
from squirrel import INBOX, REGISTRY, SCHEMAS


def _load_task_schema():
    with open(SCHEMAS / "task.schema.json") as f:
        return json.load(f)


_TASK_SCHEMA = None


def _get_schema():
    global _TASK_SCHEMA
    if _TASK_SCHEMA is None:
        _TASK_SCHEMA = _load_task_schema()
    return _TASK_SCHEMA


def validate_task(task: dict) -> list[str]:
    """Validate a task dict against the schema. Returns list of errors (empty = valid)."""
    errors = []
    try:
        validate(instance=task, schema=_get_schema())
    except ValidationError as e:
        errors.append(f"Schema validation failed: {e.message}")
    return errors


def scan_inbox() -> list[Path]:
    """Return all .json files in inbox/, sorted by name."""
    return sorted(INBOX.glob("*.json"))


def ingest(task_path: Path) -> tuple[bool, str]:
    """Validate a task file and move it to registry if valid.
    Returns (success, message)."""
    try:
        with open(task_path) as f:
            task = json.load(f)
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON in {task_path.name}: {e}"

    errors = validate_task(task)
    if errors:
        return False, f"Task {task_path.name} failed validation: {'; '.join(errors)}"

    # Check for duplicate in registry
    dest = REGISTRY / task_path.name
    if dest.exists():
        return False, f"Task {task['task_id']} already exists in registry."

    # Move to registry
    shutil.move(str(task_path), str(dest))
    return True, f"Task {task['task_id']} registered."


def ingest_all() -> list[tuple[str, bool, str]]:
    """Process all tasks in inbox. Returns list of (filename, success, message)."""
    results = []
    for path in scan_inbox():
        success, msg = ingest(path)
        results.append((path.name, success, msg))
    return results
