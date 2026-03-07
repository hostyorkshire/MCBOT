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
import types

from dotenv import load_dotenv
from meshcore import EventType, MeshCore

from story_engine import StoryEngine
from utils import chunk_message

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
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama3-8b-8192")
MAX_CHUNK_SIZE: int = int(os.getenv("MAX_CHUNK_SIZE", "200"))
CHUNK_DELAY: float = float(os.getenv("CHUNK_DELAY", "2.0"))
MAX_HISTORY: int = int(os.getenv("MAX_HISTORY", "10"))

HELP_TEXT: str = (
    "CYOA Bot: send 'start' to begin. "
    "Reply 1/2/3 to choose. "
    "'restart' resets your story. "
    "'help' shows this message."
)

# Valid single-digit choice commands
_CHOICES = {"1", "2", "3"}
# Commands that (re)start a story
_START_CMDS = {"start", "new", "begin"}
# Commands that reset the current story
_RESET_CMDS = {"restart", "reset"}
# Commands that show help
_HELP_CMDS = {"help", "?"}

# Prefixes that some MeshCore clients prepend to commands (e.g. /start, !start)
_CMD_PREFIXES = ("/", "!", "\\")


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
        lines.append(
            "No candidate serial devices found (/dev/ttyUSB*, /dev/ttyACM*)."
        )
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
        Parsed :class:`argparse.Namespace` with ``port`` and ``baud``
        attributes.
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
        help=(
            "Serial device path, e.g. /dev/ttyUSB0  "
            "[env: SERIAL_PORT, default: %(default)s]"
        ),
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=BAUD_RATE,
        metavar="RATE",
        help=(
            "Serial baud rate  "
            "[env: BAUD_RATE, default: %(default)s]"
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
        log.warning(
            "Unexpected drain result type %s – skipping", type(result).__name__
        )
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
    """Try candidate drain methods on *commands* and return normalised payloads.

    Iterates over :data:`_DRAIN_CANDIDATES` and calls the first method that
    exists on *commands*.  Handles :exc:`TypeError` (signature mismatch) and
    falls through to the next candidate.  Returns an empty list if no candidate
    succeeds.

    Args:
        commands: The ``mc.commands`` object from a connected
            :class:`~meshcore.MeshCore` instance.

    Returns:
        List of normalised payload dicts with at least ``pubkey_prefix`` and
        ``text`` keys.
    """
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
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "mc.commands.%s() raised an unexpected error: %s", name, exc
            )
            continue
        return _normalise_drain_result(result)

    log.warning(
        "No inbox-drain method found on mc.commands (tried: %s)",
        ", ".join(_DRAIN_CANDIDATES),
    )
    return []


async def send_chunked(
    mc: MeshCore,
    destination: str,
    text: str,
    chunk_size: int,
    delay: float,
) -> None:
    """Send *text* to *destination*, splitting into LoRa-sized chunks.

    Args:
        mc: Connected :class:`~meshcore.MeshCore` instance.
        destination: Pubkey prefix hex string of the recipient.
        text: Full message text (may be longer than one LoRa packet).
        chunk_size: Maximum characters per chunk.
        delay: Seconds to wait between consecutive chunks.
    """
    chunks = chunk_message(text, chunk_size)
    for i, chunk in enumerate(chunks):
        if i > 0:
            await asyncio.sleep(delay)
        try:
            await mc.commands.send_msg(destination, chunk)
            log.debug("Sent chunk %d/%d to %s", i + 1, len(chunks), destination)
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to send chunk %d to %s: %s", i + 1, destination, exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main(argv: list[str] | None = None) -> None:
    """Connect to MeshCore and run the CYOA bot event loop."""
    args = _parse_args(argv)
    serial_port: str = args.port
    baud_rate: int = args.baud

    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY environment variable is not set. "
            "Get a free key at https://console.groq.com"
        )

    story_engine = StoryEngine(
        api_key=GROQ_API_KEY,
        model=GROQ_MODEL,
        max_history=MAX_HISTORY,
    )

    log.info("Connecting to MeshCore at %s (baud %d)…", serial_port, baud_rate)
    mc = await MeshCore.create_serial(serial_port, baud_rate)
    if mc is None:
        raise ConnectionError(_connection_error_hint(serial_port, baud_rate))
    log.info("Connected to MeshCore.")

    # Fetch contacts so the library's internal cache is populated and we can
    # resolve sender names later.
    await mc.commands.get_contacts()

    async def handle_message(event) -> None:  # type: ignore[type-arg]
        """Process an incoming direct message event."""
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

        command = _normalize_command(text)

        # --- help ---
        if command in _HELP_CMDS:
            log.info("Help command from %s (%s)", user_name, pubkey_prefix)
            await send_chunked(mc, pubkey_prefix, HELP_TEXT, MAX_CHUNK_SIZE, CHUNK_DELAY)
            log.info("Sent help text to %s (%s)", user_name, pubkey_prefix)
            return

        # --- reset then start ---
        if command in _RESET_CMDS:
            log.info("Reset command from %s (%s)", user_name, pubkey_prefix)
            story_engine.clear_session(pubkey_prefix)
            command = "start"

        # --- start new adventure ---
        if command in _START_CMDS:
            log.info(
                "Start command from %s (%s) – beginning new adventure",
                user_name,
                pubkey_prefix,
            )
            response = await story_engine.start_story(pubkey_prefix, user_name)
            log.info("Sending story opening to %s (%s)", user_name, pubkey_prefix)

        # --- numbered choice ---
        elif command in _CHOICES:
            response = await story_engine.advance_story(pubkey_prefix, command)

        # --- free-text input while in a session ---
        elif story_engine.has_session(pubkey_prefix):
            response = await story_engine.advance_story(pubkey_prefix, text)

        # --- unknown command, no active session ---
        else:
            log.info(
                "Unknown command %r from %s (%s) – sending help",
                command,
                user_name,
                pubkey_prefix,
            )
            await send_chunked(mc, pubkey_prefix, HELP_TEXT, MAX_CHUNK_SIZE, CHUNK_DELAY)
            return

        await send_chunked(mc, pubkey_prefix, response, MAX_CHUNK_SIZE, CHUNK_DELAY)

    mc.subscribe(EventType.CONTACT_MSG_RECV, handle_message)

    _drain_lock = asyncio.Lock()

    async def handle_messages_waiting(event) -> None:  # type: ignore[type-arg]
        """Drain queued messages when MeshCore signals MESSAGES_WAITING."""
        if _drain_lock.locked():
            log.debug(
                "Drain already in progress – skipping MESSAGES_WAITING event"
            )
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

    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Shutting down CYOA Bot…")
    finally:
        await mc.disconnect()
        log.info("Disconnected.")


if __name__ == "__main__":
    asyncio.run(main())
