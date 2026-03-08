"""Tests for story_engine.StoryEngine and Session."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from story_engine import Session, StoryEngine, _format_reply


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_groq(reply: str = "You stand at a crossroads.\n1. Go left\n2. Go right\n3. Wait") -> MagicMock:
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
        expected = "Dark cave ahead.\n1. Enter\n2. Run\n3. Shout"
        engine._client = _make_mock_groq(expected)
        result = await engine.start_story("u1", "Bob")
        assert result == expected

    @pytest.mark.asyncio
    async def test_advance_story_no_session_returns_hint(self, engine: StoryEngine):
        result = await engine.advance_story("nobody", "1")
        assert "start" in result.lower()

    @pytest.mark.asyncio
    async def test_advance_story_valid_choice(self, engine: StoryEngine):
        expected = "You stepped into the cave.\n1. Go deeper\n2. Back\n3. Listen"
        engine._client = _make_mock_groq(expected)
        await engine.start_story("u1", "Carol")
        result = await engine.advance_story("u1", "1")
        assert result == expected

    @pytest.mark.asyncio
    async def test_advance_story_free_text(self, engine: StoryEngine):
        expected = "Interesting choice!\n1. Continue\n2. Stop\n3. Look around"
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

    @pytest.mark.asyncio
    async def test_llm_reply_choices_formatted_on_separate_lines(self, engine: StoryEngine):
        """_call_llm must post-process inline choices into separate lines."""
        raw = "Dark forest. 1) Run  2) Hide  3) Fight"
        engine._client = _make_mock_groq(raw)
        result = await engine.start_story("u1", "Grace")
        assert "\n1. " in result
        assert "\n2. " in result
        assert "\n3. " in result


# ---------------------------------------------------------------------------
# _format_reply unit tests
# ---------------------------------------------------------------------------


class TestFormatReply:
    def test_already_correct_format_unchanged(self):
        text = "Dark forest.\n1. Run\n2. Hide\n3. Fight"
        assert _format_reply(text) == text

    def test_inline_paren_choices_reformatted(self):
        text = "Dark forest. 1) Run  2) Hide  3) Fight"
        result = _format_reply(text)
        assert result == "Dark forest.\n1. Run\n2. Hide\n3. Fight"

    def test_inline_dot_choices_reformatted(self):
        text = "Cave ahead. 1. Enter  2. Run  3. Shout"
        result = _format_reply(text)
        assert result == "Cave ahead.\n1. Enter\n2. Run\n3. Shout"

    def test_multiline_paren_choices_converted_to_dot(self):
        text = "Dark forest.\n1) Run\n2) Hide\n3) Fight"
        result = _format_reply(text)
        assert result == "Dark forest.\n1. Run\n2. Hide\n3. Fight"

    def test_end_scene_inline_choices_reformatted(self):
        text = "[END] 1) Restart  2) New adventure  3) Quit"
        result = _format_reply(text)
        assert result == "[END]\n1. Restart\n2. New adventure\n3. Quit"

    def test_choices_each_on_own_line(self):
        text = "Story.\n1. A\n2. B\n3. C"
        lines = _format_reply(text).splitlines()
        assert lines[-3].startswith("1.")
        assert lines[-2].startswith("2.")
        assert lines[-1].startswith("3.")

    def test_text_without_choices_returned_unchanged(self):
        text = "No choices here."
        assert _format_reply(text) == text


# ---------------------------------------------------------------------------
# StoryEngine – genre support tests
# ---------------------------------------------------------------------------


class TestStoryEngineGenre:
    @pytest.mark.asyncio
    async def test_start_story_default_genre_prompt_includes_wasteland(self, engine: StoryEngine):
        """Default genre ('wasteland') appears in the opening user prompt."""
        await engine.start_story("u1", "Alice")
        first_msg = engine._sessions["u1"].history[0]
        assert first_msg["role"] == "user"
        assert "wasteland" in first_msg["content"].lower() or "post-apoc" in first_msg["content"].lower()

    @pytest.mark.asyncio
    async def test_start_story_horror_genre_in_prompt(self, engine: StoryEngine):
        """Selecting horror genre includes 'horror' in the opening prompt."""
        await engine.start_story("u1", "Bob", genre="horror")
        first_msg = engine._sessions["u1"].history[0]
        assert "horror" in first_msg["content"].lower()

    @pytest.mark.asyncio
    async def test_start_story_cozy_genre_in_prompt(self, engine: StoryEngine):
        """Selecting cozy genre includes relevant description in prompt."""
        await engine.start_story("u1", "Carol", genre="cozy")
        first_msg = engine._sessions["u1"].history[0]
        assert "cozy" in first_msg["content"].lower()

    @pytest.mark.asyncio
    async def test_start_story_mil_genre_in_prompt(self, engine: StoryEngine):
        """Selecting mil genre includes relevant description in prompt."""
        await engine.start_story("u1", "Dave", genre="mil")
        first_msg = engine._sessions["u1"].history[0]
        assert "mil" in first_msg["content"].lower() or "military" in first_msg["content"].lower()

    @pytest.mark.asyncio
    async def test_start_story_comedy_genre_in_prompt(self, engine: StoryEngine):
        """Selecting comedy genre includes relevant description in prompt."""
        await engine.start_story("u1", "Eve", genre="comedy")
        first_msg = engine._sessions["u1"].history[0]
        assert "comedy" in first_msg["content"].lower()

    @pytest.mark.asyncio
    async def test_start_story_unknown_genre_falls_back_to_default(self, engine: StoryEngine):
        """Unknown genre falls back to the default genre without raising.

        This is defensive programming: the bot layer validates genres before
        calling start_story(), but the engine should remain safe if called
        directly with an unrecognised genre ID.
        """
        await engine.start_story("u1", "Frank", genre="notreal")
        assert engine.has_session("u1")
        first_msg = engine._sessions["u1"].history[0]
        # Falls back to wasteland default
        assert "post-apoc" in first_msg["content"].lower() or "wasteland" in first_msg["content"].lower()

    @pytest.mark.asyncio
    async def test_start_story_genre_kwarg_omitted_uses_default(self, engine: StoryEngine):
        """Omitting genre kwarg behaves identically to genre='wasteland'."""
        expected = "Dark cave.\n1. Enter\n2. Run\n3. Shout"
        engine._client = _make_mock_groq(expected)
        result = await engine.start_story("u1", "Grace")
        assert result == expected

    def test_genres_dict_has_required_keys(self):
        from story_engine import GENRES, DEFAULT_GENRE
        for gid, info in GENRES.items():
            assert "name" in info
            assert "desc" in info
        assert DEFAULT_GENRE in GENRES

    def test_default_genre_is_wasteland(self):
        from story_engine import DEFAULT_GENRE
        assert DEFAULT_GENRE == "wasteland"

    def test_genres_contains_all_required(self):
        from story_engine import GENRES
        required = {"wasteland", "cozy", "horror", "mil", "comedy"}
        assert required <= set(GENRES.keys())
