"""Story archive for completed MCBOT adventures.

Completed stories are stored in a SQLite database so they survive restarts and
scale to thousands of entries without the read-back cost of a flat JSON file.
The database file location is controlled by the ``ARCHIVE_DB_PATH`` environment
variable so it can be placed on a USB SSD or any other mount point.

Each story is identified by a URL-safe random token that is only shared with
the player when their adventure ends — there is no unauthenticated listing.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
import time

log = logging.getLogger(__name__)

# Default database path: next to this module.  Override with ARCHIVE_DB_PATH.
_DEFAULT_DB_PATH: str = os.path.join(os.path.dirname(__file__), "stories.db")
ARCHIVE_DB_PATH: str = os.getenv("ARCHIVE_DB_PATH", _DEFAULT_DB_PATH)

# DDL executed once on first connection.
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS stories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token       TEXT    UNIQUE NOT NULL,
    user_key    TEXT    NOT NULL DEFAULT '',
    user_name   TEXT    NOT NULL DEFAULT '',
    genre       TEXT    NOT NULL DEFAULT '',
    genre_name  TEXT    NOT NULL DEFAULT '',
    started_at  REAL,
    ended_at    REAL,
    archived_at REAL    NOT NULL,
    end_reason  TEXT    NOT NULL DEFAULT '',
    chapters    INTEGER NOT NULL DEFAULT 1,
    history     TEXT    NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_stories_token ON stories (token);
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def archive_story(story_data: dict) -> str:
    """Persist a completed story and return its unique access token.

    A URL-safe random token is generated and stored alongside the story.
    There is no upper limit on the number of stored stories — the SQLite
    database grows as needed and is constrained only by available disk space.

    Args:
        story_data: Dict with at least ``user_key``, ``user_name``, ``genre``,
            ``started_at``, and ``history`` keys.

    Returns:
        URL-safe access token string for the new archive entry.
    """
    token = secrets.token_urlsafe(16)
    now = time.time()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO stories
                (token, user_key, user_name, genre, genre_name,
                 started_at, ended_at, archived_at, end_reason, chapters, history)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token,
                story_data.get("user_key", ""),
                story_data.get("user_name", ""),
                story_data.get("genre", ""),
                story_data.get("genre_name", ""),
                story_data.get("started_at"),
                story_data.get("ended_at"),
                now,
                story_data.get("end_reason", ""),
                story_data.get("chapters", 1),
                json.dumps(story_data.get("history", []), default=str),
            ),
        )
    log.info("Archived story for %s (token=%s)", story_data.get("user_name", "?"), token)
    return token


def get_story_by_token(token: str) -> dict | None:
    """Return the archived story dict for *token*, or ``None`` if not found."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM stories WHERE token = ?", (token,)
        ).fetchone()
    if row is None:
        return None
    return _row_to_dict(row, include_history=True)


def list_archived_stories() -> list[dict]:
    """Return summary dicts for all archived stories (history field excluded).

    Results are ordered oldest-first so the dashboard can reverse them for
    most-recent-first display.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, token, user_key, user_name, genre, genre_name, "
            "started_at, ended_at, archived_at, end_reason, chapters "
            "FROM stories ORDER BY id ASC"
        ).fetchall()
    return [_summary_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _connect() -> sqlite3.Connection:
    """Open (and initialise) the archive database, returning a connection.

    ``check_same_thread=False`` is safe here because each public function
    opens its own connection and closes it via the context manager.
    """
    conn = sqlite3.connect(ARCHIVE_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_CREATE_TABLE_SQL)
    conn.commit()
    return conn


def _row_to_dict(row: sqlite3.Row, *, include_history: bool) -> dict:
    """Convert a *Row* to a plain dict, optionally decoding the history JSON."""
    d = dict(row)
    if include_history:
        try:
            d["history"] = json.loads(d.get("history") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["history"] = []
    else:
        d.pop("history", None)
    return d


def _summary_row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a summary *Row* (no history column) to a plain dict."""
    return dict(row)
