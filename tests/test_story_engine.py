"""Tests for story_engine.StoryEngine and Session."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from story_engine import (
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
# classify_choice unit tests
# ---------------------------------------------------------------------------


class TestClassifyChoice:
    def test_high_risk_attack(self):
        assert classify_choice("I attack the guard") == 2

    def test_high_risk_run(self):
        assert classify_choice("Run towards the gate!") == 2

    def test_high_risk_jump(self):
        assert classify_choice("Jump across the chasm") == 2

    def test_low_risk_hide(self):
        assert classify_choice("I hide behind the rock") == 0

    def test_low_risk_wait(self):
        assert classify_choice("Wait and see what happens") == 0

    def test_low_risk_listen(self):
        assert classify_choice("Listen at the door") == 0

    def test_medium_risk_default(self):
        assert classify_choice("I go to the market") == 1

    def test_numeric_choice_default_medium(self):
        assert classify_choice("2") == 1

    def test_case_insensitive(self):
        assert classify_choice("ATTACK NOW") == 2
        assert classify_choice("HIDE in shadows") == 0


# ---------------------------------------------------------------------------
# Pacing / doom system tests
# ---------------------------------------------------------------------------


_STORY_REPLY = "You stand at a crossroads.\n1. Go left\n2. Go right\n3. Wait"
_PERIL_REPLY = "Darkness takes you. [END]\n1. Start over\n2. New adventure\n3. Quit"
_CLIFFHANGER_REPLY = "Night falls. The path seals. Return tomorrow to continue."


class TestPacing:
    """Tests for the invisible doom/pacing system in StoryEngine.advance_story."""

    @pytest.mark.asyncio
    async def test_peril_finale_when_doom_reaches_max(self, engine: StoryEngine):
        """doom >= DOOM_MAX triggers a peril finale and marks session finished."""
        engine._client = _make_mock_groq(_PERIL_REPLY)
        await engine.start_story("u1", "Alice")
        session = engine._sessions["u1"]
        # Pre-load doom so that one 'attack' advance (chapter=1 + risk=2 = 3) tips it over.
        session.doom = DOOM_MAX - 3
        session.scene_in_chapter = 5  # well below chapter end

        result = await engine.advance_story("u1", "attack the monster")

        assert session.finished is True
        assert session.doom >= DOOM_MAX
        assert result == _PERIL_REPLY

    @pytest.mark.asyncio
    async def test_finished_story_returns_over_message_without_llm(
        self, engine: StoryEngine
    ):
        """A finished story must return a 'story over' message without calling LLM."""
        await engine.start_story("u1", "Bob")
        session = engine._sessions["u1"]
        session.finished = True
        # Ensure LLM would not be called (make it raise if it were).
        engine._client.chat.completions.create = AsyncMock(
            side_effect=AssertionError("LLM should not be called")
        )

        result = await engine.advance_story("u1", "1")

        assert "start" in result.lower()
        assert session.finished is True  # unchanged

    @pytest.mark.asyncio
    async def test_chapter_end_sets_cliffhanger_and_gate(self, engine: StoryEngine):
        """Reaching SCENES_PER_CHAPTER generates a cliffhanger and sets the 24 h gate."""
        engine._client = _make_mock_groq(_CLIFFHANGER_REPLY)
        await engine.start_story("u1", "Carol")
        session = engine._sessions["u1"]
        session.scene_in_chapter = SCENES_PER_CHAPTER - 1  # one scene away from end
        session.doom = 0  # keep doom low so doom branch won't fire

        before = time.time()
        result = await engine.advance_story("u1", "1")
        after = time.time()

        assert result == _CLIFFHANGER_REPLY
        assert session.chapter == 2
        assert session.scene_in_chapter == 0
        assert session.continue_after_ts is not None
        # Gate should be approximately 24 h from now.
        from story_engine import CHAPTER_COOLDOWN
        assert before + CHAPTER_COOLDOWN - 10 <= session.continue_after_ts <= after + CHAPTER_COOLDOWN + 10
        assert not session.finished

    @pytest.mark.asyncio
    async def test_continuation_blocked_before_gate_expires(self, engine: StoryEngine):
        """Sending a message before the 24 h gate returns an in-world wait message."""
        await engine.start_story("u1", "Dave")
        session = engine._sessions["u1"]
        session.continue_after_ts = time.time() + 3_600  # gate expires in 1 h

        result = await engine.advance_story("u1", "1")

        assert "return" in result.lower() or "sealed" in result.lower()
        # Pacing state must not have advanced.
        assert session.scene_in_chapter == 0
        assert session.doom == 0

    @pytest.mark.asyncio
    async def test_continuation_allowed_after_gate_expires(self, engine: StoryEngine):
        """Sending a message after the gate expires clears the gate and advances normally."""
        engine._client = _make_mock_groq(_STORY_REPLY)
        await engine.start_story("u1", "Eve")
        session = engine._sessions["u1"]
        session.chapter = 2
        session.scene_in_chapter = 5
        session.doom = 0
        session.continue_after_ts = time.time() - 1  # gate already expired

        result = await engine.advance_story("u1", "1")

        assert session.continue_after_ts is None  # gate cleared
        assert session.scene_in_chapter == 6  # advanced
        assert result == _STORY_REPLY

    @pytest.mark.asyncio
    async def test_forced_finale_after_max_chapters(self, engine: StoryEngine):
        """Completing the last allowed chapter triggers a forced peril finale."""
        engine._client = _make_mock_groq(_PERIL_REPLY)
        await engine.start_story("u1", "Frank")
        session = engine._sessions["u1"]
        session.chapter = MAX_CHAPTERS
        session.scene_in_chapter = SCENES_PER_CHAPTER - 1
        session.doom = 0  # doom is low; finale is forced by chapter limit

        result = await engine.advance_story("u1", "1")

        assert session.finished is True
        assert result == _PERIL_REPLY

    @pytest.mark.asyncio
    async def test_doom_accumulates_per_scene(self, engine: StoryEngine):
        """doom increases by baseline_gain + risk_gain each scene."""
        engine._client = _make_mock_groq(_STORY_REPLY)
        await engine.start_story("u1", "Grace")
        session = engine._sessions["u1"]
        initial_doom = session.doom  # 0

        await engine.advance_story("u1", "wait")  # risk=0, baseline=chapter=1

        assert session.doom == initial_doom + 1 + 0  # 1

    @pytest.mark.asyncio
    async def test_high_risk_choice_adds_more_doom(self, engine: StoryEngine):
        """A high-risk choice adds 2 to doom (on top of baseline)."""
        engine._client = _make_mock_groq(_STORY_REPLY)
        await engine.start_story("u1", "Hank")
        session = engine._sessions["u1"]
        session.chapter = 1

        await engine.advance_story("u1", "attack")  # risk=2, baseline=1

        assert session.doom == 3
