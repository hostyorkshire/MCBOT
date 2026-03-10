#!/usr/bin/env python3
"""MeshCore CYOA Bot – main entry point.

Connects to a MeshCore LoRa radio over USB serial and runs a
Create Your Own Adventure (CYOA) story bot powered by the Groq cloud LLM.

Designed to run continuously on a Raspberry Pi Zero 2W.

Usage::

    python cyoa_bot.py [--port /dev/ttyUSB0] [--baud 115200]

CLI options override the corresponding environment variables
(``SERIAL_PORT``, ``BAUD_RATE``).  All other settings are configured via
environment variables or the ``.env`` file (see ``.env.example``).
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import logging
import os
import random
import sys
import threading
import time
import types

from dotenv import load_dotenv
from meshcore import EventType, MeshCore

from story_engine import DEFAULT_GENRE, GENRES, StoryEngine
from utils import chunk_message

# ---------------------------------------------------------------------------
# Optional dashboard state writer – imported lazily so the bot starts even
# if the dashboard package is not installed.
# ---------------------------------------------------------------------------
try:
    from dashboard.active_stories import STORIES_FILE as _STORIES_FILE
    from dashboard.app import create_app as _dashboard_create_app
    from dashboard.app import socketio as _dashboard_socketio
    from dashboard.state import STATE_FILE as _STATE_FILE
    from dashboard.state import write_state as _write_dashboard_state

    _DASHBOARD_ENABLED = True
except ImportError:  # pragma: no cover – optional dependency
    _DASHBOARD_ENABLED = False
    _STATE_FILE: str | None = None  # type: ignore[assignment]
    _STORIES_FILE: str | None = None  # type: ignore[assignment]

    def _write_dashboard_state(_data: dict) -> None:  # type: ignore[misc]
        """No-op fallback when the dashboard package is unavailable."""


def _clear_session_files() -> None:
    """Delete persistent session and story files at startup for a clean slate.

    Removes ``bot_state.json`` and ``active_stories.json`` if they exist so
    that every bot restart begins with no leftover session or story data.
    This is intentional behaviour during testing.
    """
    for path in (_STATE_FILE, _STORIES_FILE):
        if path and os.path.exists(path):
            try:
                os.remove(path)
                log.info("Startup: cleared session file %s", path)
            except OSError as exc:  # pragma: no cover
                log.warning("Startup: could not remove %s: %s", path, exc)


def _start_dashboard_server(*, host: str = "0.0.0.0", port: int = 5000) -> None:
    """Launch the Flask-SocketIO dashboard in a background daemon thread.

    Binding to ``host="0.0.0.0"`` makes the dashboard reachable on *all*
    network interfaces (LAN, Wi-Fi, …) at ``http://<machine-ip>:5000/dashboard/``.
    Using a daemon thread means the server shuts down automatically when the
    main bot process exits – no separate cleanup is needed.
    """
    if not _DASHBOARD_ENABLED:
        return

    _debug = os.getenv("FLASK_DEBUG", "0") == "1"
    _app = _dashboard_create_app()

    def _run() -> None:
        try:
            # host="0.0.0.0" binds to all interfaces so the dashboard is
            # accessible from other machines on the local network.
            _dashboard_socketio.run(_app, host=host, port=port, debug=_debug, use_reloader=False)
        except Exception as exc:  # pragma: no cover
            log.error("Dashboard server failed to start: %s", exc)

    t = threading.Thread(target=_run, name="dashboard-server", daemon=True)
    t.start()
    log.info(
        "Dashboard server started on port %d – open http://localhost:%d/dashboard/ locally "
        "or http://<this-machine-ip>:%d/dashboard/ from another device on the LAN",
        port,
        port,
        port,
    )


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
SERIAL_PORT: str = os.getenv("SERIAL_PORT", "/dev/ttyUSB0")
BAUD_RATE: int = int(os.getenv("BAUD_RATE", "115200"))
GROQ_API_KEY: str | None = os.getenv("GROQ_API_KEY")
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
MAX_CHUNK_SIZE: int = int(os.getenv("MAX_CHUNK_SIZE", "200"))
CHUNK_DELAY: float = float(os.getenv("CHUNK_DELAY", "2.0"))
MAX_HISTORY: int = int(os.getenv("MAX_HISTORY", "10"))
SEND_RETRIES: int = int(os.getenv("SEND_RETRIES", "3"))
SEND_RETRY_BASE_DELAY: float = float(os.getenv("SEND_RETRY_BASE_DELAY", "0.5"))
SEND_RETRY_MAX_DELAY: float = float(os.getenv("SEND_RETRY_MAX_DELAY", "3.0"))

HELP_TEXT: str = (
    "Commands:\n"
    "- help / ? \u2014 show this message\n"
    "- genres \u2014 list genres\n"
    "- start / new / begin <genre name or number>\n"
    "- restart / reset \u2014 reset\n"
)

# Compact genre list sent in response to the ``genres`` command.
GENRES_TEXT: str = (
    "Genres: " + " ".join(f"{i + 1}.{gid}" for i, gid in enumerate(GENRES)) + " | start <name|#>"
)

# Introductory message sent when the StoryBot is invoked.
INTRO_MSG: str = (
    "Hello I'm the StoryBot. Type ? for a list of commands or start to begin your adventure."
)

# Valid single-digit choice commands
_CHOICES = {"1", "2", "3"}
# Commands that (re)start a story
_START_CMDS = {"start", "new", "begin"}
# Commands that reset the current story
_RESET_CMDS = {"restart", "reset"}
# Commands that show help
_HELP_CMDS = {"help", "?"}
# Commands that list available genres
_GENRES_CMDS = {"genres"}

# Prefixes that some MeshCore clients prepend to commands (e.g. /start, !start)
_CMD_PREFIXES = ("/", "!", "\\")

# All command tokens that the bot recognises (used for invocation detection).
_ALL_KNOWN_CMDS: frozenset[str] = frozenset(
    _HELP_CMDS | _GENRES_CMDS | _START_CMDS | _RESET_CMDS | _CHOICES
)

# Minimum seconds between help-hint replies to the same idle user.
_HELP_HINT_COOLDOWN: float = 300.0


# ---------------------------------------------------------------------------
# Serial diagnostics
# ---------------------------------------------------------------------------


def scan_serial_candidates() -> list[str]:
    """Return a sorted list of candidate serial device paths.

    Scans ``/dev/ttyUSB*`` and ``/dev/ttyACM*`` using :mod:`glob`.  Does not
    require ``pyserial`` – uses only the standard library.

    Returns:
        Sorted list of discovered device paths (may be empty).
    """
    candidates: list[str] = []
    for pattern in ("/dev/ttyUSB*", "/dev/ttyACM*"):
        candidates.extend(glob.glob(pattern))
    return sorted(candidates)


def _connection_error_hint(port: str, baud: int) -> str:
    """Return a human-readable diagnostic hint for a failed serial connection.

    Args:
        port: The serial device path that was attempted.
        baud: The baud rate that was used.

    Returns:
        Multi-line hint string suitable for printing to the user.
    """
    candidates = scan_serial_candidates()

    lines: list[str] = [
        f"Could not connect to MeshCore device on {port} (baud {baud}).",
        "",
    ]

    if candidates:
        lines.append("Candidate serial devices found on this system:")
        for dev in candidates:
            lines.append(f"  {dev}")
        lines.append("")
        lines.append("Troubleshooting hints:")
        lines.append("  • Ensure your user is in the 'dialout' group:")
        lines.append("      sudo usermod -a -G dialout $USER && newgrp dialout")
        lines.append("  • Check device permissions:")
        lines.append("      ls -l /dev/ttyUSB0")
        lines.append("      ls -l /dev/ttyACM0")
        alt = next((d for d in candidates if d != port), candidates[0])
        lines.append(f"  • Try an alternate port, e.g.:  --port {alt}")
    else:
        lines.append("No candidate serial devices found (/dev/ttyUSB*, /dev/ttyACM*).")
        lines.append("  → Check USB cable and device power.")
        lines.append("  → Run: dmesg | tail -20  (look for ttyUSB or ttyACM)")
        lines.append("")
        lines.append("Troubleshooting hints:")
        lines.append("  • Ensure your user is in the 'dialout' group:")
        lines.append("      sudo usermod -a -G dialout $USER && newgrp dialout")
        lines.append("  • Check device permissions:")
        lines.append("      ls -l /dev/ttyUSB0")
        lines.append("      ls -l /dev/ttyACM0")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments, falling back to environment-variable defaults.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]`` when ``None``).

    Returns:
        Parsed :class:`argparse.Namespace` with ``port``, ``baud``, and
        ``check_env`` attributes.
    """
    parser = argparse.ArgumentParser(
        description=(
            "MeshCore CYOA Bot – connects to a MeshCore LoRa radio over USB "
            "serial and runs a Create Your Own Adventure story bot."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Environment variables (used as defaults when CLI flags are omitted):\n"
            "  SERIAL_PORT  Serial device path  (default: /dev/ttyUSB0)\n"
            "  BAUD_RATE    Serial baud rate    (default: 115200)\n"
        ),
    )
    parser.add_argument(
        "--port",
        default=SERIAL_PORT,
        metavar="DEVICE",
        help=("Serial device path, e.g. /dev/ttyUSB0  [env: SERIAL_PORT, default: %(default)s]"),
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=BAUD_RATE,
        metavar="RATE",
        help=("Serial baud rate  [env: BAUD_RATE, default: %(default)s]"),
    )
    parser.add_argument(
        "--check-env",
        action="store_true",
        help=(
            "Print whether required environment variables are set "
            "(does not print secret values) and exit."
        ),
    )
    return parser.parse_args(argv)


