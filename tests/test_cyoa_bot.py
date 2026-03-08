"""Tests for cyoa_bot command-normalisation and dispatch logic."""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

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
            NO_MORE_MSGS = "NO_MORE_MSGS"
            CONTACT_MSG_RECV = "CONTACT_MSG_RECV"
            CHANNEL_MSG_RECV = "CHANNEL_MSG_RECV"

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


# ---------------------------------------------------------------------------
# Tests: _drain_inbox – get_msg() loop (meshcore 2.2.x+)
# ---------------------------------------------------------------------------


class TestDrainInboxGetMsg:
    """_drain_inbox should prefer get_msg() over bulk-drain candidates."""

    @pytest.mark.asyncio
    async def test_get_msg_drain_returns_messages_before_no_more(self, bot):
        """get_msg() loop collects messages until NO_MORE_MSGS."""
        import types as _types
        from unittest.mock import AsyncMock

        EventType = bot.EventType

        msg_event = _types.SimpleNamespace(
            type=EventType.CONTACT_MSG_RECV,
            payload={"pubkey_prefix": "aa11", "text": "start"},
        )
        done_event = _types.SimpleNamespace(type=EventType.NO_MORE_MSGS, payload=None)

        get_msg = AsyncMock(side_effect=[msg_event, done_event])
        commands = _types.SimpleNamespace(get_msg=get_msg)
        result = await bot._drain_inbox(commands)
        assert len(result) == 1
        assert result[0]["pubkey_prefix"] == "aa11"
        assert result[0]["text"] == "start"

    @pytest.mark.asyncio
    async def test_get_msg_drain_multiple_messages(self, bot):
        """get_msg() loop collects multiple messages."""
        import types as _types
        from unittest.mock import AsyncMock

        EventType = bot.EventType

        events = [
            _types.SimpleNamespace(
                type=EventType.CONTACT_MSG_RECV,
                payload={"pubkey_prefix": "aa11", "text": "start"},
            ),
            _types.SimpleNamespace(
                type=EventType.CHANNEL_MSG_RECV,
                payload={"pubkey_prefix": "bb22", "text": "help"},
            ),
            _types.SimpleNamespace(type=EventType.NO_MORE_MSGS, payload=None),
        ]

        get_msg = AsyncMock(side_effect=events)
        commands = _types.SimpleNamespace(get_msg=get_msg)
        result = await bot._drain_inbox(commands)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_get_msg_drain_deduplicates_identical_payloads(self, bot):
        """Duplicate payloads within one drain session are suppressed."""
        import types as _types
        from unittest.mock import AsyncMock

        EventType = bot.EventType

        dup_payload = {"pubkey_prefix": "aa11", "text": "start"}
        events = [
            _types.SimpleNamespace(type=EventType.CONTACT_MSG_RECV, payload=dup_payload),
            _types.SimpleNamespace(type=EventType.CONTACT_MSG_RECV, payload=dup_payload),
            _types.SimpleNamespace(type=EventType.NO_MORE_MSGS, payload=None),
        ]

        get_msg = AsyncMock(side_effect=events)
        commands = _types.SimpleNamespace(get_msg=get_msg)
        result = await bot._drain_inbox(commands)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_msg_drain_no_messages_returns_empty(self, bot):
        """If get_msg() returns NO_MORE_MSGS immediately, return empty list."""
        import types as _types
        from unittest.mock import AsyncMock

        EventType = bot.EventType

        done_event = _types.SimpleNamespace(type=EventType.NO_MORE_MSGS, payload=None)
        get_msg = AsyncMock(return_value=done_event)
        commands = _types.SimpleNamespace(get_msg=get_msg)
        result = await bot._drain_inbox(commands)
        assert result == []

    @pytest.mark.asyncio
    async def test_get_msg_drain_error_stops_loop_gracefully(self, bot):
        """If get_msg() raises, the drain stops and returns any messages so far."""
        import types as _types
        from unittest.mock import AsyncMock

        EventType = bot.EventType

        msg_event = _types.SimpleNamespace(
            type=EventType.CONTACT_MSG_RECV,
            payload={"pubkey_prefix": "cc33", "text": "1"},
        )
        get_msg = AsyncMock(side_effect=[msg_event, RuntimeError("serial error")])
        commands = _types.SimpleNamespace(get_msg=get_msg)
        result = await bot._drain_inbox(commands)
        assert len(result) == 1
        assert result[0]["pubkey_prefix"] == "cc33"

    @pytest.mark.asyncio
    async def test_get_msg_preferred_over_bulk_candidates(self, bot):
        """When get_msg exists, bulk-drain candidates are not called."""
        import types as _types
        from unittest.mock import AsyncMock

        EventType = bot.EventType

        done_event = _types.SimpleNamespace(type=EventType.NO_MORE_MSGS, payload=None)
        get_msg = AsyncMock(return_value=done_event)
        bulk_mock = AsyncMock(return_value=[{"pubkey_prefix": "dd44", "text": "x"}])
        commands = _types.SimpleNamespace(
            get_msg=get_msg,
            get_messages=bulk_mock,
        )
        await bot._drain_inbox(commands)
        bulk_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: --check-env flag
