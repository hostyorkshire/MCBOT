"""Persistent story log for the MCBOT dashboard.

Keeps the last :data:`MAX_STORIES` sessions (whether active, finished, or
restarted) in a JSON file so the dashboard can display them even after a
session ends.

Each session entry should include a ``story_id`` (a UUID4 string) as its
unique identifier so that multiple sessions for the same user can coexist.
Entries without a ``story_id`` fall back to keying by ``user_key`` for
backward compatibility with the old schema.

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
MAX_STORIES: int = 50

_lock = threading.Lock()


def upsert_story(story: dict) -> None:
    """Add or update a story entry in the log.

    When *story* contains a ``story_id`` key it is used as the unique
    identifier, allowing multiple sessions for the same ``user_key`` to be
    retained independently.  When ``story_id`` is absent the legacy
    ``user_key`` is used as the identifier (backward-compatible behaviour).

    When the list exceeds :data:`MAX_STORIES` entries the oldest entries (by
    ``started_at``) are removed first.

    The write is atomic – a temporary file is renamed into place so readers
    never see a partial update.

    Args:
        story: A plain dict with at minimum a ``user_key`` key (and ideally a
            ``story_id`` key) plus a ``started_at`` float timestamp.  All
            keys are stored as-is.
    """
    story_id = story.get("story_id")
    user_key = story.get("user_key")
    if not story_id and not user_key:
        log.warning("upsert_story called with missing story_id and user_key; skipping")
        return

    log.debug(
        "upsert_story: story_id=%s user_key=%s finished=%s started_at=%s",
        story_id,
        user_key,
        story.get("finished"),
        story.get("started_at"),
    )

    with _lock:
        stories = _load_locked()
        if story_id:
            # New schema: key by story_id – allows multiple sessions per user.
            stories = [s for s in stories if s.get("story_id") != story_id]
        else:
            # Backward compat: key by user_key (one entry per user).
            stories = [s for s in stories if s.get("user_key") != user_key]
        stories.append(story)
        # Enforce the cap: sort oldest-first, keep the newest MAX_STORIES.
        stories.sort(key=lambda s: s.get("started_at", 0))
        stories = stories[-MAX_STORIES:]
        _write_locked(stories)
        log.debug("upsert_story: wrote %d stories to %s", len(stories), STORIES_FILE)


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
        log.error(
            "active_stories: expected a JSON list in %s, got %s; returning empty",
            STORIES_FILE,
            type(data).__name__,
        )
    except FileNotFoundError:
        pass  # Normal on first run – not an error.
    except (OSError, json.JSONDecodeError) as exc:
        log.error("active_stories: failed to load %s: %s", STORIES_FILE, exc)
    return []


def _write_locked(stories: list[dict]) -> None:
    """Atomically write *stories* to :data:`STORIES_FILE`."""
    tmp = STORIES_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(stories, fh, default=str)
        os.replace(tmp, STORIES_FILE)
    except OSError as exc:
        log.error("active_stories: failed to write %s: %s", STORIES_FILE, exc)
        with contextlib.suppress(OSError):
            os.unlink(tmp)
