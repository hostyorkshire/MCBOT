"""Tests for player_memory.PlayerMemory."""

from __future__ import annotations

import os

from player_memory import (
    _MIN_CHOICES_FOR_STYLE,
    _MIN_SESSIONS_FOR_GENRE,
    PlayerMemory,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _memory(tmp_path) -> PlayerMemory:
    """Return a fresh PlayerMemory backed by a temp file."""
    return PlayerMemory(str(tmp_path / "pm.json"))


# ---------------------------------------------------------------------------
# Persistence tests
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_missing_file_creates_empty_state(self, tmp_path):
        pm = _memory(tmp_path)
        assert pm._data == {}

    def test_save_and_reload(self, tmp_path):
        pm = _memory(tmp_path)
        pm.record_session_start("u1", "horror")
        pm.save()

        pm2 = PlayerMemory(str(tmp_path / "pm.json"))
        assert pm2._data["u1"]["sessions_started"] == 1

    def test_corrupt_file_starts_fresh(self, tmp_path):
        p = tmp_path / "pm.json"
        p.write_text("not valid json")
        pm = PlayerMemory(str(p))
        assert pm._data == {}

    def test_save_is_atomic(self, tmp_path):
        """A tmp file should not be left behind after a successful save."""
        pm = _memory(tmp_path)
        pm.record_session_start("u1", "cozy")
        pm.save()
        tmp_files = [f for f in os.listdir(tmp_path) if f.startswith(".player_memory_")]
        assert tmp_files == []


# ---------------------------------------------------------------------------
# Profile access
# ---------------------------------------------------------------------------


class TestProfile:
    def test_get_profile_unknown_user_returns_blank(self, tmp_path):
        pm = _memory(tmp_path)
        profile = pm.get_profile("nobody")
        assert profile["sessions_started"] == 0
        assert profile["total_choices"] == 0

    def test_get_profile_returns_copy(self, tmp_path):
        pm = _memory(tmp_path)
        pm.record_session_start("u1", "wasteland")
        profile = pm.get_profile("u1")
        profile["sessions_started"] = 999
        assert pm._data["u1"]["sessions_started"] == 1  # original unchanged


# ---------------------------------------------------------------------------
# Recording tests
# ---------------------------------------------------------------------------


class TestRecording:
    def test_record_session_start_increments_counter(self, tmp_path):
        pm = _memory(tmp_path)
        pm.record_session_start("u1", "horror")
        pm.record_session_start("u1", "horror")
        assert pm._data["u1"]["sessions_started"] == 2

    def test_record_session_start_tracks_genre(self, tmp_path):
        pm = _memory(tmp_path)
        pm.record_session_start("u1", "horror")
        pm.record_session_start("u1", "horror")
        pm.record_session_start("u1", "cozy")
        assert pm._data["u1"]["genre_counts"]["horror"] == 2
        assert pm._data["u1"]["genre_counts"]["cozy"] == 1

    def test_record_choice_risky(self, tmp_path):
        pm = _memory(tmp_path)
        pm.record_choice("u1", 2)
        assert pm._data["u1"]["risky_choices"] == 1
        assert pm._data["u1"]["total_choices"] == 1

    def test_record_choice_safe(self, tmp_path):
        pm = _memory(tmp_path)
        pm.record_choice("u1", 0)
        assert pm._data["u1"]["safe_choices"] == 1
        assert pm._data["u1"]["total_choices"] == 1

    def test_record_choice_neutral(self, tmp_path):
        pm = _memory(tmp_path)
        pm.record_choice("u1", 1)
        assert pm._data["u1"]["risky_choices"] == 0
        assert pm._data["u1"]["safe_choices"] == 0
        assert pm._data["u1"]["total_choices"] == 1

    def test_record_session_end_completed(self, tmp_path):
        pm = _memory(tmp_path)
        pm.record_session_start("u1", "mil")
        pm.record_session_end("u1", completed=True)
        assert pm._data["u1"]["sessions_completed"] == 1

    def test_record_session_end_abandoned(self, tmp_path):
        pm = _memory(tmp_path)
        pm.record_session_start("u1", "mil")
        pm.record_session_end("u1", completed=False)
        assert pm._data["u1"]["sessions_completed"] == 0

    def test_multiple_users_isolated(self, tmp_path):
        pm = _memory(tmp_path)
        pm.record_session_start("u1", "horror")
        pm.record_session_start("u2", "cozy")
        assert pm._data["u1"]["genre_counts"] == {"horror": 1}
        assert pm._data["u2"]["genre_counts"] == {"cozy": 1}


# ---------------------------------------------------------------------------
# Personalisation hint tests
# ---------------------------------------------------------------------------


class TestPersonalizationHint:
    def test_unknown_user_returns_empty(self, tmp_path):
        pm = _memory(tmp_path)
        assert pm.get_personalization_hint("ghost") == ""

    def test_insufficient_sessions_no_genre_hint(self, tmp_path):
        pm = _memory(tmp_path)
        # Only 1 session – below _MIN_SESSIONS_FOR_GENRE (2).
        pm.record_session_start("u1", "horror")
        hint = pm.get_personalization_hint("u1")
        assert "horror" not in hint

    def test_sufficient_sessions_genre_hint(self, tmp_path):
        pm = _memory(tmp_path)
        for _ in range(_MIN_SESSIONS_FOR_GENRE):
            pm.record_session_start("u1", "horror")
        hint = pm.get_personalization_hint("u1")
        assert "horror" in hint

    def test_favourite_genre_is_most_played(self, tmp_path):
        pm = _memory(tmp_path)
        pm.record_session_start("u1", "cozy")
        for _ in range(3):
            pm.record_session_start("u1", "horror")
        hint = pm.get_personalization_hint("u1")
        assert "horror" in hint
        assert "cozy" not in hint

    def test_insufficient_choices_no_style_hint(self, tmp_path):
        pm = _memory(tmp_path)
        for _ in range(_MIN_CHOICES_FOR_STYLE - 1):
            pm.record_choice("u1", 2)
        hint = pm.get_personalization_hint("u1")
        assert "bold" not in hint
        assert "cautious" not in hint

    def test_bold_style_hint(self, tmp_path):
        pm = _memory(tmp_path)
        # All risky choices → ratio == 1.0, above _BOLD_THRESHOLD.
        for _ in range(_MIN_CHOICES_FOR_STYLE):
            pm.record_choice("u1", 2)
        hint = pm.get_personalization_hint("u1")
        assert "bold" in hint.lower() or "daring" in hint.lower()

    def test_cautious_style_hint(self, tmp_path):
        pm = _memory(tmp_path)
        # All safe choices → ratio == 1.0, above _CAUTIOUS_THRESHOLD.
        for _ in range(_MIN_CHOICES_FOR_STYLE):
            pm.record_choice("u1", 0)
        hint = pm.get_personalization_hint("u1")
        assert "cautious" in hint.lower() or "careful" in hint.lower()

    def test_neutral_style_no_hint(self, tmp_path):
        pm = _memory(tmp_path)
        # All neutral choices → neither bold nor cautious.
        for _ in range(_MIN_CHOICES_FOR_STYLE):
            pm.record_choice("u1", 1)
        hint = pm.get_personalization_hint("u1")
        # No style hint – though there might be a genre hint if sessions were recorded.
        assert "bold" not in hint.lower()
        assert "cautious" not in hint.lower()

    def test_hint_combines_genre_and_style(self, tmp_path):
        pm = _memory(tmp_path)
        for _ in range(_MIN_SESSIONS_FOR_GENRE):
            pm.record_session_start("u1", "mil")
        for _ in range(_MIN_CHOICES_FOR_STYLE):
            pm.record_choice("u1", 2)
        hint = pm.get_personalization_hint("u1")
        assert "mil" in hint
        assert "bold" in hint.lower() or "daring" in hint.lower()