# ---------------------------------------------------------------------------


class TestCheckEnv:
    """_check_env should print env-var status and exit."""

    def test_check_env_exits_zero_when_all_set(self, bot, capsys):
        """_check_env exits 0 when GROQ_API_KEY is set."""
        with patch.object(bot, "GROQ_API_KEY", "fake_key_for_testing"):
            with pytest.raises(SystemExit) as exc_info:
                bot._check_env()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "GROQ_API_KEY" in out
        assert "fake_key_for_testing" not in out  # secret must not appear in output

    def test_check_env_exits_nonzero_when_key_missing(self, bot, capsys):
        """_check_env exits non-zero when GROQ_API_KEY is empty."""
        with patch.object(bot, "GROQ_API_KEY", ""):
            with pytest.raises(SystemExit) as exc_info:
                bot._check_env()
        assert exc_info.value.code != 0

    def test_check_env_does_not_print_secret_value(self, bot, capsys):
        """_check_env must never print the actual API key value."""
        secret = "gsk_supersecret1234567890abcdef"
        with patch.object(bot, "GROQ_API_KEY", secret):
            with pytest.raises(SystemExit):
                bot._check_env()
        out = capsys.readouterr().out
        assert secret not in out

    def test_check_env_shows_model_value(self, bot, capsys):
        """_check_env prints the current GROQ_MODEL value (not secret)."""
        with patch.object(bot, "GROQ_API_KEY", "fake_key"):
            with patch.object(bot, "GROQ_MODEL", "llama-3.1-8b-instant"):
                with pytest.raises(SystemExit):
                    bot._check_env()
        out = capsys.readouterr().out
        assert "llama-3.1-8b-instant" in out

    def test_parse_args_check_env_flag(self, bot):
        """--check-env flag is parsed as check_env=True."""
        args = bot._parse_args(["--check-env"])
        assert args.check_env is True

    def test_parse_args_no_check_env_flag(self, bot):
        """check_env is False when --check-env is not passed."""
        args = bot._parse_args([])
        assert args.check_env is False


# ---------------------------------------------------------------------------
# Tests: HELP_TEXT constant
# ---------------------------------------------------------------------------


EXPECTED_HELP_TEXT = (
    "Commands:\n"
    "- help / ? \u2014 show this message\n"
    "- genres \u2014 list genres\n"
    "- start / new / begin <genre name or number>\n"
    "- restart / reset \u2014 reset\n"
    "\n"
    "(If prompted: 1/2/3 or text. 180s confirm.)"
)


class TestHelpText:
    """HELP_TEXT must match the required string exactly and be ≤ 180 chars."""

    def test_help_text_exact_match(self, bot):
        assert bot.HELP_TEXT == EXPECTED_HELP_TEXT

    def test_help_text_under_180_chars(self, bot):
        assert len(bot.HELP_TEXT) <= 180


# ---------------------------------------------------------------------------
# Tests: BotHandler – helper utilities
# ---------------------------------------------------------------------------


def _make_handler(bot, confirm_timeout: float = 0.05):
    """Create a BotHandler with fully mocked mc and story_engine."""
    mc = MagicMock()
    mc.commands.send_msg = AsyncMock()

    story_engine = MagicMock()
    story_engine.has_session = MagicMock(return_value=False)
    story_engine.clear_session = MagicMock()
    story_engine.start_story = AsyncMock(return_value="Once upon a time…")
    story_engine.advance_story = AsyncMock(return_value="Story continues…")

    handler = bot.BotHandler(
        mc=mc,
        story_engine=story_engine,
        max_chunk_size=1000,   # large enough to avoid chunking in tests
        chunk_delay=0.0,
        confirm_timeout=confirm_timeout,
    )
    return handler, mc, story_engine


def _sent_texts(mc) -> list[str]:
    """Return a flat list of message texts sent via mc.commands.send_msg."""
    return [call.args[1] for call in mc.commands.send_msg.call_args_list]


# ---------------------------------------------------------------------------
# Tests: BotHandler – help command
# ---------------------------------------------------------------------------


