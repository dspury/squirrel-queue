"""Tests for v1.5 planner additions — role inference and packet fields."""

from squirrel.planner import infer_role, decompose


class TestInferRole:
    def test_builder_default(self):
        assert infer_role("Implement the retry flow", ["file exists"]) == "builder"

    def test_researcher(self):
        assert infer_role("Research how logging works", []) == "researcher"

    def test_reviewer(self):
        assert infer_role("Review the auth module", ["no security issues"]) == "reviewer"

    def test_operator(self):
        assert infer_role("Deploy the staging build", []) == "operator"

    def test_criteria_signal(self):
        assert infer_role("Do the thing", ["investigate root cause"]) == "researcher"

    def test_no_signal_defaults_builder(self):
        assert infer_role("Fix the bug", ["bug is fixed"]) == "builder"


class TestPacketFields:
    def test_packet_has_role(self):
        task = {
            "task_id": "sq_2026_0001",
            "objective": "Build the widget",
            "success_criteria": ["widget.py exists"],
        }
        packets = decompose(task)
        assert packets[0]["role"] == "builder"

    def test_packet_has_priority(self):
        task = {
            "task_id": "sq_2026_0001",
            "objective": "Build it",
            "priority": "critical",
            "success_criteria": ["done"],
        }
        packets = decompose(task)
        assert packets[0]["priority"] == "critical"

    def test_packet_has_depends_on(self):
        task = {
            "task_id": "sq_2026_0001",
            "objective": "Build it",
            "success_criteria": ["done"],
        }
        packets = decompose(task)
        assert packets[0]["depends_on"] == []

    def test_packet_has_inputs(self):
        task = {
            "task_id": "sq_2026_0001",
            "objective": "Build it",
            "success_criteria": ["done"],
        }
        packets = decompose(task)
        assert packets[0]["inputs"] == []

    def test_packet_has_success_criteria(self):
        task = {
            "task_id": "sq_2026_0001",
            "objective": "Build it",
            "success_criteria": ["file exists", "tests pass"],
        }
        packets = decompose(task)
        assert packets[0]["success_criteria"] == ["file exists", "tests pass"]