def _normalize_command(text: str) -> str:
    """Normalize *text* to a bare lower-case command token.

    Strips surrounding whitespace, removes a single leading ``/``, ``!``, or
    ``\\`` prefix, and lower-cases the result.  This makes the bot tolerant of
    clients that send ``/start``, ``!start``, etc.

    Args:
        text: Raw message text received from a MeshCore client.

    Returns:
        Normalized command string (e.g. ``"start"``).
    """
    cmd = text.strip().lower()
    if cmd and cmd[0] in _CMD_PREFIXES:
        cmd = cmd[1:]
    return cmd


def _parse_command(text: str) -> tuple[str, str]:
    """Parse *text* into a ``(command, argument)`` tuple.

    Like :func:`_normalize_command` but also extracts an optional argument
    following the command token.  Both the command and the argument are
    lower-cased.  For example ``"/start Horror"`` → ``("start", "horror")``.

    Args:
        text: Raw message text received from a MeshCore client.

    Returns:
        ``(command, arg)`` where *arg* is the lower-cased, stripped remainder
        after the command token, or an empty string when absent.
    """
    normalized = text.strip().lower()
    if normalized and normalized[0] in _CMD_PREFIXES:
        normalized = normalized[1:]
    parts = normalized.split(None, 1)
    if not parts:
        return "", ""
    return parts[0], parts[1].strip() if len(parts) > 1 else ""