class TestBotHandlerHelp:
    """Help command should send HELP_TEXT and leave no pending state."""

    @pytest.mark.asyncio
    async def test_help_sends_help_text(self, bot):
        handler, mc, _ = _make_handler(bot)
        await handler.handle("aa11", "help", "Alice")
        assert EXPECTED_HELP_TEXT in _sent_texts(mc)

    @pytest.mark.asyncio
    async def test_question_mark_sends_help_text(self, bot):
        handler, mc, _ = _make_handler(bot)
        await handler.handle("aa11", "?", "Alice")
        assert EXPECTED_HELP_TEXT in _sent_texts(mc)

    @pytest.mark.asyncio
    async def test_slash_help_sends_help_text(self, bot):
        handler, mc, _ = _make_handler(bot)
        await handler.handle("aa11", "/help", "Alice")
        assert EXPECTED_HELP_TEXT in _sent_texts(mc)

    @pytest.mark.asyncio
    async def test_help_does_not_set_pending_state(self, bot):
        handler, mc, _ = _make_handler(bot)
        await handler.handle("aa11", "help", "Alice")
        assert not handler.is_pending_confirm("aa11")


# ---------------------------------------------------------------------------
# Tests: BotHandler – start command / onboarding flow
# ---------------------------------------------------------------------------


class TestBotHandlerStart:
    """Start command must send 3 onboarding messages and set pending state."""

    @pytest.mark.asyncio
    async def test_start_sends_three_messages(self, bot):
        handler, mc, _ = _make_handler(bot)
        await handler.handle("bb22", "start", "Bob")
        texts = _sent_texts(mc)
        assert len(texts) == 3

    @pytest.mark.asyncio
    async def test_start_first_message(self, bot):
        handler, mc, _ = _make_handler(bot)
        await handler.handle("bb22", "start", "Bob")
        assert _sent_texts(mc)[0] == bot.ONBOARD_MSG_1

    @pytest.mark.asyncio
    async def test_start_second_message_is_commands_url(self, bot):
        handler, mc, _ = _make_handler(bot)
        await handler.handle("bb22", "start", "Bob")
        assert _sent_texts(mc)[1] == bot.ONBOARD_MSG_2

    @pytest.mark.asyncio
    async def test_start_third_message(self, bot):
        handler, mc, _ = _make_handler(bot)
        await handler.handle("bb22", "start", "Bob")
        assert _sent_texts(mc)[2] == bot.ONBOARD_MSG_3

    @pytest.mark.asyncio
    async def test_start_sets_pending_confirm(self, bot):
        handler, mc, _ = _make_handler(bot)
        await handler.handle("bb22", "start", "Bob")
        assert handler.is_pending_confirm("bb22")

    @pytest.mark.asyncio
    async def test_new_command_triggers_onboarding(self, bot):
        handler, mc, _ = _make_handler(bot)
        await handler.handle("bb22", "new", "Bob")
        assert len(_sent_texts(mc)) == 3
        assert handler.is_pending_confirm("bb22")

    @pytest.mark.asyncio
    async def test_begin_command_triggers_onboarding(self, bot):
        handler, mc, _ = _make_handler(bot)
        await handler.handle("bb22", "begin", "Bob")
        assert len(_sent_texts(mc)) == 3
        assert handler.is_pending_confirm("bb22")

    @pytest.mark.asyncio
    async def test_duplicate_start_cancels_previous_pending(self, bot):
        """A second start command replaces any existing pending confirmation."""
        handler, mc, _ = _make_handler(bot)
        await handler.handle("bb22", "start", "Bob")
        first_task = handler._pending_confirm.get("bb22")
        await handler.handle("bb22", "start", "Bob")
        # Give the event loop a chance to process the cancellation.
        await asyncio.sleep(0)
        # The original task should be cancelled or done.
        assert first_task is not None
        assert first_task.cancelled() or first_task.done()


# ---------------------------------------------------------------------------
# Tests: BotHandler – yes/no confirmation handling
# ---------------------------------------------------------------------------


