"""Tests for duplo.task_matcher."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from duplo.extractor import Feature
from duplo.planner import CompletedTask
from duplo.saver import DUPLO_JSON
from duplo.task_matcher import _parse_matches, match_unannotated_tasks


class TestParseMatches:
    def test_parses_valid_json(self):
        raw = json.dumps(
            [
                {
                    "task_index": 0,
                    "match": "existing",
                    "feature": "Dark mode",
                    "description": None,
                    "category": None,
                },
                {
                    "task_index": 1,
                    "match": "none",
                    "feature": None,
                    "description": None,
                    "category": None,
                },
            ]
        )
        result = _parse_matches(raw, 2)
        assert len(result) == 2
        assert result[0]["match"] == "existing"
        assert result[1]["match"] == "none"

    def test_strips_code_fences(self):
        raw = '```json\n[{"task_index": 0, "match": "new", "feature": "Export", "description": "Export data.", "category": "core"}]\n```'
        result = _parse_matches(raw, 1)
        assert len(result) == 1
        assert result[0]["match"] == "new"

    def test_returns_empty_on_invalid_json(self):
        assert _parse_matches("not json", 1) == []

    def test_returns_empty_on_non_list(self):
        assert _parse_matches('{"match": "existing"}', 1) == []

    def test_skips_invalid_match_values(self):
        raw = json.dumps(
            [
                {"task_index": 0, "match": "existing", "feature": "A"},
                {"task_index": 1, "match": "invalid", "feature": "B"},
            ]
        )
        result = _parse_matches(raw, 2)
        assert len(result) == 1

    def test_skips_non_dict_items(self):
        raw = json.dumps(
            [
                "not a dict",
                {"task_index": 0, "match": "none", "feature": None},
            ]
        )
        result = _parse_matches(raw, 1)
        assert len(result) == 1


class TestMatchUnannotatedTasks:
    def _setup_duplo_json(self, tmp_path: Path, features: list[dict]) -> None:
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir()
        path = tmp_path / DUPLO_JSON
        path.write_text(json.dumps({"features": features}), encoding="utf-8")

    def _read_features(self, tmp_path: Path) -> list[dict]:
        path = tmp_path / DUPLO_JSON
        return json.loads(path.read_text(encoding="utf-8"))["features"]

    def test_skips_annotated_tasks(self, tmp_path):
        self._setup_duplo_json(tmp_path, [])
        tasks = [
            CompletedTask(text="Add login", features=["Login"]),
            CompletedTask(text="Fix crash", fixes=["crash on start"]),
        ]
        matched, new = match_unannotated_tasks(tasks, [], "Phase 1", target_dir=str(tmp_path))
        assert matched == []
        assert new == []

    def test_returns_empty_when_no_tasks(self, tmp_path):
        self._setup_duplo_json(tmp_path, [])
        matched, new = match_unannotated_tasks([], [], "Phase 1", target_dir=str(tmp_path))
        assert matched == []
        assert new == []

    @patch("duplo.task_matcher.query")
    def test_marks_existing_feature(self, mock_query, tmp_path):
        features_data = [
            {
                "name": "Dark mode",
                "description": "Toggle dark theme.",
                "category": "ui",
                "status": "pending",
                "implemented_in": "",
            },
        ]
        self._setup_duplo_json(tmp_path, features_data)
        features = [Feature("Dark mode", "Toggle dark theme.", "ui")]

        mock_query.return_value = json.dumps(
            [
                {
                    "task_index": 0,
                    "match": "existing",
                    "feature": "Dark mode",
                    "description": None,
                    "category": None,
                },
            ]
        )

        tasks = [CompletedTask(text="Implement dark mode toggle")]
        matched, new = match_unannotated_tasks(
            tasks, features, "Phase 2", target_dir=str(tmp_path)
        )

        assert matched == ["Dark mode"]
        assert new == []
        saved = self._read_features(tmp_path)
        assert saved[0]["status"] == "implemented"
        assert saved[0]["implemented_in"] == "Phase 2"

    @patch("duplo.task_matcher.query")
    def test_adds_new_feature(self, mock_query, tmp_path):
        self._setup_duplo_json(
            tmp_path,
            [
                {
                    "name": "Login",
                    "description": "User login.",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                },
            ],
        )
        features = [Feature("Login", "User login.", "core")]

        mock_query.return_value = json.dumps(
            [
                {
                    "task_index": 0,
                    "match": "new",
                    "feature": "Export CSV",
                    "description": "Export data to CSV files.",
                    "category": "core",
                },
            ]
        )

        tasks = [CompletedTask(text="Add CSV export button")]
        matched, new = match_unannotated_tasks(
            tasks, features, "Phase 1", target_dir=str(tmp_path)
        )

        assert matched == []
        assert new == ["Export CSV"]
        saved = self._read_features(tmp_path)
        assert len(saved) == 2
        added = saved[1]
        assert added["name"] == "Export CSV"
        assert added["status"] == "implemented"
        assert added["implemented_in"] == "Phase 1"

    @patch("duplo.task_matcher.query")
    def test_skips_none_matches(self, mock_query, tmp_path):
        self._setup_duplo_json(tmp_path, [])

        mock_query.return_value = json.dumps(
            [
                {
                    "task_index": 0,
                    "match": "none",
                    "feature": None,
                    "description": None,
                    "category": None,
                },
            ]
        )

        tasks = [CompletedTask(text="Set up CI pipeline")]
        matched, new = match_unannotated_tasks(tasks, [], "Phase 1", target_dir=str(tmp_path))

        assert matched == []
        assert new == []

    @patch("duplo.task_matcher.query")
    def test_skips_nonexistent_feature_match(self, mock_query, tmp_path):
        self._setup_duplo_json(
            tmp_path,
            [
                {
                    "name": "Login",
                    "description": "User login.",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                },
            ],
        )
        features = [Feature("Login", "User login.", "core")]

        mock_query.return_value = json.dumps(
            [
                {
                    "task_index": 0,
                    "match": "existing",
                    "feature": "Nonexistent feature",
                    "description": None,
                    "category": None,
                },
            ]
        )

        tasks = [CompletedTask(text="Some task")]
        matched, new = match_unannotated_tasks(
            tasks, features, "Phase 1", target_dir=str(tmp_path)
        )

        assert matched == []
        assert new == []

    @patch("duplo.task_matcher.query")
    def test_mixed_results(self, mock_query, tmp_path):
        self._setup_duplo_json(
            tmp_path,
            [
                {
                    "name": "Search",
                    "description": "Full-text search.",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                },
            ],
        )
        features = [Feature("Search", "Full-text search.", "core")]

        mock_query.return_value = json.dumps(
            [
                {
                    "task_index": 0,
                    "match": "existing",
                    "feature": "Search",
                    "description": None,
                    "category": None,
                },
                {
                    "task_index": 1,
                    "match": "new",
                    "feature": "Keyboard nav",
                    "description": "Navigate with keyboard shortcuts.",
                    "category": "ui",
                },
                {
                    "task_index": 2,
                    "match": "none",
                    "feature": None,
                    "description": None,
                    "category": None,
                },
            ]
        )

        tasks = [
            CompletedTask(text="Wire up search"),
            CompletedTask(text="Add keyboard shortcuts"),
            CompletedTask(text="Clean up imports"),
        ]
        matched, new = match_unannotated_tasks(
            tasks, features, "Phase 3", target_dir=str(tmp_path)
        )

        assert matched == ["Search"]
        assert new == ["Keyboard nav"]
        saved = self._read_features(tmp_path)
        assert len(saved) == 2
        assert saved[0]["status"] == "implemented"
        assert saved[1]["name"] == "Keyboard nav"
        assert saved[1]["status"] == "implemented"
        assert saved[1]["implemented_in"] == "Phase 3"

    @patch("duplo.task_matcher.query")
    def test_skips_new_feature_without_description(self, mock_query, tmp_path):
        self._setup_duplo_json(tmp_path, [])

        mock_query.return_value = json.dumps(
            [
                {
                    "task_index": 0,
                    "match": "new",
                    "feature": "Something",
                    "description": "",
                    "category": "core",
                },
            ]
        )

        tasks = [CompletedTask(text="Do something")]
        matched, new = match_unannotated_tasks(tasks, [], "Phase 1", target_dir=str(tmp_path))

        assert new == []

    @patch("duplo.task_matcher.query")
    def test_handles_parse_failure(self, mock_query, tmp_path):
        self._setup_duplo_json(tmp_path, [])

        mock_query.return_value = "not valid json at all"

        tasks = [CompletedTask(text="Some task")]
        matched, new = match_unannotated_tasks(tasks, [], "Phase 1", target_dir=str(tmp_path))

        assert matched == []
        assert new == []

    @patch("duplo.task_matcher.query")
    def test_only_sends_unannotated_tasks(self, mock_query, tmp_path):
        self._setup_duplo_json(tmp_path, [])

        mock_query.return_value = json.dumps(
            [
                {
                    "task_index": 0,
                    "match": "none",
                    "feature": None,
                    "description": None,
                    "category": None,
                },
            ]
        )

        tasks = [
            CompletedTask(text="Add login", features=["Login"]),
            CompletedTask(text="Set up CI"),
            CompletedTask(text="Fix crash", fixes=["startup crash"]),
        ]
        match_unannotated_tasks(tasks, [], "Phase 1", target_dir=str(tmp_path))

        call_args = mock_query.call_args
        prompt = call_args[0][0]
        assert "Set up CI" in prompt
        assert "Add login" not in prompt
        assert "Fix crash" not in prompt
