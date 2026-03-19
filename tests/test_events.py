"""Tests for squirrel.events — event log and runtime state tracking."""

import json

from squirrel import RUNTIME, RUNTIME_LANES, ensure_workspace
from squirrel.events import (
    emit, read_log, clear_log,
    update_commander, read_commander,
    update_lane, read_lane, read_all_lanes, clear_lanes,
)


import pytest


@pytest.fixture(autouse=True)
def clean_runtime():
    ensure_workspace()
    clear_log()
    clear_lanes()
    commander = RUNTIME / "commander.json"
    if commander.exists():
        commander.unlink()
    yield
    clear_log()
    clear_lanes()
    if commander.exists():
        commander.unlink()


class TestEventLog:
    def test_emit_and_read(self):
        emit("task_submitted", {"task_id": "sq_9999_0001"})
        lines = read_log()
        assert len(lines) == 1
        assert "task_submitted" in lines[0]
        assert "sq_9999_0001" in lines[0]

    def test_append_only(self):
        emit("task_submitted", {"task_id": "sq_9999_0001"})
        emit("plan_created", {"task_id": "sq_9999_0001"})
        emit("receipt_written", {"task_id": "sq_9999_0001"})
        lines = read_log()
        assert len(lines) == 3

    def test_tail(self):
        for i in range(10):
            emit("event", {"i": i})
        lines = read_log(tail=3)
        assert len(lines) == 3

    def test_clear(self):
        emit("event", {})
        clear_log()
        assert read_log() == []

    def test_read_empty(self):
        assert read_log() == []


class TestCommanderState:
    def test_update_and_read(self):
        update_commander("intake", {"detail": "Scanning"})
        state = read_commander()
        assert state["phase"] == "intake"
        assert state["detail"] == "Scanning"
        assert "updated_at" in state

    def test_overwrite(self):
        update_commander("intake")
        update_commander("dispatch", {"task_id": "sq_9999_0001"})
        state = read_commander()
        assert state["phase"] == "dispatch"
        assert "detail" not in state

    def test_read_empty(self):
        assert read_commander() == {}


class TestLaneState:
    def test_update_and_read(self):
        update_lane("lane_01", {
            "role": "builder", "status": "running",
            "task_id": "sq_9999_0001", "packet_id": "wp_9999_0001_01",
            "current_action": "editing runner.py",
            "artifact_path": "", "last_error": "",
        })
        state = read_lane("lane_01")
        assert state["lane_id"] == "lane_01"
        assert state["role"] == "builder"
        assert state["status"] == "running"
        assert "updated_at" in state

    def test_read_all(self):
        update_lane("lane_01", {"role": "builder", "status": "running",
                                "task_id": "t", "packet_id": "p",
                                "current_action": "", "artifact_path": "", "last_error": ""})
        update_lane("lane_02", {"role": "reviewer", "status": "idle",
                                "task_id": "", "packet_id": "",
                                "current_action": "", "artifact_path": "", "last_error": ""})
        all_lanes = read_all_lanes()
        assert len(all_lanes) == 2
        assert all_lanes[0]["lane_id"] == "lane_01"
        assert all_lanes[1]["lane_id"] == "lane_02"

    def test_read_missing(self):
        assert read_lane("nonexistent") == {}

    def test_clear(self):
        update_lane("lane_01", {"role": "builder", "status": "idle",
                                "task_id": "", "packet_id": "",
                                "current_action": "", "artifact_path": "", "last_error": ""})
        clear_lanes()
        assert read_all_lanes() == []


class TestEventsIntegration:
    def test_runner_emits_events(self, tmp_path):
        """Verify that running a task produces events."""
        from squirrel import INBOX, REGISTRY, OUTBOX, CONTROL
        from squirrel.runner import run_once

        def _stub_handler(packet):
            return {"success": True, "artifact": "", "notes": "stub"}

        # Clean
        for d in [INBOX, OUTBOX]:
            for f in d.glob("sq_9999_*.json"):
                f.unlink()
        for f in REGISTRY.glob("sq_9999_*.json"):
            f.unlink()
        lock = CONTROL / "runner.lock"
        if lock.exists():
            lock.unlink()

        # Submit a task
        (tmp_path / "test.txt").write_text("x")
        task = {
            "task_id": "sq_9999_0001", "title": "Test events",
            "objective": "Test", "priority": "normal",
            "owner": "test", "source": "manual",
            "created_at": "2026-01-01T00:00:00Z", "status": "queued",
            "constraints": [], "success_criteria": ["test.txt file exists"],
            "context_files": [],
        }
        (INBOX / "sq_9999_0001.json").write_text(json.dumps(task))

        clear_log()
        run_once(handler=_stub_handler, cwd=str(tmp_path))

        lines = read_log()
        event_types = [l.split("] ")[1].split(" ")[0] for l in lines]
        assert "task_submitted" in event_types
        assert "plan_created" in event_types
        assert "packet_dispatched" in event_types
        assert "lane_completed" in event_types
        assert "receipt_written" in event_types

        # Commander state should be idle after run
        commander = read_commander()
        assert commander["phase"] == "idle"

        # Cleanup
        for d in [INBOX, OUTBOX]:
            for f in d.glob("sq_9999_*.json"):
                f.unlink()
        for f in REGISTRY.glob("sq_9999_*.json"):
            f.unlink()
