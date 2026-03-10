"""Tests for the dashboard application, including the _state_watcher background task."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from dashboard.active_stories import STORIES_FILE as ACTIVE_STORIES_FILE
from dashboard.app import _state_watcher, create_app, socketio
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
            patch.object(
                socketio, "emit", lambda event, data: emissions.append((event, data))
            ),
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
            patch.object(
                socketio, "emit", lambda event, data: emissions.append((event, data))
            ),
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
            patch.object(
                socketio, "emit", lambda event, data: emissions.append((event, data))
            ),
            patch("dashboard.app.load_stories", return_value=[]),
            patch("dashboard.app.get_sessions", return_value=[]),
            patch("dashboard.app.get_status", return_value={"status": "offline"}),
            pytest.raises(StopIteration),
        ):
            _state_watcher(app)

        assert len(emissions) == 1, "Should emit when bot_state.json changes"
        assert emissions[0][0] == "story_update"