class TestBotHandlerConfirmYes:
    """Yes-ish replies should start the story and clear pending state."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "yes_word",
        # "start" is in _YES_CMDS but has command priority: it re-triggers
        # onboarding rather than confirming, so it is not tested here.
        ["yes", "y", "ok", "okay", "sure", "yeah", "yep", "please", "go"],
    )
    async def test_yes_ish_starts_story(self, bot, yes_word):
        handler, mc, story_engine = _make_handler(bot)
        await handler.handle("cc33", "start", "Carol")
        mc.commands.send_msg.reset_mock()
        await handler.handle("cc33", yes_word, "Carol")
        story_engine.start_story.assert_called_once_with("cc33", "Carol", genre="wasteland")

    @pytest.mark.asyncio
    async def test_yes_sends_story_text(self, bot):
        handler, mc, story_engine = _make_handler(bot)
        await handler.handle("cc33", "start", "Carol")
        mc.commands.send_msg.reset_mock()
        await handler.handle("cc33", "yes", "Carol")
        assert "Once upon a time" in _sent_texts(mc)[0]

    @pytest.mark.asyncio
    async def test_yes_clears_pending_state(self, bot):
        handler, mc, _ = _make_handler(bot)
        await handler.handle("cc33", "start", "Carol")
        await handler.handle("cc33", "yes", "Carol")
        assert not handler.is_pending_confirm("cc33")

    @pytest.mark.asyncio
    async def test_yes_case_insensitive(self, bot):
        handler, mc, story_engine = _make_handler(bot)
        await handler.handle("cc33", "start", "Carol")
        await handler.handle("cc33", "YES", "Carol")
        story_engine.start_story.assert_called_once()


class TestBotHandlerConfirmNo:
    """No-ish replies should send NO_MSG and clear pending state."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "no_word",
        ["no", "n", "nope", "not now", "later", "cancel"],
    )
    async def test_no_ish_sends_no_msg(self, bot, no_word):
        handler, mc, story_engine = _make_handler(bot)
        await handler.handle("dd44", "start", "Dave")
        mc.commands.send_msg.reset_mock()
        await handler.handle("dd44", no_word, "Dave")
        assert bot.NO_MSG in _sent_texts(mc)
        story_engine.start_story.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_clears_pending_state(self, bot):
        handler, mc, _ = _make_handler(bot)
        await handler.handle("dd44", "start", "Dave")
        await handler.handle("dd44", "no", "Dave")
        assert not handler.is_pending_confirm("dd44")

    @pytest.mark.asyncio
    async def test_unknown_reply_treated_as_no(self, bot):
        """An unrecognised reply while pending should behave like no."""
        handler, mc, story_engine = _make_handler(bot)
        await handler.handle("dd44", "start", "Dave")
        mc.commands.send_msg.reset_mock()
        await handler.handle("dd44", "blahblah", "Dave")
        assert bot.NO_MSG in _sent_texts(mc)
        story_engine.start_story.assert_not_called()
        assert not handler.is_pending_confirm("dd44")


# ---------------------------------------------------------------------------
# Tests: BotHandler – timeout
# ---------------------------------------------------------------------------


class TestBotHandlerTimeout:
    """After confirm_timeout seconds with no reply the bot sends TIMEOUT_MSG."""

    @pytest.mark.asyncio
    async def test_timeout_sends_timeout_message(self, bot):
        # Use a very short timeout so the test completes quickly.
        handler, mc, _ = _make_handler(bot, confirm_timeout=0.05)
        await handler.handle("ee55", "start", "Eve")
        # Wait longer than the timeout.
        await asyncio.sleep(0.2)
        assert bot.TIMEOUT_MSG in _sent_texts(mc)

    @pytest.mark.asyncio
    async def test_timeout_clears_pending_state(self, bot):
        handler, mc, _ = _make_handler(bot, confirm_timeout=0.05)
        await handler.handle("ee55", "start", "Eve")
        await asyncio.sleep(0.2)
        assert not handler.is_pending_confirm("ee55")

    @pytest.mark.asyncio
    async def test_no_timeout_if_replied_in_time(self, bot):
        """If the user replies before the timeout, TIMEOUT_MSG must not be sent."""
        handler, mc, _ = _make_handler(bot, confirm_timeout=0.2)
        await handler.handle("ee55", "start", "Eve")
        # Reply quickly (before timeout fires).
        await handler.handle("ee55", "yes", "Eve")
        await asyncio.sleep(0.3)  # let the (now-cancelled) task complete
        assert bot.TIMEOUT_MSG not in _sent_texts(mc)


# ---------------------------------------------------------------------------
# Tests: BotHandler – restart/reset
# ---------------------------------------------------------------------------


