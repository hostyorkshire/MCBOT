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


# ---------------------------------------------------------------------------
# Tests: _normalise_drain_result
# ---------------------------------------------------------------------------


class TestNormaliseDrainResult:
    """_normalise_drain_result should convert raw drain output to payload list."""

    def test_list_of_dicts_passthrough(self, bot):
        msgs = [
            {"pubkey_prefix": "aabb", "text": "hello"},
            {"pubkey_prefix": "ccdd", "text": "world"},
        ]
        result = bot._normalise_drain_result(msgs)
        assert len(result) == 2
        assert result[0]["pubkey_prefix"] == "aabb"
        assert result[0]["text"] == "hello"

    def test_dict_with_messages_key(self, bot):
        raw = {
            "messages": [
                {"pubkey_prefix": "aabb", "text": "hi"},
            ]
        }
        result = bot._normalise_drain_result(raw)
        assert len(result) == 1
        assert result[0]["pubkey_prefix"] == "aabb"
        assert result[0]["text"] == "hi"

    def test_single_dict_wrapped_in_list(self, bot):
        raw = {"pubkey_prefix": "aabb", "text": "single"}
        result = bot._normalise_drain_result(raw)
        assert len(result) == 1
        assert result[0]["pubkey_prefix"] == "aabb"
        assert result[0]["text"] == "single"

    def test_missing_keys_default_to_empty_string(self, bot):
        result = bot._normalise_drain_result([{}])
        assert result[0]["pubkey_prefix"] == ""
        assert result[0]["text"] == ""

    def test_extra_keys_preserved(self, bot):
        msgs = [{"pubkey_prefix": "aa", "text": "x", "extra": 42}]
        result = bot._normalise_drain_result(msgs)
        assert result[0]["extra"] == 42

    def test_non_dict_items_in_list_skipped(self, bot):
        result = bot._normalise_drain_result(["not_a_dict"])
        assert result == []

    def test_unexpected_type_returns_empty(self, bot):
        result = bot._normalise_drain_result(12345)
        assert result == []

    def test_empty_list_returns_empty(self, bot):
        assert bot._normalise_drain_result([]) == []


# ---------------------------------------------------------------------------
# Tests: _drain_inbox
# ---------------------------------------------------------------------------


class TestDrainInbox:
    """_drain_inbox should try drain candidates and return normalised payloads."""

    @pytest.mark.asyncio
    async def test_first_candidate_found_returns_payloads(self, bot):
        """If the first matching method returns a list of dicts, return them."""
        import types as _types
        from unittest.mock import AsyncMock

        mock = AsyncMock(return_value=[{"pubkey_prefix": "aa11", "text": "start"}])
        commands = _types.SimpleNamespace(get_messages=mock)
        result = await bot._drain_inbox(commands)
        assert len(result) == 1
        assert result[0]["pubkey_prefix"] == "aa11"
        assert result[0]["text"] == "start"

    @pytest.mark.asyncio
    async def test_dict_with_messages_key_handled(self, bot):
        """A drain method returning {'messages': [...]} is normalised correctly."""
        import types as _types
        from unittest.mock import AsyncMock

        mock = AsyncMock(
            return_value={"messages": [{"pubkey_prefix": "bb22", "text": "help"}]}
        )
        commands = _types.SimpleNamespace(read_messages=mock)
        result = await bot._drain_inbox(commands)
        assert len(result) == 1
        assert result[0]["pubkey_prefix"] == "bb22"

    @pytest.mark.asyncio
    async def test_first_candidate_type_error_falls_through_to_second(self, bot):
        """If first candidate raises TypeError, the next working one is used."""
        import types as _types
        from unittest.mock import AsyncMock

        bad_mock = AsyncMock(side_effect=TypeError("wrong signature"))
        good_mock = AsyncMock(return_value=[{"pubkey_prefix": "cc33", "text": "1"}])
        # get_messages raises TypeError; read_messages works.
        commands = _types.SimpleNamespace(
            get_messages=bad_mock, read_messages=good_mock
        )
        result = await bot._drain_inbox(commands)
        assert len(result) == 1
        assert result[0]["pubkey_prefix"] == "cc33"

    @pytest.mark.asyncio
    async def test_no_candidate_found_returns_empty(self, bot):
        """When no drain method exists on commands, return empty list."""
        commands = object()
        result = await bot._drain_inbox(commands)
        assert result == []

    @pytest.mark.asyncio
    async def test_unexpected_exception_skips_candidate(self, bot):
        """An unexpected exception from a candidate falls through to the next."""
        import types as _types
        from unittest.mock import AsyncMock

        bad_mock = AsyncMock(side_effect=ValueError("oops"))
        good_mock = AsyncMock(return_value=[{"pubkey_prefix": "dd44", "text": "2"}])
        commands = _types.SimpleNamespace(
            get_messages=bad_mock, read_messages=good_mock
        )
        result = await bot._drain_inbox(commands)
        assert len(result) == 1
        assert result[0]["pubkey_prefix"] == "dd44"

    @pytest.mark.asyncio
    async def test_all_candidates_fail_returns_empty(self, bot):
        """When every candidate raises, return empty list without raising."""
        import types as _types
        from unittest.mock import AsyncMock

        always_fails = AsyncMock(side_effect=TypeError("nope"))
        attrs = {name: always_fails for name in bot._DRAIN_CANDIDATES}
        commands = _types.SimpleNamespace(**attrs)
        result = await bot._drain_inbox(commands)
        assert result == []