def _is_invoked(text: str, command: str) -> bool:
    """Return ``True`` when a message is an explicit bot invocation.

    A message is considered an invocation when it begins with a command prefix
    (``/``, ``!``, or ``\\``) *or* when its normalised command token matches a
    known command.  Plain unknown words are **not** treated as invocations so
    the bot stays silent when users chat among themselves.

    Args:
        text: Raw message text as received.
        command: Normalised command token returned by :func:`_parse_command`.

    Returns:
        ``True`` if the message is an explicit bot invocation.
    """
    raw = text.strip()
    return bool(raw and raw[0] in _CMD_PREFIXES) or command in _ALL_KNOWN_CMDS


# Ordered list of genre IDs matching insertion order of the GENRES dict.
# GENRES is treated as immutable after module load; this list is therefore stable.
_GENRE_IDS: list[str] = list(GENRES.keys())


def _resolve_genre(arg: str) -> str | None:
    """Resolve *arg* to a genre ID.

    Accepts a genre ID (e.g. ``"horror"``) or a 1-based integer that
    refers to the numbered list shown by the ``genres`` command.

    Args:
        arg: Raw argument string extracted from the user's message.

    Returns:
        A genre ID from :data:`~story_engine.GENRES`, or ``None`` if
        *arg* is unrecognised.
    """
    arg = arg.strip().lower()
    if not arg:
        return None
    if arg in GENRES:
        return arg
    try:
        n = int(arg)
        if 1 <= n <= len(_GENRE_IDS):
            return _GENRE_IDS[n - 1]
    except ValueError:
        pass
    return None