class TestBotHandlerReset:
    """restart/reset clears the session and triggers the onboarding flow."""

    @pytest.mark.asyncio
    async def test_restart_clears_session(self, bot):
        handler, mc, story_engine = _make_handler(bot)
        await handler.handle("ff66", "restart", "Frank")
        story_engine.clear_session.assert_called_once_with("ff66")

    @pytest.mark.asyncio
    async def test_restart_triggers_onboarding(self, bot):
        handler, mc, story_engine = _make_handler(bot)
        await handler.handle("ff66", "restart", "Frank")
        texts = _sent_texts(mc)
        assert len(texts) == 3
        assert texts[0] == bot.ONBOARD_MSG_1
        assert texts[1] == bot.ONBOARD_MSG_2
        assert texts[2] == bot.ONBOARD_MSG_3

    @pytest.mark.asyncio
    async def test_restart_sets_pending_confirm(self, bot):
        handler, mc, _ = _make_handler(bot)
        await handler.handle("ff66", "restart", "Frank")
        assert handler.is_pending_confirm("ff66")

    @pytest.mark.asyncio
    async def test_reset_clears_session(self, bot):
        handler, mc, story_engine = _make_handler(bot)
        await handler.handle("ff66", "reset", "Frank")
        story_engine.clear_session.assert_called_once_with("ff66")

    @pytest.mark.asyncio
    async def test_reset_triggers_onboarding(self, bot):
        handler, mc, story_engine = _make_handler(bot)
        await handler.handle("ff66", "reset", "Frank")
        texts = _sent_texts(mc)
        assert len(texts) == 3
        assert texts[0] == bot.ONBOARD_MSG_1

    @pytest.mark.asyncio
    async def test_restart_cancels_existing_pending(self, bot):
        """restart during a pending confirmation cancels the old task."""
        handler, mc, _ = _make_handler(bot)
        await handler.handle("ff66", "start", "Frank")
        old_task = handler._pending_confirm.get("ff66")
        await handler.handle("ff66", "restart", "Frank")
        # Give the event loop a chance to process the cancellation.
        await asyncio.sleep(0)
        assert old_task is not None
        assert old_task.cancelled() or old_task.done()
        assert handler.is_pending_confirm("ff66")


# ---------------------------------------------------------------------------
# Tests: _parse_command
# ---------------------------------------------------------------------------


class TestParseCommand:
    """_parse_command should return (command, arg) pairs."""

    def test_plain_command_no_arg(self, bot):
        assert bot._parse_command("start") == ("start", "")

    def test_command_with_arg(self, bot):
        assert bot._parse_command("start horror") == ("start", "horror")

    def test_slash_command_with_arg(self, bot):
        assert bot._parse_command("/start horror") == ("start", "horror")

    def test_exclamation_command_with_arg(self, bot):
        assert bot._parse_command("!start horror") == ("start", "horror")

    def test_backslash_command_with_arg(self, bot):
        assert bot._parse_command("\\start horror") == ("start", "horror")

    def test_command_with_numeric_arg(self, bot):
        assert bot._parse_command("start 2") == ("start", "2")

    def test_uppercase_command_with_arg(self, bot):
        assert bot._parse_command("/START Horror") == ("start", "horror")

    def test_whitespace_trimmed(self, bot):
        assert bot._parse_command("  /start  horror  ") == ("start", "horror")

    def test_empty_string(self, bot):
        assert bot._parse_command("") == ("", "")

    def test_plain_command_only(self, bot):
        assert bot._parse_command("genres") == ("genres", "")

    def test_slash_genres(self, bot):
        assert bot._parse_command("/genres") == ("genres", "")


# ---------------------------------------------------------------------------
# Tests: _resolve_genre
# ---------------------------------------------------------------------------


class TestResolveGenre:
    """_resolve_genre should map genre IDs and numbers to genre IDs."""

    def test_known_genre_id_returned(self, bot):
        assert bot._resolve_genre("horror") == "horror"

    def test_unknown_genre_returns_none(self, bot):
        assert bot._resolve_genre("scifi") is None

    def test_empty_string_returns_none(self, bot):
        assert bot._resolve_genre("") is None

    def test_number_1_maps_to_wasteland(self, bot):
        assert bot._resolve_genre("1") == "wasteland"

    def test_number_2_maps_to_cozy(self, bot):
        assert bot._resolve_genre("2") == "cozy"

    def test_number_3_maps_to_horror(self, bot):
        assert bot._resolve_genre("3") == "horror"

    def test_number_4_maps_to_mil(self, bot):
        assert bot._resolve_genre("4") == "mil"

    def test_number_5_maps_to_comedy(self, bot):
        assert bot._resolve_genre("5") == "comedy"

    def test_out_of_range_number_returns_none(self, bot):
        assert bot._resolve_genre("99") is None

    def test_zero_returns_none(self, bot):
        assert bot._resolve_genre("0") is None

    def test_negative_returns_none(self, bot):
        assert bot._resolve_genre("-1") is None

    def test_genre_case_insensitive(self, bot):
        assert bot._resolve_genre("HORROR") == "horror"
        assert bot._resolve_genre("Cozy") == "cozy"

    def test_all_genre_ids_resolve(self, bot):
        for gid in bot.GENRES:
            assert bot._resolve_genre(gid) == gid


