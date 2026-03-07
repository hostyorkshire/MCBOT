#!/usr/bin/env python3
"""MeshCore CYOA Bot – main entry point.

Connects to a MeshCore LoRa radio over USB serial and runs a
Create Your Own Adventure (CYOA) story bot powered by the Groq cloud LLM.

Designed to run continuously on a Raspberry Pi Zero 2W.

Usage::

    python cyoa_bot.py

Configuration is entirely via environment variables (see ``.env.example``).
"""

from __future__ import annotations

import asyncio
import logging
import os

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


async def main() -> None:
    """Connect to MeshCore and run the CYOA bot event loop."""
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

    log.info("Connecting to MeshCore at %s (baud %d)…", SERIAL_PORT, BAUD_RATE)
    mc = await MeshCore.create_serial(SERIAL_PORT, BAUD_RATE)
    if mc is None:
        raise ConnectionError(
            f"Could not connect to MeshCore device on {SERIAL_PORT}. "
            "Check the serial port and baud rate."
        )
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

        log.info("Message from %s (%s): %s", user_name, pubkey_prefix, text)

        command = text.lower()

        # --- help ---
        if command in _HELP_CMDS:
            await send_chunked(mc, pubkey_prefix, HELP_TEXT, MAX_CHUNK_SIZE, CHUNK_DELAY)
            return

        # --- reset then start ---
        if command in _RESET_CMDS:
            story_engine.clear_session(pubkey_prefix)
            command = "start"

        # --- start new adventure ---
        if command in _START_CMDS:
            response = await story_engine.start_story(pubkey_prefix, user_name)

        # --- numbered choice ---
        elif command in _CHOICES:
            response = await story_engine.advance_story(pubkey_prefix, command)

        # --- free-text input while in a session ---
        elif story_engine.has_session(pubkey_prefix):
            response = await story_engine.advance_story(pubkey_prefix, text)

        # --- unknown command, no active session ---
        else:
            await send_chunked(mc, pubkey_prefix, HELP_TEXT, MAX_CHUNK_SIZE, CHUNK_DELAY)
            return

        await send_chunked(mc, pubkey_prefix, response, MAX_CHUNK_SIZE, CHUNK_DELAY)

    mc.subscribe(EventType.CONTACT_MSG_RECV, handle_message)
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