def _split_story_choices(text: str) -> tuple[str, str]:
    """Split an LLM story reply into ``(narrative, choices)``.

    Scans for the first line that starts with ``1.`` or ``1)`` followed by a
    space and treats everything before it as the narrative and the rest as the
    choices block.

    .. note::
        The :func:`story_engine._format_reply` post-processor always normalises
        LLM output so that choices appear as ``1. …`` / ``2. …`` / ``3. …`` on
        separate lines before this function is ever called.  The ``1.`` and
        ``1)`` prefixes therefore cover all valid formats produced by the
        pipeline.

    Args:
        text: Post-processed LLM reply string (output of :func:`_format_reply`).

    Returns:
        ``(narrative, choices)`` where either part may be empty.  When no
        choices prefix is found the entire *text* is returned as *choices*
        with an empty *narrative*.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("1. ", "1) ")):
            narrative = "\n".join(lines[:i]).strip()
            choices = "\n".join(lines[i:]).strip()
            return narrative, choices
    return "", text.strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Candidate method names for draining the inbox, tried in order.
_DRAIN_CANDIDATES: tuple[str, ...] = (
    "get_messages",
    "read_messages",
    "drain_messages",
    "read_inbox",
    "drain_inbox",
    "inbox",
)


def _normalise_drain_result(result: object) -> list[dict]:
    """Convert the raw return value of a drain method to a list of payload dicts.

    Handles:

    - A list of dicts (returned directly).
    - A dict with a ``messages`` key containing a list.
    - A single dict (wrapped in a list).
    - Anything else (logged and skipped).

    Args:
        result: Raw return value from an inbox-drain method.

    Returns:
        List of dicts, each guaranteed to contain at least ``pubkey_prefix``
        and ``text`` keys (empty strings when the source data lacks them).
    """
    items: list[object]
    if isinstance(result, list):
        items = result
    elif isinstance(result, dict):
        inner = result.get("messages")
        if isinstance(inner, list):
            items = inner
        else:
            items = [result]
    else:
        log.warning("Unexpected drain result type %s – skipping", type(result).__name__)
        return []

    payloads: list[dict] = []
    for item in items:
        if isinstance(item, dict):
            payloads.append(
                {
                    **item,
                    "pubkey_prefix": item.get("pubkey_prefix", ""),
                    "text": item.get("text", ""),
                }
            )
        else:
            log.warning("Skipping non-dict drain item: %r", item)
    return payloads


async def _drain_inbox(commands: object) -> list[dict]:
    """Drain queued inbox messages and return normalised payloads.

    Tries ``mc.commands.get_msg()`` first (meshcore 2.2.x+): repeatedly calls
    it until an :attr:`~meshcore.EventType.NO_MORE_MSGS` event is received,
    deduplicating payloads within the same drain session to guard against any
    firmware that may echo the same event twice.

    Falls back to :data:`_DRAIN_CANDIDATES` (bulk-drain methods present in
    older meshcore builds) when ``get_msg`` is not available.

    Args:
        commands: The ``mc.commands`` object from a connected
            :class:`~meshcore.MeshCore` instance.

    Returns:
        List of normalised payload dicts with at least ``pubkey_prefix`` and
        ``text`` keys.
    """
    # ------------------------------------------------------------------
    # meshcore 2.2.x+: iterative get_msg() drain
    # ------------------------------------------------------------------
    get_msg = getattr(commands, "get_msg", None)
    if get_msg is not None:
        log.info("Draining inbox via mc.commands.get_msg() loop (meshcore 2.2.x+)")
        payloads: list[dict] = []
        seen: set[tuple[str, str]] = set()
        while True:
            try:
                event = await get_msg()
            except Exception as exc:
                log.warning("mc.commands.get_msg() raised an error: %s", exc)
                break
            event_type = getattr(event, "type", None)
            if event_type == EventType.NO_MORE_MSGS:
                log.info(
                    "get_msg() drain complete – %d message(s) received",
                    len(payloads),
                )
                break
            raw_payload = getattr(event, "payload", None)
            if not isinstance(raw_payload, dict):
                continue
            pk: str = raw_payload.get("pubkey_prefix", "")
            txt: str = raw_payload.get("text", "")
            dedup_key = (pk, txt)
            if dedup_key in seen:
                log.debug("Skipping duplicate get_msg() payload from %s: %r", pk, txt)
                continue
            seen.add(dedup_key)
            payloads.append({**raw_payload, "pubkey_prefix": pk, "text": txt})
        return payloads

    # ------------------------------------------------------------------
    # Fallback: bulk-drain candidates for older meshcore builds
    # ------------------------------------------------------------------
    for name in _DRAIN_CANDIDATES:
        method = getattr(commands, name, None)
        if method is None:
            continue
        log.info("Attempting inbox drain via mc.commands.%s()", name)
        try:
            result = await method()
        except TypeError as exc:
            log.warning("mc.commands.%s() signature mismatch: %s", name, exc)
            continue
        except Exception as exc:
            log.warning("mc.commands.%s() raised an unexpected error: %s", name, exc)
            continue
        return _normalise_drain_result(result)

    log.warning(
        "No inbox-drain method found on mc.commands (tried: get_msg, %s)",
        ", ".join(_DRAIN_CANDIDATES),
    )
    return []


def _check_env() -> None:
    """Print the status of required and optional environment variables.

    Shows whether each variable is set and (for non-secret values) its current
    value.  API keys are never printed – only their presence and length are
    reported.  This function always exits the process after printing.
    """
    vars_info = [
        ("GROQ_API_KEY", GROQ_API_KEY, True),
        ("GROQ_MODEL", GROQ_MODEL, False),
        ("SERIAL_PORT", SERIAL_PORT, False),
        ("BAUD_RATE", str(BAUD_RATE), False),
        ("MAX_CHUNK_SIZE", str(MAX_CHUNK_SIZE), False),
        ("CHUNK_DELAY", str(CHUNK_DELAY), False),
        ("MAX_HISTORY", str(MAX_HISTORY), False),
    ]
    print("Environment variable check:")
    all_ok = True
    for name, value, is_secret in vars_info:
        # Treat any variable whose name contains KEY or SECRET as sensitive,
        # regardless of the is_secret flag, to guard against misconfiguration.
        treat_as_secret = is_secret or any(
            kw in name.upper() for kw in ("KEY", "SECRET", "PASSWORD", "TOKEN")
        )
        if value:
            if treat_as_secret:
                status = f"SET (length {len(value)})"
            else:
                status = f"SET ({value})"
        else:
            status = "NOT SET ✗"
            all_ok = False
        print(f"  {name}: {status}")
    if not all_ok:
        print("\n✗ One or more required variables are missing. Edit your .env file.")
        raise SystemExit(1)
    print("\n✓ All required variables are set.")
    raise SystemExit(0)


async def send_chunked(
    mc: MeshCore,
    destination: str,
    text: str,
    chunk_size: int,
    delay: float,
    retries: int = SEND_RETRIES,
    retry_base_delay: float = SEND_RETRY_BASE_DELAY,
    retry_max_delay: float = SEND_RETRY_MAX_DELAY,
) -> None:
    """Send *text* to *destination*, splitting into LoRa-sized chunks.

    Each chunk is attempted up to *retries* times with exponential backoff and
    a small random jitter between attempts.  Only sleeps between retry attempts
    (not before the first try).  On final failure the error is logged and the
    function continues to the next chunk, preserving existing non-raising
    behaviour.

    Args:
        mc: Connected :class:`~meshcore.MeshCore` instance.
        destination: Pubkey prefix hex string of the recipient.
        text: Full message text (may be longer than one LoRa packet).
        chunk_size: Maximum characters per chunk.
        delay: Seconds to wait between consecutive chunks.
        retries: Maximum number of send attempts per chunk (must be ≥ 1).
        retry_base_delay: Base delay in seconds for the first retry backoff.
        retry_max_delay: Maximum delay in seconds between retries.
    """
    chunks = chunk_message(text, chunk_size)
    for i, chunk in enumerate(chunks):
        if i > 0:
            await asyncio.sleep(delay)
        last_exc: Exception | None = None
        for attempt in range(retries):
            if attempt > 0:
                backoff = min(retry_base_delay * (2 ** (attempt - 1)), retry_max_delay)
                jitter = backoff * 0.1 * random.random()
                await asyncio.sleep(backoff + jitter)
            try:
                await mc.commands.send_msg(destination, chunk)
                log.debug(
                    "Sent chunk %d/%d to %s (attempt %d)",
                    i + 1,
                    len(chunks),
                    destination,
                    attempt + 1,
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if attempt + 1 < retries:
                    log.warning(
                        "Attempt %d/%d failed for chunk %d to %s: %s",
                        attempt + 1,
                        retries,
                        i + 1,
                        destination,
                        exc,
                    )
        if last_exc is not None:
            log.error(
                "All %d attempts failed for chunk %d to %s: %s",
                retries,
                i + 1,
                destination,
                last_exc,
            )


# ---------------------------------------------------------------------------
# BotHandler – per-user state machine
# ---------------------------------------------------------------------------


class BotHandler:
    """Manages per-user state and dispatches incoming messages.

    Handles the story start flow and dispatches commands independently of the
    hardware layer so that it can be tested with mocked dependencies.

    Args:
        mc: Connected :class:`~meshcore.MeshCore` instance (or compatible mock).
        story_engine: :class:`StoryEngine` used to generate story text.
        max_chunk_size: Maximum characters per LoRa chunk.
        chunk_delay: Seconds between consecutive chunks.
    """

    def __init__(
        self,
        mc: object,
        story_engine: StoryEngine,
        max_chunk_size: int = MAX_CHUNK_SIZE,
        chunk_delay: float = CHUNK_DELAY,
    ) -> None:
        self.mc = mc
        self.story_engine = story_engine
        self.max_chunk_size = max_chunk_size
        self.chunk_delay = chunk_delay
        # pubkey_prefixes whose message is currently being processed; guards
        # against double-dispatch when both CONTACT_MSG_RECV and
        # MESSAGES_WAITING fire for the same incoming message.
        self._processing: set[str] = set()
        # pubkey_prefix → monotonic timestamp of the last help-hint sent.
        self._last_help_hint: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _send(self, destination: str, text: str) -> None:
        """Send *text* to *destination* using :func:`send_chunked`."""
        await send_chunked(self.mc, destination, text, self.max_chunk_size, self.chunk_delay)

    async def _send_story(self, destination: str, response: str) -> None:
        """Send a story *response*, splitting narrative from choices into two separate messages.

        The narrative (scene description) is sent first; the numbered choices
        are sent as a second message after :attr:`chunk_delay` seconds.  This
        ensures the user always sees the options in a distinct, final message
        before being expected to reply.

        If *response* contains no choices prefix the whole text is sent as a
        single message (e.g. gate / cooldown messages).

        The inter-message delay is only inserted when a narrative was actually
        sent first – if there is no narrative the choices are the opening
        message and no preceding delay is required.
        """
        narrative, choices = _split_story_choices(response)
        if narrative:
            await self._send(destination, narrative)
            # Allow the radio channel to clear before sending the choices.
            await asyncio.sleep(self.chunk_delay)
        if choices:
            await self._send(destination, choices)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def handle(self, pubkey_prefix: str, text: str, user_name: str) -> None:
        """Dispatch an incoming message from *pubkey_prefix*.

        A per-user processing guard prevents the same user's message from being
        handled twice concurrently (e.g. when both ``CONTACT_MSG_RECV`` and
        ``MESSAGES_WAITING`` fire for the same inbound packet).

        Args:
            pubkey_prefix: Sender's pubkey prefix (used as user key).
            text: Raw message text exactly as received.
            user_name: Friendly display name for the sender.
        """
        if pubkey_prefix in self._processing:
            log.debug(
                "Dropping duplicate/concurrent message from %s (%s) – "
                "previous message is still being handled",
                user_name,
                pubkey_prefix,
            )
            return

        self._processing.add(pubkey_prefix)
        try:
            await self._dispatch(pubkey_prefix, text, user_name)
        finally:
            self._processing.discard(pubkey_prefix)

    async def _dispatch(self, pubkey_prefix: str, text: str, user_name: str) -> None:
        """Inner dispatch logic, called only when no concurrent handling is active."""
        command, arg = _parse_command(text)

        # --- help ---
        if command in _HELP_CMDS:
            log.info("Help command from %s (%s)", user_name, pubkey_prefix)
            await self._send(pubkey_prefix, HELP_TEXT)
            log.info("Sent help text to %s (%s)", user_name, pubkey_prefix)
            return

        # --- genres list ---
        if command in _GENRES_CMDS:
            log.info("Genres command from %s (%s)", user_name, pubkey_prefix)
            await self._send(pubkey_prefix, GENRES_TEXT)
            return

        # --- reset then start ---
        if command in _RESET_CMDS:
            log.info("Reset command from %s (%s)", user_name, pubkey_prefix)
            self.story_engine.clear_session(pubkey_prefix)
            command = "start"

        # --- start (also reached after reset) ---
        if command in _START_CMDS:
            genre = DEFAULT_GENRE
            if arg:
                resolved = _resolve_genre(arg)
                if resolved is None:
                    log.info(
                        "Unknown genre %r from %s (%s)",
                        arg,
                        user_name,
                        pubkey_prefix,
                    )
                    await self._send(
                        pubkey_prefix,
                        f"Unknown genre '{arg}'. Type 'genres' for list.",
                    )
                    return
                genre = resolved
            log.info(
                "Start command from %s (%s) genre=%s – starting story",
                user_name,
                pubkey_prefix,
                genre,
            )
            response = await self.story_engine.start_story(pubkey_prefix, user_name, genre=genre)
            await self._send_story(pubkey_prefix, response)
            return

        # ------------------------------------------------------------------
        # Normal dispatch (not a command).
        # ------------------------------------------------------------------

        # --- numbered choice ---
        if command in _CHOICES:
            response = await self.story_engine.advance_story(pubkey_prefix, command)

        # --- free-text input while in a session ---
        elif self.story_engine.has_session(pubkey_prefix):
            response = await self.story_engine.advance_story(pubkey_prefix, text)

        # --- unknown command, no active session ---
        else:
            now = time.monotonic()
            last = self._last_help_hint.get(pubkey_prefix, float("-inf"))
            if now - last < _HELP_HINT_COOLDOWN:
                log.debug(
                    "Help hint rate-limited for %s (%s) – skipping",
                    user_name,
                    pubkey_prefix,
                )
                return
            self._last_help_hint[pubkey_prefix] = now
            if not _is_invoked(text, command):
                log.info(
                    "First contact from %s (%s) – sending intro",
                    user_name,
                    pubkey_prefix,
                )
                await self._send(pubkey_prefix, INTRO_MSG)
            else:
                log.info(
                    "Unknown command %r from %s (%s) – sending help",
                    command,
                    user_name,
                    pubkey_prefix,
                )
                await self._send(pubkey_prefix, HELP_TEXT)
            return

        await self._send_story(pubkey_prefix, response)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main(argv: list[str] | None = None) -> None:
    """Connect to MeshCore and run the CYOA bot event loop."""
    args = _parse_args(argv)

    if args.check_env:
        _check_env()  # prints and exits

    # Clear any leftover session/story files from a previous run so every
    # restart begins with a completely clean slate (important during testing).
    _clear_session_files()

    serial_port: str = args.port
    baud_rate: int = args.baud

    if not GROQ_API_KEY:
        log.error(
            "GROQ_API_KEY environment variable is not set or empty. "
            "Get a free key at https://console.groq.com and add it to .env."
        )
        sys.exit(1)

    log.info("Using Groq model: %s", GROQ_MODEL)

    story_engine = StoryEngine(
        api_key=GROQ_API_KEY,
        model=GROQ_MODEL,
        max_history=MAX_HISTORY,
    )

    # Dashboard: record start time and error count at module level so the
    # periodic state writer can access them without closures over mutable vars.
    _bot_start_time: float = time.time()
    _bot_error_count: int = 0

    log.info("Connecting to MeshCore at %s (baud %d)…", serial_port, baud_rate)
    mc = await MeshCore.create_serial(serial_port, baud_rate)
    if mc is None:
        raise ConnectionError(_connection_error_hint(serial_port, baud_rate))
    log.info("Connected to MeshCore.")

    # Fetch contacts so the library's internal cache is populated and we can
    # resolve sender names later.
    await mc.commands.get_contacts()

    handler = BotHandler(
        mc=mc,
        story_engine=story_engine,
        max_chunk_size=MAX_CHUNK_SIZE,
        chunk_delay=CHUNK_DELAY,
    )

    async def handle_message(event) -> None:  # type: ignore[type-arg]
        """Process an incoming direct message event."""
        nonlocal _bot_error_count
        payload = event.payload
        pubkey_prefix: str = payload.get("pubkey_prefix", "")
        text: str = payload.get("text", "").strip()

        if not pubkey_prefix or not text:
            return

        # Look up a friendly name for the sender.
        contact = mc.get_contact_by_key_prefix(pubkey_prefix)
        user_name: str = (
            contact.get("adv_name", "Adventurer").strip() or "Adventurer"
            if contact
            else "Adventurer"
        )

        snippet = text[:80] + ("…" if len(text) > 80 else "")
        log.info("Message from %s (%s): %r", user_name, pubkey_prefix, snippet)

        try:
            await handler.handle(pubkey_prefix, text, user_name)
        except Exception:
            _bot_error_count += 1
            raise

    mc.subscribe(EventType.CONTACT_MSG_RECV, handle_message)

    _drain_lock = asyncio.Lock()

    async def handle_messages_waiting(event) -> None:  # type: ignore[type-arg]
        """Drain queued messages when MeshCore signals MESSAGES_WAITING."""
        if _drain_lock.locked():
            log.debug("Drain already in progress – skipping MESSAGES_WAITING event")
            return
        async with _drain_lock:
            log.info("MESSAGES_WAITING received – draining inbox")
            payloads = await _drain_inbox(mc.commands)
            for payload in payloads:
                # Wrap the raw payload so handle_message can access event.payload.
                event_wrapper = types.SimpleNamespace(payload=payload)
                await handle_message(event_wrapper)

    mc.subscribe(EventType.MESSAGES_WAITING, handle_messages_waiting)
    log.info("CYOA Bot is running. Waiting for messages…")

    async def _dashboard_state_writer() -> None:
        """Periodically write bot state to the dashboard state file."""
        while True:
            try:
                _write_dashboard_state(
                    {
                        "status": "running",
                        "start_time": _bot_start_time,
                        "uptime": time.time() - _bot_start_time,
                        "error_count": _bot_error_count,
                        "sessions": story_engine.get_sessions_info(),
                    }
                )
            except Exception as exc:
                log.debug("Dashboard state write failed: %s", exc)
            await asyncio.sleep(5)

    # Keep a strong reference to background tasks to prevent garbage collection.
    _background_tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]

    if _DASHBOARD_ENABLED:
        # Start the Flask dashboard web server in a background daemon thread.
        # Binds to 0.0.0.0:5000 so the dashboard is reachable from any device
        # on the local network at http://<machine-ip>:5000/dashboard/
        _start_dashboard_server(host="0.0.0.0", port=5000)

        _t = asyncio.ensure_future(_dashboard_state_writer())
        _background_tasks.add(_t)
        _t.add_done_callback(_background_tasks.discard)

    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Shutting down CYOA Bot…")
        _write_dashboard_state(
            {
                "status": "stopped",
                "start_time": _bot_start_time,
                "uptime": time.time() - _bot_start_time,
                "error_count": _bot_error_count,
                "sessions": story_engine.get_sessions_info(),
            }
        )
    finally:
        await mc.disconnect()
        log.info("Disconnected.")


if __name__ == "__main__":
    asyncio.run(main())