# ---------------------------------------------------------------------------
# Tests: BotHandler – genres command
# ---------------------------------------------------------------------------


class TestBotHandlerGenres:
    """genres command should send the compact genre list."""

    @pytest.mark.asyncio
    async def test_genres_sends_genres_text(self, bot):
        handler, mc, _ = _make_handler(bot)
        await handler.handle("aa11", "genres", "Alice")
        assert bot.GENRES_TEXT in _sent_texts(mc)

    @pytest.mark.asyncio
    async def test_slash_genres_sends_genres_text(self, bot):
        handler, mc, _ = _make_handler(bot)
        await handler.handle("aa11", "/genres", "Alice")
        assert bot.GENRES_TEXT in _sent_texts(mc)

    @pytest.mark.asyncio
    async def test_exclamation_genres_sends_genres_text(self, bot):
        handler, mc, _ = _make_handler(bot)
        await handler.handle("aa11", "!genres", "Alice")
        assert bot.GENRES_TEXT in _sent_texts(mc)

    @pytest.mark.asyncio
    async def test_genres_does_not_start_story(self, bot):
        handler, mc, story_engine = _make_handler(bot)
        await handler.handle("aa11", "genres", "Alice")
        story_engine.start_story.assert_not_called()

    @pytest.mark.asyncio
    async def test_genres_text_contains_all_genre_ids(self, bot):
        for gid in bot.GENRES:
            assert gid in bot.GENRES_TEXT

    def test_genres_cmds_constant(self, bot):
        assert "genres" in bot._GENRES_CMDS


# ---------------------------------------------------------------------------
# Tests: BotHandler – genre selection via start <genre|#>
# ---------------------------------------------------------------------------


class TestBotHandlerGenreStart:
    """start <genre|#> should onboard with the specified genre."""

    @pytest.mark.asyncio
    async def test_start_horror_triggers_onboarding(self, bot):
        handler, mc, _ = _make_handler(bot)
        await handler.handle("gg77", "start horror", "Grace")
        assert len(_sent_texts(mc)) == 3
        assert handler.is_pending_confirm("gg77")

    @pytest.mark.asyncio
    async def test_start_horror_then_yes_calls_engine_with_genre(self, bot):
        handler, mc, story_engine = _make_handler(bot)
        await handler.handle("gg77", "start horror", "Grace")
        mc.commands.send_msg.reset_mock()
        await handler.handle("gg77", "yes", "Grace")
        story_engine.start_story.assert_called_once_with("gg77", "Grace", genre="horror")

    @pytest.mark.asyncio
    async def test_start_numeric_2_maps_to_cozy(self, bot):
        handler, mc, story_engine = _make_handler(bot)
        await handler.handle("gg77", "start 2", "Grace")
        mc.commands.send_msg.reset_mock()
        await handler.handle("gg77", "yes", "Grace")
        story_engine.start_story.assert_called_once_with("gg77", "Grace", genre="cozy")

    @pytest.mark.asyncio
    async def test_start_numeric_3_maps_to_horror(self, bot):
        handler, mc, story_engine = _make_handler(bot)
        await handler.handle("gg77", "start 3", "Grace")
        mc.commands.send_msg.reset_mock()
        await handler.handle("gg77", "yes", "Grace")
        story_engine.start_story.assert_called_once_with("gg77", "Grace", genre="horror")

    @pytest.mark.asyncio
    async def test_start_no_arg_defaults_to_wasteland(self, bot):
        handler, mc, story_engine = _make_handler(bot)
        await handler.handle("gg77", "start", "Grace")
        mc.commands.send_msg.reset_mock()
        await handler.handle("gg77", "yes", "Grace")
        story_engine.start_story.assert_called_once_with("gg77", "Grace", genre="wasteland")

    @pytest.mark.asyncio
    async def test_unknown_genre_sends_error_not_onboarding(self, bot):
        handler, mc, _ = _make_handler(bot)
        await handler.handle("gg77", "start badgenre", "Grace")
        texts = _sent_texts(mc)
        assert len(texts) == 1
        assert "Unknown genre" in texts[0]
        assert not handler.is_pending_confirm("gg77")

    @pytest.mark.asyncio
    async def test_unknown_genre_hints_genres_command(self, bot):
        handler, mc, _ = _make_handler(bot)
        await handler.handle("gg77", "start badgenre", "Grace")
        texts = _sent_texts(mc)
        assert "genres" in texts[0].lower()

    @pytest.mark.asyncio
    async def test_new_horror_triggers_horror_genre(self, bot):
        handler, mc, story_engine = _make_handler(bot)
        await handler.handle("gg77", "new horror", "Grace")
        mc.commands.send_msg.reset_mock()
        await handler.handle("gg77", "yes", "Grace")
        story_engine.start_story.assert_called_once_with("gg77", "Grace", genre="horror")

    @pytest.mark.asyncio
    async def test_begin_comedy_triggers_comedy_genre(self, bot):
        handler, mc, story_engine = _make_handler(bot)
        await handler.handle("gg77", "begin comedy", "Grace")
        mc.commands.send_msg.reset_mock()
        await handler.handle("gg77", "yes", "Grace")
        story_engine.start_story.assert_called_once_with("gg77", "Grace", genre="comedy")

    @pytest.mark.asyncio
    async def test_slash_start_horror(self, bot):
        handler, mc, story_engine = _make_handler(bot)
        await handler.handle("gg77", "/start horror", "Grace")
        mc.commands.send_msg.reset_mock()
        await handler.handle("gg77", "yes", "Grace")
        story_engine.start_story.assert_called_once_with("gg77", "Grace", genre="horror")

    @pytest.mark.asyncio
    async def test_reset_with_genre_uses_that_genre(self, bot):
        """restart <genre> should onboard and then start story in that genre."""
        handler, mc, story_engine = _make_handler(bot)
        await handler.handle("gg77", "restart mil", "Grace")
        mc.commands.send_msg.reset_mock()
        await handler.handle("gg77", "yes", "Grace")
        story_engine.start_story.assert_called_once_with("gg77", "Grace", genre="mil")

    @pytest.mark.asyncio
    async def test_reset_no_genre_defaults_to_wasteland(self, bot):
        """restart with no genre should default to wasteland."""
        handler, mc, story_engine = _make_handler(bot)
        await handler.handle("gg77", "restart", "Grace")
        mc.commands.send_msg.reset_mock()
        await handler.handle("gg77", "yes", "Grace")
        story_engine.start_story.assert_called_once_with("gg77", "Grace", genre="wasteland")


