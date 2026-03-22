"""Tests for the dashboard application, including the _state_watcher background task."""

from __future__ import annotations

import os
import uuid
from unittest.mock import MagicMock, patch

import httpx
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


# ---------------------------------------------------------------------------
# /chat endpoint tests (user–bot web communication)
# ---------------------------------------------------------------------------


def _make_groq_error(exc_class, status_code: int = 400):
    """Build a real Groq error instance suitable for use as a side_effect."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.request = MagicMock()
    return exc_class("test error", response=mock_resp, body=None)


class TestWebChat:
    """Tests for the /chat endpoint (user–bot web communication)."""

    def _make_app(self):
        return create_app()

    @staticmethod
    def _mock_completion(text: str):
        """Return a MagicMock Groq completion with the given reply text."""
        mc = MagicMock()
        mc.choices = [MagicMock()]
        mc.choices[0].message.content = text
        return mc

    # ── 400 validation ──────────────────────────────────────────────────────

    def test_empty_message_returns_400(self):
        """Empty message string is rejected with HTTP 400."""
        app = self._make_app()
        with app.test_client() as c:
            resp = c.post("/chat", json={"message": "", "user_id": str(uuid.uuid4())})
        assert resp.status_code == 400
        assert "error" in resp.get_json()

    def test_whitespace_only_message_returns_400(self):
        """A message made entirely of whitespace is treated as empty and rejected."""
        app = self._make_app()
        with app.test_client() as c:
            resp = c.post("/chat", json={"message": "   ", "user_id": str(uuid.uuid4())})
        assert resp.status_code == 400

    def test_missing_message_key_returns_400(self):
        """A request body without a 'message' key returns HTTP 400."""
        app = self._make_app()
        with app.test_client() as c:
            resp = c.post("/chat", json={"user_id": str(uuid.uuid4())})
        assert resp.status_code == 400

    # ── 503 / missing API key ───────────────────────────────────────────────

    def test_missing_api_key_returns_503(self):
        """503 is returned when GROQ_API_KEY is absent from the environment."""
        app = self._make_app()
        env_without_key = {k: v for k, v in os.environ.items() if k != "GROQ_API_KEY"}
        with app.test_client() as c, patch.dict(os.environ, env_without_key, clear=True):
            resp = c.post("/chat", json={"message": "hello", "user_id": str(uuid.uuid4())})
        assert resp.status_code == 503
        assert "error" in resp.get_json()

    # ── successful reply ────────────────────────────────────────────────────

    def test_successful_reply_returns_200(self):
        """A valid request with a working Groq API key returns HTTP 200 with a reply."""
        app = self._make_app()
        completion = self._mock_completion("Your adventure begins!")
        user_id = str(uuid.uuid4())

        with (
            app.test_client() as c,
            patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}),
            patch("groq.Groq") as MockGroq,
        ):
            MockGroq.return_value.chat.completions.create.return_value = completion
            resp = c.post("/chat", json={"message": "start", "user_id": user_id})

        assert resp.status_code == 200
        assert resp.get_json()["reply"] == "Your adventure begins!"

    def test_reply_uses_configured_model(self):
        """The GROQ_MODEL env variable is forwarded to the Groq API call."""
        app = self._make_app()
        completion = self._mock_completion("Hello!")

        with (
            app.test_client() as c,
            patch.dict(os.environ, {"GROQ_API_KEY": "k", "GROQ_MODEL": "my-model"}),
            patch("groq.Groq") as MockGroq,
        ):
            MockGroq.return_value.chat.completions.create.return_value = completion
            c.post("/chat", json={"message": "hi", "user_id": str(uuid.uuid4())})
            call_kwargs = MockGroq.return_value.chat.completions.create.call_args.kwargs
            assert call_kwargs["model"] == "my-model"

    # ── Groq error handling ─────────────────────────────────────────────────

    def test_authentication_error_returns_503(self):
        """Groq AuthenticationError maps to HTTP 503."""
        from groq import AuthenticationError

        app = self._make_app()
        err = _make_groq_error(AuthenticationError, status_code=401)

        with (
            app.test_client() as c,
            patch.dict(os.environ, {"GROQ_API_KEY": "bad-key"}),
            patch("groq.Groq") as MockGroq,
        ):
            MockGroq.return_value.chat.completions.create.side_effect = err
            resp = c.post("/chat", json={"message": "hi", "user_id": str(uuid.uuid4())})

        assert resp.status_code == 503

    def test_rate_limit_error_returns_429(self):
        """Groq RateLimitError maps to HTTP 429."""
        from groq import RateLimitError

        app = self._make_app()
        err = _make_groq_error(RateLimitError, status_code=429)

        with (
            app.test_client() as c,
            patch.dict(os.environ, {"GROQ_API_KEY": "real-key"}),
            patch("groq.Groq") as MockGroq,
        ):
            MockGroq.return_value.chat.completions.create.side_effect = err
            resp = c.post("/chat", json={"message": "hi", "user_id": str(uuid.uuid4())})

        assert resp.status_code == 429

    def test_generic_groq_error_returns_503(self):
        """Any unexpected exception from the Groq client maps to HTTP 503."""
        app = self._make_app()

        with (
            app.test_client() as c,
            patch.dict(os.environ, {"GROQ_API_KEY": "real-key"}),
            patch("groq.Groq") as MockGroq,
        ):
            MockGroq.return_value.chat.completions.create.side_effect = RuntimeError("oops")
            resp = c.post("/chat", json={"message": "hi", "user_id": str(uuid.uuid4())})

        assert resp.status_code == 503

    # ── CORS ────────────────────────────────────────────────────────────────

    def test_options_preflight_returns_200_with_cors_headers(self):
        """OPTIONS pre-flight request returns HTTP 200 with CORS headers."""
        app = self._make_app()
        with app.test_client() as c:
            resp = c.options("/chat")
        assert resp.status_code == 200
        assert "Access-Control-Allow-Origin" in resp.headers

    def test_cors_headers_on_success_response(self):
        """Successful POST response includes the CORS Allow-Origin header."""
        app = self._make_app()
        completion = self._mock_completion("Go!")

        with (
            app.test_client() as c,
            patch.dict(os.environ, {"GROQ_API_KEY": "k"}),
            patch("groq.Groq") as MockGroq,
        ):
            MockGroq.return_value.chat.completions.create.return_value = completion
            resp = c.post("/chat", json={"message": "go", "user_id": str(uuid.uuid4())})

        assert "Access-Control-Allow-Origin" in resp.headers

    # ── input sanitisation ──────────────────────────────────────────────────

    def test_invalid_user_id_replaced_gracefully(self):
        """An invalid UUID user_id is silently replaced; the request still succeeds."""
        app = self._make_app()
        completion = self._mock_completion("Hello!")

        with (
            app.test_client() as c,
            patch.dict(os.environ, {"GROQ_API_KEY": "k"}),
            patch("groq.Groq") as MockGroq,
        ):
            MockGroq.return_value.chat.completions.create.return_value = completion
            resp = c.post("/chat", json={"message": "hi", "user_id": "not-a-uuid"})

        assert resp.status_code == 200

    def test_message_truncated_to_500_chars(self):
        """Messages longer than 500 characters are silently truncated before being sent."""
        app = self._make_app()
        long_msg = "x" * 600
        completion = self._mock_completion("Truncated!")

        with (
            app.test_client() as c,
            patch.dict(os.environ, {"GROQ_API_KEY": "k"}),
            patch("groq.Groq") as MockGroq,
        ):
            MockGroq.return_value.chat.completions.create.return_value = completion
            c.post("/chat", json={"message": long_msg, "user_id": str(uuid.uuid4())})
            call_msgs = MockGroq.return_value.chat.completions.create.call_args.kwargs[
                "messages"
            ]
            user_turn = next(m for m in call_msgs if m["role"] == "user")
            assert len(user_turn["content"]) == 500

    # ── conversation history ────────────────────────────────────────────────

    def test_conversation_history_preserved(self):
        """Subsequent requests with the same user_id include prior turns in the LLM context."""
        app = self._make_app()
        user_id = str(uuid.uuid4())
        first_reply = "Chapter 1 begins."
        second_reply = "Chapter 2 continues."

        with app.test_client() as c, patch.dict(os.environ, {"GROQ_API_KEY": "k"}):
            with patch("groq.Groq") as MockGroq:
                MockGroq.return_value.chat.completions.create.return_value = self._mock_completion(
                    first_reply
                )
                c.post("/chat", json={"message": "start", "user_id": user_id})

            with patch("groq.Groq") as MockGroq2:
                MockGroq2.return_value.chat.completions.create.return_value = self._mock_completion(
                    second_reply
                )
                c.post("/chat", json={"message": "go left", "user_id": user_id})
                second_call_msgs = MockGroq2.return_value.chat.completions.create.call_args.kwargs[
                    "messages"
                ]

        # The second LLM call should include the first user message and assistant reply.
        roles = [m["role"] for m in second_call_msgs]
        assert "user" in roles
        assert "assistant" in roles
        contents = [m["content"] for m in second_call_msgs]
        assert first_reply in contents
