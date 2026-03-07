"""Tests for cyoa_bot command-normalisation and dispatch logic."""

from __future__ import annotations

import sys
import types

import pytest


# ---------------------------------------------------------------------------
# Helpers – import cyoa_bot without starting asyncio or requiring hardware
# ---------------------------------------------------------------------------


def _import_bot():
    """Import cyoa_bot, stubbing out external dependencies."""
    # Stub meshcore so we don't need the real package at test time.
    if "meshcore" not in sys.modules:
        fake_mc = types.ModuleType("meshcore")

        class _FakeEventType:
            pass

        fake_mc.EventType = _FakeEventType
        fake_mc.MeshCore = object
        sys.modules["meshcore"] = fake_mc

    # Stub dotenv.
    if "dotenv" not in sys.modules:
        fake_dotenv = types.ModuleType("dotenv")
        fake_dotenv.load_dotenv = lambda *a, **k: None
        sys.modules["dotenv"] = fake_dotenv

    # Stub groq (needed transitively by story_engine).
    if "groq" not in sys.modules:
        fake_groq = types.ModuleType("groq")
        fake_groq.AsyncGroq = object
        sys.modules["groq"] = fake_groq

    sys.modules.pop("cyoa_bot", None)
    import cyoa_bot

    return cyoa_bot


# Module-level fixture shared by all test classes to avoid re-importing the
# module for every individual test method.
@pytest.fixture(scope="module")
def bot():
    """Return the cyoa_bot module with stubbed dependencies."""
    return _import_bot()


# ---------------------------------------------------------------------------
# Tests: _normalize_command
# ---------------------------------------------------------------------------


class TestNormalizeCommand:
    """_normalize_command should strip whitespace, lower-case, and remove
    a single leading / ! or \\ prefix."""

    def test_plain_start(self, bot):
        assert bot._normalize_command("start") == "start"

    def test_uppercase(self, bot):
        assert bot._normalize_command("START") == "start"

    def test_mixed_case(self, bot):
        assert bot._normalize_command("StArT") == "start"

    def test_leading_whitespace(self, bot):
        assert bot._normalize_command("  start  ") == "start"

    def test_slash_prefix(self, bot):
        assert bot._normalize_command("/start") == "start"

    def test_slash_prefix_uppercase(self, bot):
        assert bot._normalize_command("/START") == "start"

    def test_exclamation_prefix(self, bot):
        assert bot._normalize_command("!start") == "start"

    def test_backslash_prefix(self, bot):
        assert bot._normalize_command("\\start") == "start"

    def test_slash_with_whitespace(self, bot):
        assert bot._normalize_command("  /start  ") == "start"

    def test_slash_new(self, bot):
        assert bot._normalize_command("/new") == "new"

    def test_slash_begin(self, bot):
        assert bot._normalize_command("/begin") == "begin"

    def test_slash_restart(self, bot):
        assert bot._normalize_command("/restart") == "restart"

    def test_slash_help(self, bot):
        assert bot._normalize_command("/help") == "help"

    def test_choice_digit(self, bot):
        assert bot._normalize_command("1") == "1"

    def test_slash_choice_digit(self, bot):
        # /1 is unlikely but should still normalise to "1"
        assert bot._normalize_command("/1") == "1"

    def test_empty_string(self, bot):
        assert bot._normalize_command("") == ""

    def test_whitespace_only(self, bot):
        assert bot._normalize_command("   ") == ""

    def test_free_text_unchanged(self, bot):
        # Regular prose should pass through lower-cased.
        assert bot._normalize_command("I go left") == "i go left"

    def test_double_slash_only_strips_one(self, bot):
        # Only the first prefix character should be removed.
        assert bot._normalize_command("//start") == "/start"


# ---------------------------------------------------------------------------
# Tests: command-set membership (verifies the constants are correct)
# ---------------------------------------------------------------------------


class TestCommandSets:
    def test_start_cmds_contains_start(self, bot):
        assert "start" in bot._START_CMDS

    def test_start_cmds_contains_new(self, bot):
        assert "new" in bot._START_CMDS

    def test_start_cmds_contains_begin(self, bot):
        assert "begin" in bot._START_CMDS

    def test_reset_cmds_contains_restart(self, bot):
        assert "restart" in bot._RESET_CMDS

    def test_reset_cmds_contains_reset(self, bot):
        assert "reset" in bot._RESET_CMDS

    def test_help_cmds_contains_help(self, bot):
        assert "help" in bot._HELP_CMDS

    def test_choices_are_digits_1_to_3(self, bot):
        assert bot._CHOICES == {"1", "2", "3"}


# ---------------------------------------------------------------------------
# Tests: end-to-end normalization → command dispatch
# ---------------------------------------------------------------------------


class TestNormalizeToDispatch:
    """Ensure normalized commands land in the right command set."""

    @pytest.mark.parametrize("raw", ["start", "START", "/start", "!start", "\\start", "  /start  "])
    def test_start_variants_land_in_start_cmds(self, bot, raw):
        assert bot._normalize_command(raw) in bot._START_CMDS

    @pytest.mark.parametrize("raw", ["new", "/new", "!new"])
    def test_new_variants_land_in_start_cmds(self, bot, raw):
        assert bot._normalize_command(raw) in bot._START_CMDS

    @pytest.mark.parametrize("raw", ["restart", "/restart", "!restart"])
    def test_restart_variants_land_in_reset_cmds(self, bot, raw):
        assert bot._normalize_command(raw) in bot._RESET_CMDS

    @pytest.mark.parametrize("raw", ["help", "/help", "?"])
    def test_help_variants_land_in_help_cmds(self, bot, raw):
        assert bot._normalize_command(raw) in bot._HELP_CMDS

    @pytest.mark.parametrize("raw", ["1", "2", "3"])
    def test_digit_choices_land_in_choices(self, bot, raw):
        assert bot._normalize_command(raw) in bot._CHOICES
