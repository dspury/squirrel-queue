"""Tests for squirrel.validation — criteria checking and verify commands."""

import json
import tempfile
from pathlib import Path

import pytest
from squirrel.validation import check, parse_criterion, _check_criterion, _run_verify


class TestParseCriterion:
    def test_plain(self):
        desc, cmd = parse_criterion("game.py file exists")
        assert desc == "game.py file exists"
        assert cmd is None

    def test_with_verify(self):
        desc, cmd = parse_criterion("grid is 12x12 :: wc -l output.txt | grep 12")
        assert desc == "grid is 12x12"
        assert cmd == "wc -l output.txt | grep 12"

    def test_separator_only_first(self):
        desc, cmd = parse_criterion("a :: b :: c")
        assert desc == "a"
        assert cmd == "b :: c"


class TestRunVerify:
    def test_passing_command(self, tmp_path):
        passed, note = _run_verify("true", "true", tmp_path)
        assert passed
        assert "passed" in note.lower()

    def test_failing_command(self, tmp_path):
        passed, note = _run_verify("false", "false", tmp_path)
        assert not passed
        assert "failed" in note.lower()

    def test_runs_in_cwd(self, tmp_path):
        (tmp_path / "marker.txt").write_text("hello")
        passed, _ = _run_verify("check", "test -f marker.txt", tmp_path)
        assert passed

    def test_timeout(self, tmp_path):
        from squirrel import validation
        old = validation.VERIFY_TIMEOUT
        validation.VERIFY_TIMEOUT = 1
        passed, note = _run_verify("slow", "sleep 10", tmp_path)
        validation.VERIFY_TIMEOUT = old
        assert not passed
        assert "timed out" in note.lower()


class TestCheckCriterion:
    def test_file_exists(self, tmp_path):
        (tmp_path / "foo.txt").write_text("x")
        passed, _ = _check_criterion("foo.txt file exists", tmp_path)
        assert passed

    def test_file_missing(self, tmp_path):
        passed, note = _check_criterion("missing.txt file exists", tmp_path)
        assert not passed
        assert "not found" in note.lower()

    def test_content_check(self, tmp_path):
        (tmp_path / ".gitignore").write_text("node_modules\n__pycache__\n")
        passed, _ = _check_criterion("Includes node_modules, __pycache__", tmp_path)
        assert passed

    def test_unverifiable_fails(self, tmp_path):
        passed, note = _check_criterion("The code is elegant", tmp_path)
        assert not passed
        assert "UNVERIFIABLE" in note


class TestCheck:
    def _lane_ok(self):
        return [{"packet_id": "wp_01", "success": True}]

    def _lane_fail(self):
        return [{"packet_id": "wp_01", "success": False}]

    def test_no_criteria(self):
        passed, _, _ = check({"success_criteria": []}, self._lane_ok())
        assert not passed

    def test_lane_failure_short_circuits(self, tmp_path):
        task = {"success_criteria": ["anything"]}
        passed, note, _ = check(task, self._lane_fail())
        assert not passed
        assert "Lane execution failed" in note

    def test_file_criterion_passes(self, tmp_path):
        (tmp_path / "app.py").write_text("x")
        task = {"success_criteria": ["app.py file exists"]}
        passed, _, _ = check(task, self._lane_ok(), cwd=str(tmp_path))
        assert passed

    def test_verify_criterion_passes(self, tmp_path):
        (tmp_path / "data.txt").write_text("hello\nworld\n")
        task = {"success_criteria": ["two lines :: wc -l data.txt | grep -q 2"]}
        passed, _, _ = check(task, self._lane_ok(), cwd=str(tmp_path))
        assert passed

    def test_verify_criterion_fails(self, tmp_path):
        (tmp_path / "data.txt").write_text("hello\n")
        task = {"success_criteria": ["five lines :: test $(wc -l < data.txt) -eq 5"]}
        passed, _, _ = check(task, self._lane_ok(), cwd=str(tmp_path))
        assert not passed

    def test_mixed_criteria(self, tmp_path):
        (tmp_path / "app.py").write_text("x")
        task = {
            "success_criteria": [
                "app.py file exists",
                "app runs :: test -f app.py",
            ]
        }
        passed, _, _ = check(task, self._lane_ok(), cwd=str(tmp_path))
        assert passed
