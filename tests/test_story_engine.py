"""Tests for story_engine.StoryEngine and Session."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from story_engine import (
    _CHAPTER_CHOICE_SUFFIX,
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
        assert s.history[0]["role"] == "user"
        assert s.history[0]["content"] == "hello"
        assert "ts" in s.history[0]

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
    async def test_api_error_on_start_story_returns_error_message(self, engine: StoryEngine):
        engine._client.chat.completions.create = AsyncMock(side_effect=RuntimeError("timeout"))
        result = await engine.start_story("u1", "Frank")
        assert "API error" in result

    @pytest.mark.asyncio
    async def test_api_error_on_start_story_does_not_create_session(self, engine: StoryEngine):
        engine._client.chat.completions.create = AsyncMock(side_effect=RuntimeError("timeout"))
        await engine.start_story("u1", "Frank")
        assert not engine.has_session("u1")

    @pytest.mark.asyncio
    async def test_api_error_on_advance_story_returns_error_message(self, engine: StoryEngine):
        await engine.start_story("u1", "Frank")
        engine._client.chat.completions.create = AsyncMock(side_effect=RuntimeError("timeout"))
        result = await engine.advance_story("u1", "1")
        assert "API error" in result

    @pytest.mark.asyncio
    async def test_api_error_on_advance_story_does_not_poison_history(self, engine: StoryEngine):
        await engine.start_story("u1", "Frank")
        history_len_before = len(engine._sessions["u1"].history)
        engine._client.chat.completions.create = AsyncMock(side_effect=RuntimeError("timeout"))
        await engine.advance_story("u1", "1")
        # History must be unchanged: no error string and no extra messages
        session = engine._sessions["u1"]
        assert len(session.history) == history_len_before
        assert all("API error" not in msg["content"] for msg in session.history)

    @pytest.mark.asyncio
    async def test_start_story_after_api_error_starts_fresh(self, engine: StoryEngine):
        """After a failed start, a successful retry must create a clean session."""
        engine._client.chat.completions.create = AsyncMock(side_effect=RuntimeError("timeout"))
        await engine.start_story("u1", "Frank")
        assert not engine.has_session("u1")
        # Restore working mock and retry
        engine._client = _make_mock_groq()
        result = await engine.start_story("u1", "Frank")
        assert engine.has_session("u1")
        assert "API error" not in result

    @pytest.mark.asyncio
    async def test_llm_reply_choices_formatted_on_separate_lines(self, engine: StoryEngine):
        """_call_llm must post-process inline choices into separate lines."""
        raw = "Dark forest. 1) Run  2) Hide  3) Fight"
        engine._client = _make_mock_groq(raw)
        result = await engine.start_story("u1", "Grace")
        assert "\n1. " in result
        assert "\n2. " in result
        assert "\n3. " in result

    @pytest.mark.asyncio
    async def test_call_llm_strips_ts_from_api_messages(self, engine: StoryEngine):
        """Messages forwarded to the Groq API must contain only 'role' and
        'content' – extra history fields such as 'ts' must be stripped to
        avoid an invalid_request_error from the API endpoint."""
        captured: list[list[dict]] = []

        async def _capture_create(**kwargs):  # type: ignore[no-untyped-def]
            captured.append(kwargs["messages"])
            mock_choice = MagicMock()
            mock_choice.message.content = "Scene.\n1. Go\n2. Stay\n3. Run"
            mock_response = MagicMock()
            mock_response.choices = [mock_choice]
            return mock_response

        engine._client.chat.completions.create = _capture_create
        await engine.start_story("u1", "Hannah")
        await engine.advance_story("u1", "1")

        assert captured, "No API calls were captured"
        for call_messages in captured:
            for msg in call_messages:
                assert set(msg.keys()) <= {"role", "content"}, (
                    f"Unexpected keys in API message: {set(msg.keys()) - {'role', 'content'}}"
                )


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
# _log_story (upsert_story) call verification tests
# ---------------------------------------------------------------------------


class TestLogStoryCalled:
    """Verify that _log_story / upsert_story is invoked on every finish path."""

    @pytest.mark.asyncio
    async def test_restart_logs_previous_session(self, engine: StoryEngine):
        """start_story with an existing session must log the old session."""
        await engine.start_story("u1", "Alice")
        with patch("story_engine._log_story") as mock_log:
            await engine.start_story("u1", "Alice")
        mock_log.assert_called_once()
        call_arg = mock_log.call_args[0][0]
        assert call_arg["user_key"] == "u1"
        assert "started_at" in call_arg

    @pytest.mark.asyncio
    async def test_player_ended_logs_finished_story(self, engine: StoryEngine):
        """Choosing 3 (End) at a chapter boundary must log the finished session."""
        eng = _engine_at_chapter_boundary()
        await eng.advance_story("u1", "1")  # trigger boundary
        with patch("story_engine._log_story") as mock_log:
            await eng.advance_story("u1", "3")  # End
        mock_log.assert_called_once()
        call_arg = mock_log.call_args[0][0]
        assert call_arg["user_key"] == "u1"
        assert call_arg["finished"] is True

    @pytest.mark.asyncio
    async def test_doom_finale_logs_finished_story(self, engine: StoryEngine):
        """Hitting DOOM_MAX must log the session with finished=True."""
        session = Session("u1", "Hero", max_history=10)
        session.doom = DOOM_MAX - 1  # One more tick will trigger doom
        session.scene_in_chapter = 0
        session.chapter = 1
        engine._sessions["u1"] = session
        engine._client = _make_mock_groq(
            "Doom strikes!\n[END]\n1. Start over\n2. New adventure\n3. Quit"
        )
        with patch("story_engine._log_story") as mock_log:
            await engine.advance_story("u1", "attack")  # risky → doom triggers
        mock_log.assert_called_once()
        call_arg = mock_log.call_args[0][0]
        assert call_arg["user_key"] == "u1"
        assert call_arg["finished"] is True

    @pytest.mark.asyncio
    async def test_forced_finale_logs_finished_story(self, engine: StoryEngine):
        """Reaching MAX_CHAPTERS must log the session with finished=True."""
        session = Session("u1", "Hero", max_history=10)
        session.chapter = MAX_CHAPTERS
        session.scene_in_chapter = SCENES_PER_CHAPTER - 1
        session.doom = 0
        engine._sessions["u1"] = session
        engine._client = _make_mock_groq(
            "It is over.\n[END]\n1. Start over\n2. New adventure\n3. Quit"
        )
        with patch("story_engine._log_story") as mock_log:
            await engine.advance_story("u1", "1")  # triggers forced finale
        mock_log.assert_called_once()
        call_arg = mock_log.call_args[0][0]
        assert call_arg["user_key"] == "u1"
        assert call_arg["finished"] is True


# ---------------------------------------------------------------------------
# _ensure_choices unit tests
# ---------------------------------------------------------------------------


from story_engine import (  # noqa: E402 - import after fixtures/helpers
    _FINALE_FALLBACK_CHOICES,
    _STORY_FALLBACK_CHOICES,
    _ensure_choices,
)


class TestEnsureChoices:
    """_ensure_choices must guarantee a choices block is always present."""

    def test_reply_with_choices_unchanged(self):
        text = "You enter the cave.\n1. Go left\n2. Go right\n3. Wait"
        assert _ensure_choices(text) == text

    def test_reply_with_paren_choices_unchanged(self):
        text = "A wolf growls.\n1) Run\n2) Fight\n3) Hide"
        assert _ensure_choices(text) == text

    def test_reply_without_choices_gets_story_fallback(self):
        text = "Darkness surrounds you."
        result = _ensure_choices(text)
        assert result.startswith(text)
        assert "\n1." in result or "\n1)" in result

    def test_default_fallback_is_story_fallback(self):
        text = "No choices here."
        result = _ensure_choices(text)
        assert result == text + _STORY_FALLBACK_CHOICES

    def test_custom_fallback_used_when_provided(self):
        text = "Your story ends here."
        result = _ensure_choices(text, _FINALE_FALLBACK_CHOICES)
        assert result == text + _FINALE_FALLBACK_CHOICES

    def test_empty_string_gets_fallback(self):
        result = _ensure_choices("")
        assert _STORY_FALLBACK_CHOICES in result

    def test_reply_with_end_tag_and_choices_unchanged(self):
        text = "The hero falls.\n[END]\n1. Start over\n2. New adventure\n3. Quit"
        assert _ensure_choices(text) == text

    def test_reply_with_end_tag_but_no_choices_gets_fallback(self):
        text = "The hero falls.\n[END]"
        result = _ensure_choices(text, _FINALE_FALLBACK_CHOICES)
        assert result == text + _FINALE_FALLBACK_CHOICES

    def test_choices_only_at_start_of_string_unchanged(self):
        """Choices at the very start of the string (no preceding narrative) must not
        trigger the fallback – they are already valid choices."""
        text = "1. Go left\n2. Go right\n3. Wait"
        assert _ensure_choices(text) == text


# ---------------------------------------------------------------------------
# Story engine – options always present after LLM advances
# ---------------------------------------------------------------------------


class TestEnsureChoicesIntegration:
    """Verify the story engine always returns choices even when the LLM omits them."""

    @pytest.mark.asyncio
    async def test_start_story_without_llm_choices_gets_fallback(self):
        """start_story appends fallback choices when the LLM omits them."""
        with patch("story_engine.AsyncGroq") as mock_cls:
            mock_cls.return_value = _make_mock_groq()
            eng = StoryEngine(api_key="fake-key")
        eng._client = _make_mock_groq("You wake in a dark cave.")  # no choices
        result = await eng.start_story("u1", "Alice")
        assert "1." in result, "start_story must always return a choices block"

    @pytest.mark.asyncio
    async def test_advance_normal_scene_without_llm_choices_gets_fallback(self):
        """advance_story (normal scene) appends fallback choices when LLM omits them."""
        with patch("story_engine.AsyncGroq") as mock_cls:
            mock_cls.return_value = _make_mock_groq()
            eng = StoryEngine(api_key="fake-key")
        # Start with a good response to create the session
        eng._client = _make_mock_groq("Opening scene.\n1. A\n2. B\n3. C")
        await eng.start_story("u1", "Bob")
        # Advance with an LLM that omits choices
        eng._client = _make_mock_groq("The path ahead is dark.")
        result = await eng.advance_story("u1", "1")
        assert "1." in result, "advance_story must always return a choices block"

    @pytest.mark.asyncio
    async def test_advance_chapter_continue_without_llm_choices_gets_fallback(self):
        """advance_story (chapter continue) appends fallback choices when LLM omits them."""
        with patch("story_engine.AsyncGroq") as mock_cls:
            mock_cls.return_value = _make_mock_groq()
            eng = StoryEngine(api_key="fake-key")
        session = Session("u1", "Hero", max_history=10)
        session.awaiting_chapter_choice = True
        eng._sessions["u1"] = session
        # LLM returns a scene without choices
        eng._client = _make_mock_groq("A new chapter begins.")
        result = await eng.advance_story("u1", "1")  # Continue
        assert "1." in result, "chapter-continue must always return a choices block"

    @pytest.mark.asyncio
    async def test_doom_finale_without_llm_choices_gets_finale_fallback(self):
        """doom finale appends end-of-story fallback choices when LLM omits them."""
        with patch("story_engine.AsyncGroq") as mock_cls:
            mock_cls.return_value = _make_mock_groq()
            eng = StoryEngine(api_key="fake-key")
        session = Session("u1", "Hero", max_history=10)
        session.doom = DOOM_MAX - 1
        session.chapter = 1
        session.scene_in_chapter = 0
        eng._sessions["u1"] = session
        # LLM returns finale narrative without choices
        eng._client = _make_mock_groq("The hero meets their doom.")
        result = await eng.advance_story("u1", "attack")  # risky -> triggers doom finale
        assert "1." in result, "doom finale must always return a choices block"
        # Confirm it includes end-of-story semantics from the finale fallback
        assert "Start over" in result or "New adventure" in result or "[END]" in result

    @pytest.mark.asyncio
    async def test_forced_finale_without_llm_choices_gets_finale_fallback(self):
        """forced-peril finale appends end-of-story fallback when LLM omits choices."""
        with patch("story_engine.AsyncGroq") as mock_cls:
            mock_cls.return_value = _make_mock_groq()
            eng = StoryEngine(api_key="fake-key")
        session = Session("u1", "Hero", max_history=10)
        session.chapter = MAX_CHAPTERS
        session.scene_in_chapter = SCENES_PER_CHAPTER - 1
        session.doom = 0
        eng._sessions["u1"] = session
        # LLM returns finale narrative without choices
        eng._client = _make_mock_groq("The final chapter closes.")
        result = await eng.advance_story("u1", "1")  # triggers forced finale
        assert "1." in result, "forced finale must always return a choices block"
        assert "Start over" in result or "New adventure" in result or "[END]" in result

    @pytest.mark.asyncio
    async def test_advance_with_choices_unchanged(self):
        """When LLM correctly includes choices, the response must be returned unchanged."""
        with patch("story_engine.AsyncGroq") as mock_cls:
            mock_cls.return_value = _make_mock_groq()
            eng = StoryEngine(api_key="fake-key")
        expected = "You press on.\n1. Keep going\n2. Turn back\n3. Rest"
        eng._client = _make_mock_groq("Opening.\n1. A\n2. B\n3. C")
        await eng.start_story("u1", "Carol")
        eng._client = _make_mock_groq(expected)
        result = await eng.advance_story("u1", "1")
        assert result == expected
