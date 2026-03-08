"""Story archive for completed MCBOT adventures.

Completed stories are written to a JSON file so the dashboard can serve full
replay pages via protected URL tokens.  Each entry is identified by a
URL-safe random token that is only shared with the player when their story
ends; there is no directory listing exposed to unauthenticated users.

Archive file location: ``dashboard/story_archive.json`` (next to this module).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import time

log = logging.getLogger(__name__)

# Absolute path to the archive JSON file regardless of working directory.
ARCHIVE_FILE: str = os.path.join(os.path.dirname(__file__), "story_archive.json")

# Maximum number of stories retained on disk (oldest pruned first).
MAX_ARCHIVED_STORIES: int = 500


def archive_story(story_data: dict) -> str:
    """Archive a completed story and return its unique access token.

    A URL-safe random token is generated and embedded in the entry before
    writing.  The on-disk list is pruned to at most
    :data:`MAX_ARCHIVED_STORIES` entries after each write.

    Args:
        story_data: Dict with at least ``user_key``, ``user_name``, ``genre``,
            ``started_at``, and ``history`` keys.

    Returns:
        URL-safe access token string for the new archive entry.
    """
    token = secrets.token_urlsafe(16)
    entry = {
        **story_data,
        "token": token,
        "archived_at": time.time(),
    }
    stories = _read_archive()
    stories.append(entry)
    # Prune oldest entries to keep the file from growing indefinitely.
    if len(stories) > MAX_ARCHIVED_STORIES:
        stories = stories[-MAX_ARCHIVED_STORIES:]
    _write_archive(stories)
    log.info("Archived story for %s (token=%s)", story_data.get("user_name", "?"), token)
    return token


def get_story_by_token(token: str) -> dict | None:
    """Return the archived story dict for *token*, or ``None`` if not found."""
    for story in _read_archive():
        if story.get("token") == token:
            return story
    return None


def list_archived_stories() -> list[dict]:
    """Return summary dicts for all archived stories (history field excluded)."""
    return [{k: v for k, v in s.items() if k != "history"} for s in _read_archive()]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_archive() -> list[dict]:
    """Load the archive from disk; return an empty list on any error."""
    try:
        with open(ARCHIVE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _write_archive(stories: list[dict]) -> None:
    """Atomically write *stories* to the archive file."""
    tmp = ARCHIVE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(stories, fh, default=str, indent=2)
        os.replace(tmp, ARCHIVE_FILE)
    except OSError as exc:
        log.warning("Could not write story archive: %s", exc)
        with contextlib.suppress(OSError):
            os.unlink(tmp)
