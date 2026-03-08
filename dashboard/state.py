"""Shared bot-state helpers for the MCBOT dashboard.

The bot writes state to a JSON file every few seconds; the Flask dashboard
reads that file to serve the API endpoints.  This file-based approach keeps
the bot and dashboard fully decoupled – they never share a process or memory.

State file location: ``dashboard/bot_state.json`` (next to this module).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time

log = logging.getLogger(__name__)

# Absolute path to the JSON state file regardless of working directory.
STATE_FILE: str = os.path.join(os.path.dirname(__file__), "bot_state.json")

# How many seconds of silence before the bot is considered "idle".
IDLE_THRESHOLD: float = 15.0


def write_state(data: dict) -> None:
    """Atomically write *data* to the state file as JSON.

    Uses a temporary file + rename so readers never see a partial write.
    """
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, default=str)
        os.replace(tmp, STATE_FILE)
    except OSError as exc:
        log.warning("Could not write dashboard state: %s", exc)
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def read_state() -> dict | None:
    """Return the current bot state dict, or ``None`` if unavailable."""
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def get_status() -> dict:
    """Return a normalised status dict suitable for the API response.

    If no state file is found the bot is reported as offline.
    """
    state = read_state()
    if state is None:
        return {
            "status": "offline",
            "uptime": 0,
            "uptime_human": "—",
            "error_count": 0,
            "start_time": None,
        }

    # Re-compute uptime live from start_time so the value stays accurate even
    # if the bot hasn't written a fresh state file recently.
    start_time: float | None = state.get("start_time")
    uptime: float = (time.time() - start_time) if start_time else state.get("uptime", 0)

    status = state.get("status", "unknown")
    # If the file is stale, mark the bot as idle.
    mtime = _state_file_mtime()
    if mtime is not None and (time.time() - mtime) > IDLE_THRESHOLD:
        status = "idle"

    return {
        "status": status,
        "uptime": round(uptime, 1),
        "uptime_human": _format_uptime(uptime),
        "error_count": state.get("error_count", 0),
        "start_time": start_time,
    }


def get_sessions() -> list[dict]:
    """Return the list of active sessions from the state file."""
    state = read_state()
    if state is None:
        return []
    return state.get("sessions", [])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _state_file_mtime() -> float | None:
    """Return the modification time of the state file, or ``None``."""
    try:
        return os.path.getmtime(STATE_FILE)
    except OSError:
        return None


def _format_uptime(seconds: float) -> str:
    """Return a human-readable uptime string (e.g. ``"3h 42m 07s"``)."""
    seconds = max(0, int(seconds))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs:02d}s")
    return " ".join(parts)
