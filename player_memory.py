"""Persistent per-user learning / preference store for the MCBOT.

Tracks play-style signals across sessions so the story engine can personalise
subsequent adventures without repeating the same experience.

Data is stored in a single JSON file (default: ``player_memory.json``).  The
schema is deliberately minimal to stay lightweight on a Raspberry Pi Zero 2W.

Persisted fields per user
--------------------------
* ``genre_counts``     – ``{genre_id: int}`` play-count per genre.
* ``risky_choices``    – cumulative count of high-risk choices (doom+2).
* ``safe_choices``     – cumulative count of low-risk choices (doom+0).
* ``total_choices``    – cumulative count of all choices recorded.
* ``sessions_started`` – number of adventure sessions started.
* ``sessions_completed`` – number of sessions that reached a story ending.
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import os
import tempfile
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Play-style thresholds
# ---------------------------------------------------------------------------

#: Fraction of risky choices above which a player is classified as "bold".
_BOLD_THRESHOLD: float = 0.55

#: Fraction of safe choices above which a player is classified as "cautious".
_CAUTIOUS_THRESHOLD: float = 0.55

#: Minimum choices needed before play-style classification kicks in.
_MIN_CHOICES_FOR_STYLE: int = 5

#: Minimum sessions needed before genre preference is reported.
_MIN_SESSIONS_FOR_GENRE: int = 2


# ---------------------------------------------------------------------------
# PlayerMemory
# ---------------------------------------------------------------------------


class PlayerMemory:
    """Persist and query per-user learning data.

    Args:
        filepath: Path to the JSON file used for persistent storage.
            Created automatically if it does not exist.
    """

    def __init__(self, filepath: str = "player_memory.json") -> None:
        self._filepath = filepath
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load existing player data from *filepath*, if present."""
        if not os.path.exists(self._filepath):
            log.debug("Player memory file not found – starting fresh: %s", self._filepath)
            return
        try:
            with open(self._filepath, encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                self._data = raw
                log.info(
                    "Loaded player memory for %d user(s) from %s",
                    len(self._data),
                    self._filepath,
                )
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not load player memory from %s: %s – starting fresh", self._filepath, exc)

    def save(self) -> None:
        """Atomically write current player data to *filepath*."""
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(os.path.abspath(self._filepath)) or ".",
            prefix=".player_memory_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
            os.replace(tmp_path, self._filepath)
            log.debug("Saved player memory to %s", self._filepath)
        except OSError as exc:
            log.error("Failed to save player memory to %s: %s", self._filepath, exc)
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)

    # ------------------------------------------------------------------
    # Profile access
    # ------------------------------------------------------------------

    def _get_or_create(self, user_key: str) -> dict[str, Any]:
        """Return the profile dict for *user_key*, creating it if absent."""
        if user_key not in self._data:
            self._data[user_key] = {
                "genre_counts": {},
                "risky_choices": 0,
                "safe_choices": 0,
                "total_choices": 0,
                "sessions_started": 0,
                "sessions_completed": 0,
            }
        return self._data[user_key]

    def get_profile(self, user_key: str) -> dict[str, Any]:
        """Return a deep copy of the profile for *user_key* (or a blank profile).

        Returns a deep copy so callers cannot accidentally mutate internal state,
        including nested structures such as ``genre_counts``.
        """
        return copy.deepcopy(self._get_or_create(user_key))

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_session_start(self, user_key: str, genre: str) -> None:
        """Increment session count and genre tally for *user_key*.

        Args:
            user_key: Unique identifier for the user (pubkey_prefix).
            genre: Genre ID of the story being started.
        """
        profile = self._get_or_create(user_key)
        profile["sessions_started"] += 1
        genre_counts: dict[str, int] = profile.setdefault("genre_counts", {})
        genre_counts[genre] = genre_counts.get(genre, 0) + 1
        self.save()

    def record_choice(self, user_key: str, risk_score: int) -> None:
        """Record a choice with its pre-computed *risk_score*.

        Args:
            user_key: Unique identifier for the user.
            risk_score: Value returned by :func:`story_engine.classify_choice`;
                ``0`` = safe, ``1`` = neutral, ``2`` = risky.
        """
        profile = self._get_or_create(user_key)
        profile["total_choices"] += 1
        if risk_score == 2:
            profile["risky_choices"] += 1
        elif risk_score == 0:
            profile["safe_choices"] += 1
        # Neutral choices (risk_score == 1) are counted in total_choices only.
        # Choices are batch-persisted when the session ends via record_session_end.
        # If the process exits before session end, the unsaved choice counts for
        # the current session are lost; genre counts and session-start counts
        # are always written immediately by record_session_start.

    def record_session_end(self, user_key: str, *, completed: bool = True) -> None:
        """Mark a session as ended for *user_key* and persist.

        Args:
            user_key: Unique identifier for the user.
            completed: ``True`` if the story reached a narrative ending
                (peril finale, player-chosen end); ``False`` if abandoned.
        """
        profile = self._get_or_create(user_key)
        if completed:
            profile["sessions_completed"] += 1
        self.save()

    # ------------------------------------------------------------------
    # Personalisation hint
    # ------------------------------------------------------------------

    def get_personalization_hint(self, user_key: str) -> str:
        """Return a short personalisation note for the LLM system prompt.

        The hint is non-empty only when there is enough data to be useful.
        An empty string is returned for first-time or data-sparse users.

        Args:
            user_key: Unique identifier for the user.

        Returns:
            A short English sentence (or empty string) describing the player's
            preferences, suitable for appending to the LLM system prompt.
        """
        profile = self._data.get(user_key)
        if not profile:
            return ""

        parts: list[str] = []

        # --- Genre preference ---
        genre_counts: dict[str, int] = profile.get("genre_counts", {})
        sessions_started: int = profile.get("sessions_started", 0)
        if sessions_started >= _MIN_SESSIONS_FOR_GENRE and genre_counts:
            favourite = max(genre_counts, key=lambda g: genre_counts[g])
            parts.append(f"This player's favourite genre is '{favourite}'.")

        # --- Play-style preference ---
        total: int = profile.get("total_choices", 0)
        risky: int = profile.get("risky_choices", 0)
        safe: int = profile.get("safe_choices", 0)
        if total >= _MIN_CHOICES_FOR_STYLE:
            risky_ratio = risky / total
            safe_ratio = safe / total
            if risky_ratio >= _BOLD_THRESHOLD:
                parts.append(
                    "This player favours bold, risky actions — "
                    "craft an adventure that rewards daring."
                )
            elif safe_ratio >= _CAUTIOUS_THRESHOLD:
                parts.append(
                    "This player prefers cautious, thoughtful choices — "
                    "craft an adventure that rewards careful planning."
                )

        return " ".join(parts)
