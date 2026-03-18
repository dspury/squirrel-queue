"""Tests for squirrel.planner — task decomposition."""

from squirrel.planner import decompose, _group_criteria, _extract_target


class TestDecompose:
    def test_single_criterion_single_packet(self):
        task = {
            "task_id": "sq_2026_0001",
            "objective": "Do a thing",
            "success_criteria": ["thing.py file exists"],
        }
        packets = decompose(task)
        assert len(packets) == 1
        assert packets[0]["objective"] == "Do a thing"
        assert packets[0]["criteria"] == ["thing.py file exists"]

    def test_no_criteria_defaults(self):
        task = {
            "task_id": "sq_2026_0001",
            "objective": "Do a thing",
            "success_criteria": [],
        }
        packets = decompose(task)
        assert len(packets) == 1
        assert packets[0]["criteria"] == ["Objective completed as described"]

    def test_same_dir_files_single_packet(self):
        task = {
            "task_id": "sq_2026_0001",
            "objective": "Build it",
            "success_criteria": [
                "game.py file exists",
                "main.py file exists",
                "state.json file exists",
            ],
        }
        packets = decompose(task)
        assert len(packets) == 1
        assert len(packets[0]["criteria"]) == 3

    def test_different_dirs_split(self):
        task = {
            "task_id": "sq_2026_0001",
            "objective": "Build frontend and backend",
            "success_criteria": [
                "frontend/index.html file exists",
                "backend/server.py file exists",
            ],
        }
        packets = decompose(task)
        assert len(packets) == 2

    def test_packets_carry_full_objective(self):
        task = {
            "task_id": "sq_2026_0001",
            "objective": "Full objective text here",
            "success_criteria": [
                "frontend/a.py file exists",
                "backend/b.py file exists",
            ],
        }
        packets = decompose(task)
        for p in packets:
            assert p["objective"] == "Full objective text here"

    def test_packet_ids_sequential(self):
        task = {
            "task_id": "sq_2026_0005",
            "objective": "test",
            "success_criteria": [
                "a/x.py file exists",
                "b/y.py file exists",
            ],
        }
        packets = decompose(task)
        assert packets[0]["packet_id"] == "wp_2026_0005_01"
        assert packets[1]["packet_id"] == "wp_2026_0005_02"

    def test_constraints_propagated(self):
        task = {
            "task_id": "sq_2026_0001",
            "objective": "test",
            "success_criteria": ["done"],
            "constraints": ["no external deps"],
        }
        packets = decompose(task)
        assert packets[0]["constraints"] == ["no external deps"]


class TestGroupCriteria:
    def test_single_criterion(self):
        groups = _group_criteria(["a"], "obj")
        assert groups == [["a"]]

    def test_all_same_target(self):
        criteria = ["src/a.py file exists", "src/b.py file exists"]
        groups = _group_criteria(criteria, "obj")
        assert len(groups) == 1

    def test_different_targets(self):
        criteria = ["src/a.py file exists", "docs/b.md file exists"]
        groups = _group_criteria(criteria, "obj")
        assert len(groups) == 2

    def test_unextractable_joins_largest(self):
        criteria = [
            "src/a.py file exists",
            "src/b.py file exists",
            "All tests pass",
        ]
        groups = _group_criteria(criteria, "obj")
        assert len(groups) == 1


class TestExtractTarget:
    def test_dotfile(self):
        assert _extract_target("game.py file exists") == "game.py"

    def test_path_file(self):
        assert _extract_target("src/app.py file exists") == "src/app.py"

    def test_backticked(self):
        assert _extract_target("`frontend/index.html` exists") == "frontend/index.html"

    def test_no_target(self):
        assert _extract_target("All tests pass") is None

    def test_dir_with_slash(self):
        assert _extract_target("docs/ exists") == "docs"
