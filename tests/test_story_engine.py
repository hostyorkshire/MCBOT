"""Tests for story_engine.StoryEngine and Session."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from story_engine import Session, StoryEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_groq(reply: str = "You stand at a crossroads. 1) Go left 2) Go right 3) Wait") -> MagicMock:
    """Return a MagicMock that mimics the AsyncGroq client."""
    mock_choice = MagicMock()
    mock_choice.message.content = reply

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    mock_completions = MagicMock()
    mock_completions.create = AsyncMock(return_value=mock_response)

    mock_chat = MagicMock()
    mock_chat.completions = mock_completions

    mock_client = MagicMock()
    mock_client.chat = mock_chat

    return mock_client


@pytest.fixture()
def engine() -> StoryEngine:
    """StoryEngine with a mocked AsyncGroq client."""
    with patch("story_engine.AsyncGroq") as mock_cls:
        mock_cls.return_value = _make_mock_groq()
        eng = StoryEngine(api_key="fake-key")
    # Attach the mock so tests can inspect calls
    eng._client = _make_mock_groq()
    return eng


# ---------------------------------------------------------------------------
# Session unit tests
# ---------------------------------------------------------------------------


class TestSession:
    def test_initial_history_is_empty(self):
        s = Session("u1", "Alice", max_history=5)
        assert s.history == []

    def test_add_message_appends(self):
        s = Session("u1", "Alice", max_history=10)
        s.add_message("user", "hello")
        assert len(s.history) == 1
        assert s.history[0] == {"role": "user", "content": "hello"}

    def test_history_bounded_by_max_history(self):
        s = Session("u1", "Alice", max_history=4)
        for i in range(10):
            s.add_message("user", f"msg {i}")
        assert len(s.history) <= 4

    def test_first_message_preserved_when_trimming(self):
        s = Session("u1", "Alice", max_history=3)
        s.add_message("user", "first")
        for i in range(10):
            s.add_message("assistant", f"reply {i}")
        assert s.history[0]["content"] == "first"

    def test_get_messages_returns_copy(self):
        s = Session("u1", "Alice", max_history=5)
        s.add_message("user", "hello")
        msgs = s.get_messages()
        msgs.append({"role": "user", "content": "extra"})
        assert len(s.history) == 1  # original unchanged


# ---------------------------------------------------------------------------
# StoryEngine unit tests
# ---------------------------------------------------------------------------


class TestStoryEngineSession:
    def test_has_session_false_initially(self, engine: StoryEngine):
        assert not engine.has_session("u1")

    @pytest.mark.asyncio
    async def test_start_story_creates_session(self, engine: StoryEngine):
        await engine.start_story("u1", "Alice")
        assert engine.has_session("u1")

    @pytest.mark.asyncio
    async def test_start_story_replaces_existing_session(self, engine: StoryEngine):
        await engine.start_story("u1", "Alice")
        old_session = engine._sessions["u1"]
        await engine.start_story("u1", "Alice")
        assert engine._sessions["u1"] is not old_session

    def test_clear_session_removes_session(self, engine: StoryEngine):
        engine._sessions["u1"] = Session("u1", "Alice", max_history=5)
        engine.clear_session("u1")
        assert not engine.has_session("u1")

    def test_clear_nonexistent_session_is_safe(self, engine: StoryEngine):
        engine.clear_session("no-such-user")  # must not raise


class TestStoryEngineStory:
    @pytest.mark.asyncio
    async def test_start_story_returns_llm_reply(self, engine: StoryEngine):
        expected = "Dark cave ahead. 1) Enter 2) Run 3) Shout"
        engine._client = _make_mock_groq(expected)
        result = await engine.start_story("u1", "Bob")
        assert result == expected

    @pytest.mark.asyncio
    async def test_advance_story_no_session_returns_hint(self, engine: StoryEngine):
        result = await engine.advance_story("nobody", "1")
        assert "start" in result.lower()

    @pytest.mark.asyncio
    async def test_advance_story_valid_choice(self, engine: StoryEngine):
        expected = "You stepped into the cave. 1) Go deeper 2) Back 3) Listen"
        engine._client = _make_mock_groq(expected)
        await engine.start_story("u1", "Carol")
        result = await engine.advance_story("u1", "1")
        assert result == expected

    @pytest.mark.asyncio
    async def test_advance_story_free_text(self, engine: StoryEngine):
        expected = "Interesting choice! 1) Continue 2) Stop 3) Look around"
        engine._client = _make_mock_groq(expected)
        await engine.start_story("u1", "Dave")
        result = await engine.advance_story("u1", "I try to pick the lock")
        assert result == expected

    @pytest.mark.asyncio
    async def test_advance_story_records_history(self, engine: StoryEngine):
        await engine.start_story("u1", "Eve")
        await engine.advance_story("u1", "2")
        # history: [start prompt, llm reply, choice prompt, llm reply] = 4
        assert len(engine._sessions["u1"].history) == 4

    @pytest.mark.asyncio
    async def test_api_error_returns_fallback_message(self, engine: StoryEngine):
        engine._client.chat.completions.create = AsyncMock(
            side_effect=RuntimeError("timeout")
        )
        await engine.start_story("u1", "Frank")
        # Re-inject mock with exception for the advance call
        engine._client.chat.completions.create = AsyncMock(
            side_effect=RuntimeError("timeout")
        )
        result = await engine.advance_story("u1", "1")
        assert "API error" in result or "error" in result.lower()
