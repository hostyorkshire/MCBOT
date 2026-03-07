"""Tests for cyoa_bot command-normalisation and dispatch logic."""

from __future__ import annotations

import sys
import types
from unittest.mock import patch

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


# ---------------------------------------------------------------------------
# Tests: scan_serial_candidates
# ---------------------------------------------------------------------------


class TestScanSerialCandidates:
    """scan_serial_candidates should return sorted paths from glob results."""

    def test_returns_sorted_list(self, bot):
        with patch("glob.glob") as mock_glob:
            mock_glob.side_effect = lambda pattern: (
                ["/dev/ttyUSB1", "/dev/ttyUSB0"] if "ttyUSB" in pattern else []
            )
            result = bot.scan_serial_candidates()
        assert result == ["/dev/ttyUSB0", "/dev/ttyUSB1"]

    def test_includes_ttyacm_devices(self, bot):
        with patch("glob.glob") as mock_glob:
            mock_glob.side_effect = lambda pattern: (
                [] if "ttyUSB" in pattern else ["/dev/ttyACM0"]
            )
            result = bot.scan_serial_candidates()
        assert result == ["/dev/ttyACM0"]

    def test_combines_usb_and_acm(self, bot):
        with patch("glob.glob") as mock_glob:
            mock_glob.side_effect = lambda pattern: (
                ["/dev/ttyUSB0"] if "ttyUSB" in pattern else ["/dev/ttyACM0"]
            )
            result = bot.scan_serial_candidates()
        assert "/dev/ttyUSB0" in result
        assert "/dev/ttyACM0" in result

    def test_empty_when_no_devices(self, bot):
        with patch("glob.glob", return_value=[]):
            result = bot.scan_serial_candidates()
        assert result == []

    def test_result_is_sorted(self, bot):
        with patch("glob.glob") as mock_glob:
            mock_glob.side_effect = lambda pattern: (
                ["/dev/ttyUSB2", "/dev/ttyUSB0", "/dev/ttyUSB1"]
                if "ttyUSB" in pattern
                else []
            )
            result = bot.scan_serial_candidates()
        assert result == sorted(result)


# ---------------------------------------------------------------------------
# Tests: _connection_error_hint
# ---------------------------------------------------------------------------


class TestConnectionErrorHint:
    """_connection_error_hint should format a useful diagnostic message."""

    def test_includes_configured_port(self, bot):
        with patch.object(bot, "scan_serial_candidates", return_value=[]):
            msg = bot._connection_error_hint("/dev/ttyUSB0", 115200)
        assert "/dev/ttyUSB0" in msg

    def test_includes_baud_rate(self, bot):
        with patch.object(bot, "scan_serial_candidates", return_value=[]):
            msg = bot._connection_error_hint("/dev/ttyUSB0", 115200)
        assert "115200" in msg

    def test_includes_dialout_hint(self, bot):
        with patch.object(bot, "scan_serial_candidates", return_value=[]):
            msg = bot._connection_error_hint("/dev/ttyUSB0", 115200)
        assert "dialout" in msg

    def test_includes_ls_hint(self, bot):
        with patch.object(bot, "scan_serial_candidates", return_value=[]):
            msg = bot._connection_error_hint("/dev/ttyUSB0", 115200)
        assert "ls -l" in msg

    def test_no_devices_mentions_dmesg(self, bot):
        with patch.object(bot, "scan_serial_candidates", return_value=[]):
            msg = bot._connection_error_hint("/dev/ttyUSB0", 115200)
        assert "dmesg" in msg

    def test_discovered_ports_listed_when_present(self, bot):
        candidates = ["/dev/ttyACM0", "/dev/ttyUSB1"]
        with patch.object(bot, "scan_serial_candidates", return_value=candidates):
            msg = bot._connection_error_hint("/dev/ttyUSB0", 115200)
        assert "/dev/ttyACM0" in msg
        assert "/dev/ttyUSB1" in msg

    def test_alternate_port_hint_uses_port_flag(self, bot):
        with patch.object(
            bot, "scan_serial_candidates", return_value=["/dev/ttyACM0"]
        ):
            msg = bot._connection_error_hint("/dev/ttyUSB0", 115200)
        assert "--port" in msg
        assert "/dev/ttyACM0" in msg

    def test_no_devices_no_port_hint(self, bot):
        with patch.object(bot, "scan_serial_candidates", return_value=[]):
            msg = bot._connection_error_hint("/dev/ttyUSB0", 115200)
        # Should not suggest --port when no candidates found
        assert "--port" not in msg


# ---------------------------------------------------------------------------
# Tests: _parse_args – CLI overrides env vars
# ---------------------------------------------------------------------------


class TestParseArgs:
    """_parse_args should use CLI values over env-var defaults."""

    def test_default_port_comes_from_env(self, bot):
        # SERIAL_PORT default is whatever the module read from env at import
        args = bot._parse_args([])
        assert args.port == bot.SERIAL_PORT

    def test_default_baud_comes_from_env(self, bot):
        args = bot._parse_args([])
        assert args.baud == bot.BAUD_RATE

    def test_port_flag_overrides_default(self, bot):
        args = bot._parse_args(["--port", "/dev/ttyACM0"])
        assert args.port == "/dev/ttyACM0"

    def test_baud_flag_overrides_default(self, bot):
        args = bot._parse_args(["--baud", "9600"])
        assert args.baud == 9600

    def test_both_flags_together(self, bot):
        args = bot._parse_args(["--port", "/dev/ttyUSB1", "--baud", "57600"])
        assert args.port == "/dev/ttyUSB1"
        assert args.baud == 57600

    def test_baud_is_int(self, bot):
        args = bot._parse_args(["--baud", "38400"])
        assert isinstance(args.baud, int)
