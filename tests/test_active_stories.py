"""Tests for dashboard.active_stories – the persistent story log helper."""

from __future__ import annotations

import json
import os
import threading
from unittest.mock import patch

import pytest

import dashboard.active_stories as _mod

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def tmp_stories_file(tmp_path, monkeypatch):
    """Redirect STORIES_FILE to a temporary path for each test."""
    stories_file = str(tmp_path / "active_stories.json")
    monkeypatch.setattr(_mod, "STORIES_FILE", stories_file)
    yield stories_file


# ---------------------------------------------------------------------------
# load_stories
# ---------------------------------------------------------------------------


class TestLoadStories:
    def test_returns_empty_list_when_file_missing(self):
        assert _mod.load_stories() == []

    def test_returns_empty_list_on_corrupt_json(self, tmp_stories_file):
        with open(tmp_stories_file, "w") as fh:
            fh.write("not-json")
        assert _mod.load_stories() == []

    def test_corrupt_json_logs_error(self, tmp_stories_file, caplog):
        """A corrupt JSON file should produce a visible log.error message."""
        with open(tmp_stories_file, "w") as fh:
            fh.write("not-json")
        import logging

        with caplog.at_level(logging.ERROR, logger="dashboard.active_stories"):
            _mod.load_stories()
        assert any("failed to load" in r.message for r in caplog.records)

    def test_returns_empty_list_when_json_is_not_a_list(self, tmp_stories_file):
        with open(tmp_stories_file, "w") as fh:
            json.dump({"key": "value"}, fh)
        assert _mod.load_stories() == []

    def test_non_list_json_logs_error(self, tmp_stories_file, caplog):
        """A JSON file whose top-level value is not a list should log an error."""
        with open(tmp_stories_file, "w") as fh:
            json.dump({"key": "value"}, fh)
        import logging

        with caplog.at_level(logging.ERROR, logger="dashboard.active_stories"):
            _mod.load_stories()
        assert any("expected a JSON list" in r.message for r in caplog.records)

    def test_returns_stored_stories(self, tmp_stories_file):
        stories = [{"user_key": "u1", "user_name": "Alice", "started_at": 1.0}]
        with open(tmp_stories_file, "w") as fh:
            json.dump(stories, fh)
        assert _mod.load_stories() == stories

    def test_missing_file_does_not_log_error(self, caplog):
        """A missing file is a normal first-run state and must not log an error."""
        import logging

        with caplog.at_level(logging.ERROR, logger="dashboard.active_stories"):
            _mod.load_stories()
        assert not any(r.levelno >= logging.ERROR for r in caplog.records)


# ---------------------------------------------------------------------------
# upsert_story
# ---------------------------------------------------------------------------


class TestUpsertStory:
    def test_upsert_adds_new_story(self):
        story = {"user_key": "u1", "user_name": "Alice", "started_at": 1.0}
        _mod.upsert_story(story)
        assert _mod.load_stories() == [story]

    def test_upsert_replaces_existing_story(self):
        old = {"user_key": "u1", "user_name": "Alice", "started_at": 1.0, "finished": False}
        new = {"user_key": "u1", "user_name": "Alice", "started_at": 1.0, "finished": True}
        _mod.upsert_story(old)
        _mod.upsert_story(new)
        stories = _mod.load_stories()
        assert len(stories) == 1
        assert stories[0]["finished"] is True

    def test_upsert_ignores_missing_user_key(self):
        _mod.upsert_story({"user_name": "Alice", "started_at": 1.0})
        assert _mod.load_stories() == []

    def test_upsert_multiple_different_users(self):
        for i in range(5):
            _mod.upsert_story(
                {"user_key": f"u{i}", "user_name": f"User{i}", "started_at": float(i)}
            )
        assert len(_mod.load_stories()) == 5

    def test_upsert_cap_at_max_stories(self):
        """When more than MAX_STORIES entries exist, oldest are removed."""
        max_stories = _mod.MAX_STORIES
        for i in range(max_stories + 5):
            _mod.upsert_story({"user_key": f"u{i}", "started_at": float(i)})
        stories = _mod.load_stories()
        assert len(stories) == max_stories

    def test_upsert_cap_removes_oldest(self):
        """After the cap, the retained entries are the most recent ones."""
        max_stories = _mod.MAX_STORIES
        for i in range(max_stories + 5):
            _mod.upsert_story({"user_key": f"u{i}", "started_at": float(i)})
        stories = _mod.load_stories()
        keys = {s["user_key"] for s in stories}
        # The 5 oldest (u0..u4) should have been evicted.
        for i in range(5):
            assert f"u{i}" not in keys

    def test_upsert_write_is_atomic(self, tmp_stories_file):
        """The tmp file should not remain after a successful write."""
        _mod.upsert_story({"user_key": "u1", "started_at": 1.0})
        assert not os.path.exists(tmp_stories_file + ".tmp")

    def test_upsert_stories_sorted_by_started_at(self):
        """Stories should be ordered oldest-first in the file."""
        _mod.upsert_story({"user_key": "u2", "started_at": 2.0})
        _mod.upsert_story({"user_key": "u1", "started_at": 1.0})
        _mod.upsert_story({"user_key": "u3", "started_at": 3.0})
        stories = _mod.load_stories()
        times = [s["started_at"] for s in stories]
        assert times == sorted(times)

    def test_upsert_write_failure_logs_error(self, tmp_stories_file, caplog):
        """A write failure must log a visible error, not silently fail."""
        import logging

        with caplog.at_level(logging.ERROR, logger="dashboard.active_stories"):
            with patch("dashboard.active_stories.os.replace", side_effect=OSError("disk full")):
                _mod.upsert_story({"user_key": "u1", "started_at": 1.0})
        assert any("failed to write" in r.message for r in caplog.records)

    def test_upsert_concurrent_writes_do_not_corrupt(self):
        """Multiple threads upserting simultaneously must not corrupt the file."""
        errors: list[Exception] = []

        def worker(i: int) -> None:
            try:
                _mod.upsert_story({"user_key": f"u{i}", "started_at": float(i)})
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # All 10 entries should be present (10 < MAX_STORIES).
        assert len(_mod.load_stories()) == 10
