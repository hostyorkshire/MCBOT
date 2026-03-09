"""Persistent story log for the MCBOT dashboard.

Keeps the last :data:`MAX_STORIES` sessions (whether active, finished, or
restarted) in a JSON file so the dashboard can display them even after a
session ends.

Thread-safe via an in-process lock; file writes are atomic (tmp + rename).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading

log = logging.getLogger(__name__)

#: Absolute path to the persistent story log file.
STORIES_FILE: str = os.path.join(os.path.dirname(__file__), "active_stories.json")

#: Maximum number of story entries to retain.
MAX_STORIES: int = 20

_lock = threading.Lock()


def upsert_story(story: dict) -> None:
    """Add or update a story entry in the log.

    If a story with the same ``user_key`` already exists it is replaced
    in-place.  When the list exceeds :data:`MAX_STORIES` entries the oldest
    entries (by ``started_at``) are removed first.

    The write is atomic – a temporary file is renamed into place so readers
    never see a partial update.

    Args:
        story: A plain dict with at minimum a ``user_key`` key and a
            ``started_at`` float timestamp.  All keys are stored as-is.
    """
    user_key = story.get("user_key")
    if not user_key:
        log.warning("upsert_story called with missing user_key; skipping")
        return

    with _lock:
        stories = _load_locked()
        # Replace any existing entry for this user_key.
        stories = [s for s in stories if s.get("user_key") != user_key]
        stories.append(story)
        # Enforce the cap: sort oldest-first, keep the newest MAX_STORIES.
        stories.sort(key=lambda s: s.get("started_at", 0))
        stories = stories[-MAX_STORIES:]
        _write_locked(stories)


def load_stories() -> list[dict]:
    """Return the current story log (up to :data:`MAX_STORIES` entries).

    Returns an empty list if the file does not exist or cannot be read.
    """
    with _lock:
        return _load_locked()


# ---------------------------------------------------------------------------
# Internal helpers (must be called with _lock held)
# ---------------------------------------------------------------------------


def _load_locked() -> list[dict]:
    """Read the stories file; return an empty list on any error."""
    try:
        with open(STORIES_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _write_locked(stories: list[dict]) -> None:
    """Atomically write *stories* to :data:`STORIES_FILE`."""
    tmp = STORIES_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(stories, fh, default=str)
        os.replace(tmp, STORIES_FILE)
    except OSError as exc:
        log.warning("Could not write active_stories: %s", exc)
        with contextlib.suppress(OSError):
            os.unlink(tmp)