# ---------------------------------------------------------------------------
# Tests: _split_story_choices
# ---------------------------------------------------------------------------


class TestSplitStoryChoices:
    """_split_story_choices must correctly separate narrative from choices."""

    def test_splits_narrative_from_dot_choices(self, bot):
        narrative, choices = bot._split_story_choices(
            "You stand at a crossroads.\n1. Go left\n2. Go right\n3. Wait"
        )
        assert narrative == "You stand at a crossroads."
        assert choices == "1. Go left\n2. Go right\n3. Wait"

    def test_splits_narrative_from_paren_choices(self, bot):
        narrative, choices = bot._split_story_choices(
            "A wolf growls.\n1) Run\n2) Fight\n3) Hide"
        )
        assert narrative == "A wolf growls."
        assert choices == "1) Run\n2) Fight\n3) Hide"

    def test_no_choices_returns_empty_narrative(self, bot):
        narrative, choices = bot._split_story_choices("Just a plain message.")
        assert narrative == ""
        assert choices == "Just a plain message."

    def test_choices_only_returns_empty_narrative(self, bot):
        narrative, choices = bot._split_story_choices("1. A\n2. B\n3. C")
        assert narrative == ""
        assert choices == "1. A\n2. B\n3. C"

    def test_end_tag_becomes_narrative(self, bot):
        narrative, choices = bot._split_story_choices(
            "[END]\n1. Restart\n2. New adventure\n3. Quit"
        )
        assert narrative == "[END]"
        assert choices == "1. Restart\n2. New adventure\n3. Quit"

    def test_multiline_narrative(self, bot):
        text = (
            "You enter the cave.\nDarkness surrounds you.\n"
            "1. Light torch\n2. Feel ahead\n3. Retreat"
        )
        narrative, choices = bot._split_story_choices(text)
        assert narrative == "You enter the cave.\nDarkness surrounds you."
        assert choices == "1. Light torch\n2. Feel ahead\n3. Retreat"

    def test_empty_string_returns_empty_pair(self, bot):
        narrative, choices = bot._split_story_choices("")
        assert narrative == ""
        assert choices == ""

    def test_gate_message_no_choices_unchanged(self, bot):
        msg = "The path is sealed. Return in 2h 30m to continue."
        narrative, choices = bot._split_story_choices(msg)
        assert narrative == ""
        assert choices == msg


# ---------------------------------------------------------------------------
# Tests: BotHandler – story and choices sent as separate messages
# ---------------------------------------------------------------------------


