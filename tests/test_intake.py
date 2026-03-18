"""Tests for squirrel.intake — task validation and ingestion."""

import json
from pathlib import Path

import pytest
from squirrel import INBOX, REGISTRY, ensure_workspace
from squirrel.intake import validate_task, ingest, ingest_all


@pytest.fixture(autouse=True)
def workspace():
    ensure_workspace()
    # Clean inbox and registry before each test
    for f in INBOX.glob("*.json"):
        f.unlink()
    for f in REGISTRY.glob("sq_9999_*.json"):
        f.unlink()
    yield
    for f in INBOX.glob("*.json"):
        f.unlink()
    for f in REGISTRY.glob("sq_9999_*.json"):
        f.unlink()


def _valid_task(task_id="sq_9999_0001"):
    return {
        "task_id": task_id,
        "title": "Test task",
        "objective": "Do something",
        "priority": "normal",
        "status": "queued",
        "success_criteria": ["thing exists"],
        "created_at": "2026-01-01T00:00:00Z",
    }


class TestValidateTask:
    def test_valid(self):
        assert validate_task(_valid_task()) == []

    def test_missing_objective(self):
        task = _valid_task()
        del task["objective"]
        errors = validate_task(task)
        assert len(errors) > 0

    def test_empty_criteria(self):
        task = _valid_task()
        task["success_criteria"] = []
        errors = validate_task(task)
        assert len(errors) > 0

    def test_invalid_priority(self):
        task = _valid_task()
        task["priority"] = "ultra"
        errors = validate_task(task)
        assert len(errors) > 0


class TestIngest:
    def test_valid_task_moves_to_registry(self):
        task = _valid_task("sq_9999_0001")
        path = INBOX / "sq_9999_0001.json"
        path.write_text(json.dumps(task))

        success, msg = ingest(path)
        assert success
        assert (REGISTRY / "sq_9999_0001.json").exists()
        assert not path.exists()

    def test_invalid_json_rejected(self):
        path = INBOX / "bad.json"
        path.write_text("not json{{{")

        success, msg = ingest(path)
        assert not success
        assert "Invalid JSON" in msg

    def test_duplicate_rejected(self):
        task = _valid_task("sq_9999_0002")
        (REGISTRY / "sq_9999_0002.json").write_text(json.dumps(task))
        path = INBOX / "sq_9999_0002.json"
        path.write_text(json.dumps(task))

        success, msg = ingest(path)
        assert not success
        assert "already exists" in msg


class TestIngestAll:
    def test_processes_all_inbox(self):
        for i in range(3):
            task = _valid_task(f"sq_9999_000{i}")
            (INBOX / f"sq_9999_000{i}.json").write_text(json.dumps(task))

        results = ingest_all()
        assert len(results) == 3
        assert all(r[1] for r in results)
