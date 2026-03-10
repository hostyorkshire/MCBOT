"""Tests for the dashboard application, including the _state_watcher background task."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from dashboard.active_stories import STORIES_FILE as ACTIVE_STORIES_FILE
from dashboard.app import _merge_stories, _state_watcher, create_app, socketio
from dashboard.state import STATE_FILE

# ---------------------------------------------------------------------------
# _state_watcher tests
# ---------------------------------------------------------------------------


class TestStateWatcher:
    """Verify _state_watcher emits story_update on the correct file-change events."""

    def _make_app(self):
        return create_app()

    def test_emits_on_active_stories_change_only(self):
        """_state_watcher emits story_update when active_stories.json changes,
        even if bot_state.json has not changed."""
        app = self._make_app()

        _stories_call_count = [0]

        def mock_getmtime(path):
            if path == STATE_FILE:
                return 1000.0  # never changes
            if path == ACTIVE_STORIES_FILE:
                _stories_call_count[0] += 1
                # Seed call (count==1) returns 2000.0; loop call returns 2001.0
                return 2000.0 if _stories_call_count[0] == 1 else 2001.0
            raise OSError(f"unexpected path: {path}")

        emissions = []

        def mock_sleep(_seconds):
            raise StopIteration

        with (
            patch("os.path.getmtime", mock_getmtime),
            patch.object(socketio, "sleep", mock_sleep),
            patch.object(socketio, "emit", lambda event, data: emissions.append((event, data))),
            patch("dashboard.app.load_stories", return_value=[]),
            patch("dashboard.app.get_sessions", return_value=[]),
            patch("dashboard.app.get_status", return_value={"status": "offline"}),
            pytest.raises(StopIteration),
        ):
            _state_watcher(app)

        assert len(emissions) == 1, "Should emit exactly once when active_stories.json changes"
        event, data = emissions[0]
        assert event == "story_update"
        assert "stories" in data
        assert "status" in data

    def test_does_not_emit_when_neither_file_changes(self):
        """_state_watcher must not emit when neither file has changed."""
        app = self._make_app()

        def mock_getmtime(_path):
            return 1000.0  # nothing changes

        emissions = []

        def mock_sleep(_seconds):
            raise StopIteration

        with (
            patch("os.path.getmtime", mock_getmtime),
            patch.object(socketio, "sleep", mock_sleep),
            patch.object(socketio, "emit", lambda event, data: emissions.append((event, data))),
            patch("dashboard.app.load_stories", return_value=[]),
            patch("dashboard.app.get_sessions", return_value=[]),
            patch("dashboard.app.get_status", return_value={"status": "offline"}),
            pytest.raises(StopIteration),
        ):
            _state_watcher(app)

        assert len(emissions) == 0, "Should not emit when neither file changes"

    def test_emits_on_bot_state_change_only(self):
        """_state_watcher still emits when only bot_state.json changes."""
        app = self._make_app()

        _state_call_count = [0]

        def mock_getmtime(path):
            if path == STATE_FILE:
                _state_call_count[0] += 1
                # Seed call (count==1) returns 1000.0; loop call returns 1001.0
                return 1000.0 if _state_call_count[0] == 1 else 1001.0
            if path == ACTIVE_STORIES_FILE:
                return 2000.0  # never changes
            raise OSError(f"unexpected path: {path}")

        emissions = []

        def mock_sleep(_seconds):
            raise StopIteration

        with (
            patch("os.path.getmtime", mock_getmtime),
            patch.object(socketio, "sleep", mock_sleep),
            patch.object(socketio, "emit", lambda event, data: emissions.append((event, data))),
            patch("dashboard.app.load_stories", return_value=[]),
            patch("dashboard.app.get_sessions", return_value=[]),
            patch("dashboard.app.get_status", return_value={"status": "offline"}),
            pytest.raises(StopIteration),
        ):
            _state_watcher(app)

        assert len(emissions) == 1, "Should emit when bot_state.json changes"
        assert emissions[0][0] == "story_update"


# ---------------------------------------------------------------------------
# _merge_stories tests
# ---------------------------------------------------------------------------


class TestMergeStories:
    """Verify that _merge_stories correctly handles the race condition between
    active_stories.json (persisted) and bot_state.json (active sessions)."""

    def test_finished_story_not_overwritten_by_stale_active_session(self):
        """A finished story in active_stories.json must not be reverted to
        active by a stale session from bot_state.json."""
        persisted = [{"user_key": "u1", "user_name": "Alice", "finished": True, "started_at": 1.0}]
        stale_active = [
            {"user_key": "u1", "user_name": "Alice", "finished": False, "started_at": 1.0}
        ]

        with (
            patch("dashboard.app.load_stories", return_value=persisted),
            patch("dashboard.app.get_sessions", return_value=stale_active),
        ):
            result = _merge_stories()

        assert len(result) == 1
        assert result[0]["finished"] is True

    def test_active_session_shown_when_no_persisted_entry(self):
        """An active session with no matching persisted entry should appear."""
        active = [{"user_key": "u1", "user_name": "Alice", "finished": False, "started_at": 1.0}]

        with (
            patch("dashboard.app.load_stories", return_value=[]),
            patch("dashboard.app.get_sessions", return_value=active),
        ):
            result = _merge_stories()

        assert len(result) == 1
        assert result[0]["finished"] is False

    def test_finished_active_session_overrides_persisted(self):
        """An active session with finished=True should override a persisted entry."""
        persisted = [
            {
                "user_key": "u1",
                "user_name": "Alice",
                "finished": False,
                "started_at": 1.0,
                "chapter": 1,
            }
        ]
        active = [
            {
                "user_key": "u1",
                "user_name": "Alice",
                "finished": True,
                "started_at": 1.0,
                "chapter": 3,
            }
        ]

        with (
            patch("dashboard.app.load_stories", return_value=persisted),
            patch("dashboard.app.get_sessions", return_value=active),
        ):
            result = _merge_stories()

        assert len(result) == 1
        assert result[0]["finished"] is True
        assert result[0]["chapter"] == 3

    def test_active_overrides_active_persisted(self):
        """When both persisted and active are unfinished, active wins (fresher data)."""
        persisted = [
            {
                "user_key": "u1",
                "user_name": "Alice",
                "finished": False,
                "started_at": 1.0,
                "chapter": 1,
            }
        ]
        active = [
            {
                "user_key": "u1",
                "user_name": "Alice",
                "finished": False,
                "started_at": 1.0,
                "chapter": 2,
            }
        ]

        with (
            patch("dashboard.app.load_stories", return_value=persisted),
            patch("dashboard.app.get_sessions", return_value=active),
        ):
            result = _merge_stories()

        assert len(result) == 1
        assert result[0]["chapter"] == 2

    def test_multiple_users_merged_correctly(self):
        """Stories from different users are all included in the result."""
        persisted = [{"user_key": "u1", "user_name": "Alice", "finished": True, "started_at": 1.0}]
        active = [{"user_key": "u2", "user_name": "Bob", "finished": False, "started_at": 2.0}]

        with (
            patch("dashboard.app.load_stories", return_value=persisted),
            patch("dashboard.app.get_sessions", return_value=active),
        ):
            result = _merge_stories()

        assert len(result) == 2
        keys = {s["user_key"] for s in result}
        assert keys == {"u1", "u2"}

    def test_results_sorted_newest_first(self):
        """Merged stories should be returned newest-first."""
        persisted = [
            {"user_key": "u1", "user_name": "Alice", "finished": True, "started_at": 1.0},
            {"user_key": "u2", "user_name": "Bob", "finished": True, "started_at": 3.0},
        ]
        active = [{"user_key": "u3", "user_name": "Carol", "finished": False, "started_at": 2.0}]

        with (
            patch("dashboard.app.load_stories", return_value=persisted),
            patch("dashboard.app.get_sessions", return_value=active),
        ):
            result = _merge_stories()

        assert [s["user_key"] for s in result] == ["u2", "u3", "u1"]

    def test_sessions_without_user_key_are_skipped(self):
        """Active sessions missing a user_key should be ignored."""
        active = [{"user_name": "NoKey", "finished": False, "started_at": 1.0}]

        with (
            patch("dashboard.app.load_stories", return_value=[]),
            patch("dashboard.app.get_sessions", return_value=active),
        ):
            result = _merge_stories()

        assert len(result) == 0
