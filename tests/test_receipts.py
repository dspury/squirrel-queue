"""Tests for squirrel.receipts — receipt generation and writing."""

import json
from squirrel import OUTBOX, ensure_workspace
from squirrel.receipts import generate, write, summary


def _lane_result(success=True):
    return {
        "packet_id": "wp_2026_0001_01",
        "lane_id": "lane_01",
        "success": success,
        "artifact": "out.txt" if success else "",
        "notes": "done" if success else "failed",
        "started_at": "2026-01-01T00:00:00Z",
        "completed_at": "2026-01-01T00:01:00Z",
    }


def _task():
    return {"task_id": "sq_2026_0001", "title": "Build receipt schema"}


class TestGenerate:
    def test_pass(self):
        r = generate(_task(), [_lane_result()], True, "All good")
        assert r["status"] == "complete"
        assert r["validation_result"] == "pass"
        assert r["artifacts"] == ["out.txt"]

    def test_fail(self):
        r = generate(_task(), [_lane_result(False)], False, "Bad")
        assert r["status"] == "failed"
        assert r["validation_result"] == "fail"
        assert "failed" in r["errors"][0]

    def test_timestamps(self):
        r = generate(_task(), [_lane_result()], True, "ok")
        assert r["started_at"] == "2026-01-01T00:00:00Z"
        assert r["completed_at"] == "2026-01-01T00:01:00Z"


class TestWrite:
    def test_writes_to_outbox(self):
        ensure_workspace()
        receipt = generate(_task(), [_lane_result()], True, "ok")
        path = write(receipt)
        assert "sq_2026_0001_receipt.json" in path
        data = json.loads(open(path).read())
        assert data["task_id"] == "sq_2026_0001"
        # Cleanup
        import os
        os.unlink(path)


class TestSummary:
    def test_format(self):
        receipt = generate(_task(), [_lane_result()], True, "All good")
        s = summary(receipt)
        assert "Build receipt schema" in s
        assert "COMPLETE" in s
        assert "PASS" in s

    def test_summary_uses_title_not_id(self):
        receipt = generate(_task(), [_lane_result()], True, "ok")
        s = summary(receipt)
        assert s.startswith("Task: Build receipt schema")

    def test_summary_falls_back_to_id(self):
        task = {"task_id": "sq_2026_0099"}
        receipt = generate(task, [_lane_result()], True, "ok")
        s = summary(receipt)
        assert "sq_2026_0099" in s
