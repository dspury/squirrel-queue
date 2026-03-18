"""Tests for squirrel.runner — execution loop stability."""

import json

import pytest
from squirrel import INBOX, REGISTRY, OUTBOX, CONTROL, ensure_workspace
from squirrel.runner import run_once, _check_control, _make_crash_receipt


@pytest.fixture(autouse=True)
def clean_workspace():
    ensure_workspace()
    # Clean test artifacts
    for d in [INBOX, OUTBOX]:
        for f in d.glob("sq_9999_*.json"):
            f.unlink()
        for f in d.glob("sq_9999_*_receipt.json"):
            f.unlink()
    for f in REGISTRY.glob("sq_9999_*.json"):
        f.unlink()
    for pattern in ["cancel_*.json", "retry_*.json"]:
        for f in CONTROL.glob(pattern):
            f.unlink()
    pipeline = CONTROL / "pipeline.json"
    if pipeline.exists():
        pipeline.unlink()
    lock = CONTROL / "runner.lock"
    if lock.exists():
        lock.unlink()
    yield
    for d in [INBOX, OUTBOX]:
        for f in d.glob("sq_9999_*.json"):
            f.unlink()
        for f in d.glob("sq_9999_*_receipt.json"):
            f.unlink()
    for f in REGISTRY.glob("sq_9999_*.json"):
        f.unlink()


def _submit_task(task_id="sq_9999_0001", criteria=None):
    task = {
        "task_id": task_id,
        "title": "Test",
        "objective": "Test objective",
        "priority": "normal",
        "owner": "test",
        "source": "manual",
        "created_at": "2026-01-01T00:00:00Z",
        "status": "queued",
        "constraints": [],
        "success_criteria": criteria or ["Objective completed as described"],
        "context_files": [],
    }
    (INBOX / f"{task_id}.json").write_text(json.dumps(task))
    return task


class TestCheckControl:
    def test_no_signals(self):
        signals = _check_control()
        assert not signals["paused"]
        assert len(signals["cancel_ids"]) == 0

    def test_pause_signal(self):
        (CONTROL / "pipeline.json").write_text('{"state": "paused"}')
        signals = _check_control()
        assert signals["paused"]

    def test_running_not_paused(self):
        (CONTROL / "pipeline.json").write_text('{"state": "running"}')
        signals = _check_control()
        assert not signals["paused"]

    def test_cancel_signal(self):
        sig = {"action": "cancel", "task_id": "sq_9999_0001"}
        (CONTROL / "cancel_sq_9999_0001.json").write_text(json.dumps(sig))
        signals = _check_control()
        assert "sq_9999_0001" in signals["cancel_ids"]

    def test_retry_signal(self):
        sig = {"action": "retry", "task_id": "sq_9999_0001"}
        (CONTROL / "retry_sq_9999_0001.json").write_text(json.dumps(sig))
        signals = _check_control()
        assert "sq_9999_0001" in signals["retry_ids"]


class TestCrashReceipt:
    def test_structure(self):
        r = _make_crash_receipt("sq_9999_0001", "boom")
        assert r["task_id"] == "sq_9999_0001"
        assert r["status"] == "failed"
        assert "boom" in r["errors"][0]
        assert r["validation_result"] == "fail"


class TestRunOnce:
    def test_empty_inbox_returns_empty(self):
        result = run_once()
        assert result == []

    def test_paused_returns_empty(self):
        _submit_task()
        (CONTROL / "pipeline.json").write_text('{"state": "paused"}')
        result = run_once()
        assert result == []

    def test_processes_task(self, tmp_path):
        (tmp_path / "test.txt").write_text("x")
        _submit_task(criteria=["test.txt file exists"])
        result = run_once(cwd=str(tmp_path))
        assert len(result) == 1
        assert result[0]["task_id"] == "sq_9999_0001"
        assert result[0]["validation_result"] == "pass"

    def test_unverifiable_fails(self):
        _submit_task(criteria=["The vibes are good"])
        result = run_once()
        assert len(result) == 1
        assert result[0]["validation_result"] == "fail"

    def test_receipt_always_written(self):
        _submit_task()
        run_once()
        assert (OUTBOX / "sq_9999_0001_receipt.json").exists()

    def test_retry_signal_requeues_failed_task(self, tmp_path):
        """Retry signal in control/ re-queues a failed task."""
        (tmp_path / "test.txt").write_text("x")
        # First run: task fails because criterion is unverifiable
        _submit_task(criteria=["The vibes are good"])
        run_once()
        # Verify it failed
        task_data = json.loads((REGISTRY / "sq_9999_0001.json").read_text())
        assert task_data["status"] == "failed"
        # Drop a retry signal
        sig = {"action": "retry", "task_id": "sq_9999_0001"}
        (CONTROL / "retry_sq_9999_0001.json").write_text(json.dumps(sig))
        # Second run: retry signal re-queues, task fails again but proves re-queue worked
        run_once()
        task_data = json.loads((REGISTRY / "sq_9999_0001.json").read_text())
        assert task_data.get("retry_count") == 1

    def test_priority_ordering(self, tmp_path):
        """Higher-priority tasks are processed before lower-priority ones."""
        (tmp_path / "test.txt").write_text("x")
        # Submit low-priority first, then critical
        low = {
            "task_id": "sq_9999_0002", "title": "Low", "objective": "low",
            "priority": "low", "owner": "test", "source": "manual",
            "created_at": "2026-01-01T00:00:00Z", "status": "queued",
            "constraints": [], "success_criteria": ["test.txt file exists"],
            "context_files": [],
        }
        critical = {
            "task_id": "sq_9999_0001", "title": "Critical", "objective": "critical",
            "priority": "critical", "owner": "test", "source": "manual",
            "created_at": "2026-01-01T00:00:00Z", "status": "queued",
            "constraints": [], "success_criteria": ["test.txt file exists"],
            "context_files": [],
        }
        # Submit low first so it'd be processed first by filename sort
        (INBOX / "sq_9999_0002.json").write_text(json.dumps(low))
        (INBOX / "sq_9999_0001.json").write_text(json.dumps(critical))
        results = run_once(cwd=str(tmp_path))
        assert len(results) == 2
        # Critical should be processed first
        assert results[0]["task_id"] == "sq_9999_0001"
        assert results[1]["task_id"] == "sq_9999_0002"
