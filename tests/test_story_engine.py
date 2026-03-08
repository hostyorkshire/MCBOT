"""Tests for story_engine.StoryEngine and Session."""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from story_engine import (
    _CHAPTER_CHOICE_SUFFIX,
    DEFAULT_GENRE,
    DOOM_MAX,
    MAX_CHAPTERS,
    SCENES_PER_CHAPTER,
    Session,
    StoryEngine,
    _format_reply,
    classify_choice,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_groq(
    reply: str = "You stand at a crossroads.\n1. Go left\n2. Go right\n3. Wait",
) -> MagicMock:
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

    def test_awaiting_chapter_choice_defaults_false(self):
        s = Session("u1", "Alice", max_history=5)
        assert s.awaiting_chapter_choice is False


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
        engine._client.chat.completions.create = AsyncMock(side_effect=RuntimeError("timeout"))
        await engine.start_story("u1", "Frank")
        # Re-inject mock with exception for the advance call
        engine._client.chat.completions.create = AsyncMock(side_effect=RuntimeError("timeout"))
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
# Pacing constants sanity checks
# ---------------------------------------------------------------------------


class TestPacingConstants:
    def test_doom_max_is_positive(self):
        assert DOOM_MAX > 0

    def test_scenes_per_chapter_is_positive(self):
        assert SCENES_PER_CHAPTER > 0

    def test_scenes_per_chapter_at_least_150(self):
        assert SCENES_PER_CHAPTER >= 150

    def test_max_chapters_is_positive(self):
        assert MAX_CHAPTERS > 0

    def test_doom_max_allows_multiple_scenes(self):
        # A single scene at chapter 1 adds at most 1 (baseline) + 2 (high risk) = 3 doom.
        # DOOM_MAX must allow at least a few scenes before triggering.
        assert DOOM_MAX >= SCENES_PER_CHAPTER * 2


# ---------------------------------------------------------------------------
# classify_choice unit tests
# ---------------------------------------------------------------------------


class TestClassifyChoice:
    def test_risky_choice_returns_two(self):
        assert classify_choice("attack the guard") == 2

    def test_safe_choice_returns_zero(self):
        assert classify_choice("hide behind the barrel") == 0

    def test_neutral_choice_returns_one(self):
        assert classify_choice("go through the door") == 1

    def test_numeric_choice_returns_one(self):
        assert classify_choice("1") == 1

    def test_case_insensitive(self):
        assert classify_choice("FIGHT the dragon") == 2

    def test_empty_string_returns_one(self):
        assert classify_choice("") == 1


# ---------------------------------------------------------------------------
# Chapter-boundary behaviour tests
# ---------------------------------------------------------------------------


def _engine_at_chapter_boundary(cliffhanger_reply: str = "Darkness falls\u2026") -> StoryEngine:
    """Return an engine whose session is one scene away from a chapter boundary."""
    with patch("story_engine.AsyncGroq") as mock_cls:
        mock_cls.return_value = _make_mock_groq()
        eng = StoryEngine(api_key="fake-key")

    session = Session("u1", "Hero", max_history=10)
    # Preload the session at the last scene of the first chapter.
    session.chapter = 1
    session.scene_in_chapter = SCENES_PER_CHAPTER - 1
    eng._sessions["u1"] = session
    # Point the mock client at the cliffhanger reply.
    eng._client = _make_mock_groq(cliffhanger_reply)
    return eng


class TestChapterBoundary:
    @pytest.mark.asyncio
    async def test_chapter_end_sets_awaiting_chapter_choice(self):
        """Reaching SCENES_PER_CHAPTER triggers awaiting_chapter_choice, not a cooldown."""
        eng = _engine_at_chapter_boundary()
        await eng.advance_story("u1", "1")
        session = eng._sessions["u1"]
        assert session.awaiting_chapter_choice is True
        assert session.continue_after_ts is None

    @pytest.mark.asyncio
    async def test_chapter_end_reply_contains_continue_pause_end(self):
        """The chapter-boundary reply includes the three fixed choices."""
        eng = _engine_at_chapter_boundary("Shadows close in.")
        reply = await eng.advance_story("u1", "1")
        assert "1." in reply
        assert "2." in reply
        assert "3." in reply
        assert _CHAPTER_CHOICE_SUFFIX in reply

    @pytest.mark.asyncio
    async def test_chapter_end_advances_chapter_counter(self):
        """Chapter number increments and scene_in_chapter resets when boundary is hit."""
        eng = _engine_at_chapter_boundary()
        await eng.advance_story("u1", "1")
        session = eng._sessions["u1"]
        assert session.chapter == 2
        assert session.scene_in_chapter == 0

    @pytest.mark.asyncio
    async def test_choice_1_continues_story(self):
        """Selecting 1 (Continue) clears the flag and returns the next scene."""
        next_scene = "You press on.\n1. Climb\n2. Swim\n3. Wait"
        eng = _engine_at_chapter_boundary()
        await eng.advance_story("u1", "1")  # trigger boundary
        eng._client = _make_mock_groq(next_scene)
        reply = await eng.advance_story("u1", "1")  # Continue
        session = eng._sessions["u1"]
        assert session.awaiting_chapter_choice is False
        assert session.finished is False
        assert reply == next_scene

    @pytest.mark.asyncio
    async def test_choice_2_pauses_without_cooldown(self):
        """Selecting 2 (Pause) clears the flag and does not set a cooldown gate."""
        eng = _engine_at_chapter_boundary()
        await eng.advance_story("u1", "1")  # trigger boundary
        reply = await eng.advance_story("u1", "2")  # Pause
        session = eng._sessions["u1"]
        assert session.awaiting_chapter_choice is False
        assert session.continue_after_ts is None
        assert session.finished is False
        assert "continue" in reply.lower()

    @pytest.mark.asyncio
    async def test_choice_3_ends_story(self):
        """Selecting 3 (End) marks the story finished and returns an end screen."""
        eng = _engine_at_chapter_boundary()
        await eng.advance_story("u1", "1")  # trigger boundary
        reply = await eng.advance_story("u1", "3")  # End
        session = eng._sessions["u1"]
        assert session.awaiting_chapter_choice is False
        assert session.finished is True
        assert "[END]" in reply

    @pytest.mark.asyncio
    async def test_unknown_input_at_chapter_boundary_reshows_prompt(self):
        """Any input that is not 1/2/3 while awaiting_chapter_choice re-shows the prompt."""
        eng = _engine_at_chapter_boundary()
        await eng.advance_story("u1", "1")  # trigger boundary
        reply = await eng.advance_story("u1", "maybe later")
        assert eng._sessions["u1"].awaiting_chapter_choice is True
        assert "1." in reply or "Continue" in reply

    @pytest.mark.asyncio
    async def test_no_24h_cooldown_after_chapter_end(self):
        """The 24-hour cooldown gate must never be set at a chapter boundary."""
        eng = _engine_at_chapter_boundary()
        await eng.advance_story("u1", "1")  # trigger boundary
        session = eng._sessions["u1"]
        assert session.continue_after_ts is None


# ---------------------------------------------------------------------------
# Session serialisation (to_dict / from_dict)
# ---------------------------------------------------------------------------


class TestSessionSerialisation:
    def test_to_dict_contains_all_fields(self):
        s = Session("u1", "Alice", max_history=5)
        s.chapter = 3
        s.scene_in_chapter = 7
        s.doom = 42
        s.finished = True
        s.genre = "horror"
        d = s.to_dict()
        assert d["user_key"] == "u1"
        assert d["user_name"] == "Alice"
        assert d["max_history"] == 5
        assert d["chapter"] == 3
        assert d["scene_in_chapter"] == 7
        assert d["doom"] == 42
        assert d["finished"] is True
        assert d["genre"] == "horror"
        assert "history" in d
        assert "started_at" in d

    def test_from_dict_round_trip(self):
        s = Session("u2", "Bob", max_history=8)
        s.add_message("user", "hello")
        s.add_message("assistant", "world")
        s.chapter = 2
        s.doom = 10
        s.genre = "cozy"
        d = s.to_dict()
        restored = Session.from_dict(d)
        assert restored.user_key == s.user_key
        assert restored.user_name == s.user_name
        assert restored.max_history == s.max_history
        assert restored.history == s.history
        assert restored.chapter == s.chapter
        assert restored.doom == s.doom
        assert restored.genre == s.genre
        assert restored.finished == s.finished

    def test_from_dict_missing_optional_keys_use_defaults(self):
        minimal = {"user_key": "u3", "user_name": "Charlie"}
        s = Session.from_dict(minimal)
        assert s.chapter == 1
        assert s.scene_in_chapter == 0
        assert s.doom == 0
        assert s.finished is False
        assert s.awaiting_chapter_choice is False
        assert s.continue_after_ts is None
        assert s.genre == DEFAULT_GENRE
        assert s.history == []

    def test_from_dict_preserves_history(self):
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        d = {"user_key": "u4", "user_name": "Dana", "history": msgs}
        s = Session.from_dict(d)
        assert s.history == msgs


# ---------------------------------------------------------------------------
# StoryEngine session persistence (save_sessions / load_sessions)
# ---------------------------------------------------------------------------


def _make_engine_with_file(sessions_file: str) -> StoryEngine:
    """Return a StoryEngine backed by *sessions_file* with a mocked Groq client."""
    with patch("story_engine.AsyncGroq") as mock_cls:
        mock_cls.return_value = _make_mock_groq()
        eng = StoryEngine(api_key="fake-key", sessions_file=sessions_file)
    eng._client = _make_mock_groq()
    return eng


class TestSessionPersistence:
    def test_save_sessions_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "sessions.json")
            eng = _make_engine_with_file(path)
            eng._sessions["u1"] = Session("u1", "Alice", max_history=5)
            eng.save_sessions()
            assert os.path.exists(path)

    def test_save_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "sessions.json")
            eng = _make_engine_with_file(path)
            s = Session("u1", "Alice", max_history=5)
            s.add_message("user", "hello")
            s.chapter = 2
            s.doom = 15
            eng._sessions["u1"] = s
            eng.save_sessions()

            # Load into a fresh engine.
            eng2 = _make_engine_with_file(path)
            assert eng2.has_session("u1")
            restored = eng2._sessions["u1"]
            assert restored.user_name == "Alice"
            assert restored.chapter == 2
            assert restored.doom == 15
            assert restored.history == s.history

    def test_load_sessions_missing_file_is_safe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "no_such_file.json")
            # Should not raise – file simply doesn't exist yet.
            eng = _make_engine_with_file(path)
            assert not eng.has_session("u1")

    def test_load_sessions_corrupt_file_is_safe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "sessions.json")
            with open(path, "w") as fh:
                fh.write("not valid json {{{{")
            # Should not raise – bad JSON is logged and ignored.
            eng = _make_engine_with_file(path)
            assert not eng.has_session("u1")

    def test_load_sessions_skips_corrupt_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "sessions.json")
            # One valid entry, one invalid (missing required keys).
            data = {
                "u1": {"user_key": "u1", "user_name": "Alice"},
                "u2": None,  # invalid – cannot call .get() on None
            }
            with open(path, "w") as fh:
                json.dump(data, fh)
            eng = _make_engine_with_file(path)
            # u1 loads fine; u2 is silently skipped.
            assert eng.has_session("u1")
            assert not eng.has_session("u2")

    def test_save_sessions_no_file_configured_does_nothing(self):
        with patch("story_engine.AsyncGroq") as mock_cls:
            mock_cls.return_value = _make_mock_groq()
            eng = StoryEngine(api_key="fake-key")  # no sessions_file
        eng._sessions["u1"] = Session("u1", "Alice", max_history=5)
        # Must not raise even without a configured file.
        eng.save_sessions()

    def test_clear_session_saves_to_disk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "sessions.json")
            eng = _make_engine_with_file(path)
            eng._sessions["u1"] = Session("u1", "Alice", max_history=5)
            eng._sessions["u2"] = Session("u2", "Bob", max_history=5)
            eng.save_sessions()

            eng.clear_session("u1")

            # Reload from disk and verify u1 is gone.
            eng2 = _make_engine_with_file(path)
            assert not eng2.has_session("u1")
            assert eng2.has_session("u2")

    @pytest.mark.asyncio
    async def test_start_story_persists_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "sessions.json")
            eng = _make_engine_with_file(path)
            await eng.start_story("u1", "Alice")
            assert os.path.exists(path)
            with open(path) as fh:
                data = json.load(fh)
            assert "u1" in data

    @pytest.mark.asyncio
    async def test_advance_story_persists_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "sessions.json")
            eng = _make_engine_with_file(path)
            await eng.start_story("u1", "Alice")
            await eng.advance_story("u1", "1")
            with open(path) as fh:
                data = json.load(fh)
            # History should contain the advance turn.
            assert len(data["u1"]["history"]) >= 4

    @pytest.mark.asyncio
    async def test_session_survives_engine_restart(self):
        """A session saved by one engine can be resumed by a fresh engine."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "sessions.json")

            eng1 = _make_engine_with_file(path)
            await eng1.start_story("u1", "Alice", genre="horror")
            await eng1.advance_story("u1", "2")

            saved_chapter = eng1._sessions["u1"].chapter
            saved_doom = eng1._sessions["u1"].doom

            # Simulate a restart by creating a brand-new engine from the same file.
            eng2 = _make_engine_with_file(path)
            assert eng2.has_session("u1")
            restored = eng2._sessions["u1"]
            assert restored.user_name == "Alice"
            assert restored.genre == "horror"
            assert restored.chapter == saved_chapter
            assert restored.doom == saved_doom
