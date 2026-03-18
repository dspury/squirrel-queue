"""Tests for squirrel.state — state machine enforcement."""

import pytest
from squirrel import state


def test_valid_transitions_returns_tuples():
    transitions = state.valid_transitions()
    assert len(transitions) > 0
    for t in transitions:
        assert len(t) == 3


def test_can_transition_valid():
    assert state.can_transition("queued", "active")
    assert state.can_transition("active", "validating")
    assert state.can_transition("active", "blocked")
    assert state.can_transition("active", "failed")
    assert state.can_transition("blocked", "queued")
    assert state.can_transition("validating", "complete")
    assert state.can_transition("validating", "failed")
    assert state.can_transition("failed", "queued")


def test_can_transition_invalid():
    assert not state.can_transition("queued", "complete")
    assert not state.can_transition("queued", "failed")
    assert not state.can_transition("complete", "queued")
    assert not state.can_transition("blocked", "complete")


def test_transition_applies():
    task = {"status": "queued"}
    state.transition(task, "active", "lane_pickup")
    assert task["status"] == "active"


def test_transition_logs_history():
    task = {"status": "queued"}
    state.transition(task, "active", "lane_pickup")
    state.transition(task, "validating", "execution_complete")
    state.transition(task, "complete", "validation_pass")
    assert len(task["transitions"]) == 3
    assert task["transitions"][0]["from"] == "queued"
    assert task["transitions"][0]["to"] == "active"
    assert task["transitions"][0]["trigger"] == "lane_pickup"
    assert "timestamp" in task["transitions"][0]
    assert task["transitions"][2]["from"] == "validating"
    assert task["transitions"][2]["to"] == "complete"


def test_transition_illegal_raises():
    task = {"status": "queued"}
    with pytest.raises(ValueError, match="Illegal transition"):
        state.transition(task, "complete", "validation_pass")


def test_transition_wrong_trigger_raises():
    task = {"status": "queued"}
    with pytest.raises(ValueError, match="Trigger"):
        state.transition(task, "active", "wrong_trigger")


def test_max_retries():
    assert state.max_retries() == 3