class TestBotHandlerStoryChoicesSplit:
    """After start/advance, narrative and choices must arrive in separate messages."""

    @pytest.mark.asyncio
    async def test_yes_sends_narrative_then_choices(self, bot):
        handler, mc, story_engine = _make_handler(bot)
        story_engine.start_story = AsyncMock(
            return_value="You wake in a cave.\n1. Explore\n2. Wait\n3. Shout"
        )
        await handler.handle("hh88", "start", "Harriet")
        mc.commands.send_msg.reset_mock()
        await handler.handle("hh88", "yes", "Harriet")
        texts = _sent_texts(mc)
        assert texts[0] == "You wake in a cave."
        assert texts[1] == "1. Explore\n2. Wait\n3. Shout"

    @pytest.mark.asyncio
    async def test_yes_always_two_messages_when_choices_present(self, bot):
        handler, mc, story_engine = _make_handler(bot)
        story_engine.start_story = AsyncMock(
            return_value="Short.\n1. A\n2. B\n3. C"
        )
        await handler.handle("hh88", "start", "Harriet")
        mc.commands.send_msg.reset_mock()
        await handler.handle("hh88", "yes", "Harriet")
        # Narrative + choices = exactly 2 sends (plus possible intra-chunk sends
        # but chunk_size=1000 in tests so no extra chunking).
        assert len(_sent_texts(mc)) == 2

    @pytest.mark.asyncio
    async def test_choice_advance_sends_narrative_then_choices(self, bot):
        handler, mc, story_engine = _make_handler(bot)
        story_engine.advance_story = AsyncMock(
            return_value="You step forward.\n1. Keep going\n2. Turn back\n3. Hide"
        )
        story_engine.has_session = MagicMock(return_value=True)
        await handler.handle("hh88", "1", "Harriet")
        texts = _sent_texts(mc)
        assert texts[0] == "You step forward."
        assert texts[1] == "1. Keep going\n2. Turn back\n3. Hide"

    @pytest.mark.asyncio
    async def test_non_story_response_single_message(self, bot):
        """A response without choices (e.g. cooldown gate) is a single message."""
        handler, mc, story_engine = _make_handler(bot)
        story_engine.advance_story = AsyncMock(
            return_value="The path is sealed. Return in 2h."
        )
        story_engine.has_session = MagicMock(return_value=True)
        await handler.handle("hh88", "1", "Harriet")
        texts = _sent_texts(mc)
        assert len(texts) == 1
        assert "The path is sealed" in texts[0]

    @pytest.mark.asyncio
    async def test_story_only_no_choices_single_message(self, bot):
        """A response that is pure narrative (no choices) sends one message."""
        handler, mc, story_engine = _make_handler(bot)
        story_engine.start_story = AsyncMock(return_value="Once upon a time…")
        await handler.handle("hh88", "start", "Harriet")
        mc.commands.send_msg.reset_mock()
        await handler.handle("hh88", "yes", "Harriet")
        texts = _sent_texts(mc)
        assert len(texts) == 1
        assert "Once upon a time" in texts[0]


# ---------------------------------------------------------------------------
# Tests: BotHandler – duplicate / concurrent message guard
# ---------------------------------------------------------------------------


class TestBotHandlerProcessingGuard:
    """A message that arrives while the same user's previous message is still
    being handled must be silently dropped."""

    @pytest.mark.asyncio
    async def test_message_dropped_while_user_in_processing(self, bot):
        handler, mc, story_engine = _make_handler(bot)
        story_engine.has_session = MagicMock(return_value=True)

        # Simulate another coroutine holding the processing slot.
        handler._processing.add("ii99")

        await handler.handle("ii99", "1", "Ivan")

        story_engine.advance_story.assert_not_called()
        mc.commands.send_msg.assert_not_called()

        handler._processing.discard("ii99")

    @pytest.mark.asyncio
    async def test_processing_slot_released_after_handle(self, bot):
        """After handle() completes the user's slot must be free."""
        handler, mc, story_engine = _make_handler(bot)
        await handler.handle("ii99", "help", "Ivan")
        assert "ii99" not in handler._processing

    @pytest.mark.asyncio
    async def test_different_users_not_blocked(self, bot):
        """A held processing slot for one user must not block another user."""
        handler, mc, story_engine = _make_handler(bot)
        story_engine.has_session = MagicMock(return_value=True)

        # User A is being processed.
        handler._processing.add("aa11")

        # User B must still be handled normally.
        await handler.handle("bb22", "1", "Bob")
        story_engine.advance_story.assert_called_once_with("bb22", "1")

        handler._processing.discard("aa11")
