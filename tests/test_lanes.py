"""Tests for squirrel.lanes — dispatch and blocked state."""

import json
from pathlib import Path

import pytest
from squirrel.lanes import dispatch, check_context_files, BlockedError, NoHandlerError


def _stub_handler(p):
    return {"success": True, "artifact": "", "notes": "stub executed"}


class TestCheckContextFiles:
    def test_no_context_files(self):
        assert check_context_files({}) == []

    def test_existing_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        packet = {"context_files": ["a.txt"]}
        assert check_context_files(packet, cwd=tmp_path) == []

    def test_missing_files(self, tmp_path):
        packet = {"context_files": ["missing.py"]}
        missing = check_context_files(packet, cwd=tmp_path)
        assert missing == ["missing.py"]


class TestDispatch:
    def _packet(self):
        return {
            "packet_id": "wp_2026_0001_01",
            "lane_id": "lane_01",
            "context_files": [],
            "objective": "test",
            "status": "queued",
        }

    def test_no_handler_raises(self, tmp_path):
        with pytest.raises(NoHandlerError):
            dispatch(self._packet(), cwd=tmp_path)

    def test_custom_handler(self, tmp_path):
        def handler(p):
            return {"success": True, "artifact": "out.txt", "notes": "done"}

        result = dispatch(self._packet(), handler=handler, cwd=tmp_path)
        assert result["success"]
        assert result["artifact"] == "out.txt"

    def test_handler_exception_captured(self, tmp_path):
        def bad_handler(p):
            raise RuntimeError("boom")

        result = dispatch(self._packet(), handler=bad_handler, cwd=tmp_path)
        assert not result["success"]
        assert "boom" in result["notes"]

    def test_blocked_on_missing_context(self, tmp_path):
        packet = self._packet()
        packet["context_files"] = ["nonexistent.py"]
        with pytest.raises(BlockedError) as exc_info:
            dispatch(packet, handler=_stub_handler, cwd=tmp_path)
        assert "nonexistent.py" in str(exc_info.value)

    def test_lane_file_written(self, tmp_path):
        from squirrel import LANES
        LANES.mkdir(parents=True, exist_ok=True)
        result = dispatch(self._packet(), handler=_stub_handler, cwd=tmp_path)
        lane_file = LANES / "wp_2026_0001_01.json"
        assert lane_file.exists()
        data = json.loads(lane_file.read_text())
        assert data["status"] == "complete"
