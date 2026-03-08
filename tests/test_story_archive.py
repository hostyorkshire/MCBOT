"""Tests for dashboard.story_archive."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from dashboard.story_archive import (
    archive_story,
    get_story_by_token,
    list_archived_stories,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_story_data(**kwargs) -> dict:
    """Return a minimal story data dict suitable for archiving."""
    base = {
        "user_key": "aabb1122",
        "user_name": "Alice",
        "genre": "wasteland",
        "genre_name": "Post-Apocalyptic Wasteland",
        "started_at": 1_700_000_000.0,
        "ended_at": 1_700_001_000.0,
        "end_reason": "player_choice",
        "chapters": 2,
        "history": [
            {"role": "user", "content": "Begin adventure."},
            {"role": "assistant", "content": "You wake in rubble.\n1. Search\n2. Run\n3. Wait"},
        ],
    }
    base.update(kwargs)
    return base


@pytest.fixture()
def tmp_archive(tmp_path, monkeypatch):
    """Redirect the archive file to a temporary directory for each test."""
    archive_path = str(tmp_path / "story_archive.json")
    monkeypatch.setattr("dashboard.story_archive.ARCHIVE_FILE", archive_path)
    return archive_path


# ---------------------------------------------------------------------------
# archive_story
# ---------------------------------------------------------------------------


class TestArchiveStory:
    def test_returns_string_token(self, tmp_archive):
        token = archive_story(_make_story_data())
        assert isinstance(token, str)
        assert len(token) > 0

    def test_token_is_url_safe(self, tmp_archive):
        """Token must only contain URL-safe base64 characters."""
        import re

        token = archive_story(_make_story_data())
        assert re.fullmatch(r"[A-Za-z0-9_\-]+", token)

    def test_archive_file_created(self, tmp_archive):
        archive_story(_make_story_data())
        assert os.path.exists(tmp_archive)

    def test_archive_file_is_valid_json(self, tmp_archive):
        archive_story(_make_story_data())
        with open(tmp_archive) as fh:
            data = json.load(fh)
        assert isinstance(data, list)

    def test_archived_entry_contains_token(self, tmp_archive):
        token = archive_story(_make_story_data())
        with open(tmp_archive) as fh:
            entries = json.load(fh)
        assert any(e.get("token") == token for e in entries)

    def test_archived_entry_contains_user_name(self, tmp_archive):
        archive_story(_make_story_data(user_name="Bob"))
        with open(tmp_archive) as fh:
            entries = json.load(fh)
        assert entries[-1]["user_name"] == "Bob"

    def test_archived_entry_contains_archived_at(self, tmp_archive):
        archive_story(_make_story_data())
        with open(tmp_archive) as fh:
            entries = json.load(fh)
        assert "archived_at" in entries[-1]

    def test_multiple_stories_appended(self, tmp_archive):
        archive_story(_make_story_data(user_name="Alice"))
        archive_story(_make_story_data(user_name="Bob"))
        with open(tmp_archive) as fh:
            entries = json.load(fh)
        assert len(entries) == 2

    def test_tokens_are_unique(self, tmp_archive):
        t1 = archive_story(_make_story_data())
        t2 = archive_story(_make_story_data())
        assert t1 != t2

    def test_pruning_keeps_max_stories(self, tmp_archive):
        """When the archive exceeds MAX_ARCHIVED_STORIES, old entries are pruned."""
        with patch("dashboard.story_archive.MAX_ARCHIVED_STORIES", 3):
            for i in range(5):
                archive_story(_make_story_data(user_name=f"User{i}"))
        with open(tmp_archive) as fh:
            entries = json.load(fh)
        assert len(entries) == 3
        # Most-recent entries should be retained.
        assert entries[-1]["user_name"] == "User4"

    def test_write_error_is_logged_not_raised(self, tmp_archive, caplog):
        """If writing fails, a warning is logged and no exception propagates."""
        import logging

        with (
            patch("builtins.open", side_effect=OSError("disk full")),
            caplog.at_level(logging.WARNING, logger="dashboard.story_archive"),
        ):
            # Should not raise.
            archive_story(_make_story_data())
        assert "Could not write story archive" in caplog.text


# ---------------------------------------------------------------------------
# get_story_by_token
# ---------------------------------------------------------------------------


class TestGetStoryByToken:
    def test_returns_none_when_not_found(self, tmp_archive):
        assert get_story_by_token("nonexistent") is None

    def test_returns_story_for_valid_token(self, tmp_archive):
        token = archive_story(_make_story_data(user_name="Carol"))
        story = get_story_by_token(token)
        assert story is not None
        assert story["user_name"] == "Carol"

    def test_returns_correct_story_among_multiple(self, tmp_archive):
        archive_story(_make_story_data(user_name="Alice"))
        token = archive_story(_make_story_data(user_name="Bob"))
        archive_story(_make_story_data(user_name="Carol"))
        story = get_story_by_token(token)
        assert story["user_name"] == "Bob"

    def test_story_includes_history(self, tmp_archive):
        token = archive_story(_make_story_data())
        story = get_story_by_token(token)
        assert "history" in story
        assert len(story["history"]) == 2

    def test_returns_none_when_file_missing(self, tmp_archive):
        # File was never created – just the fixture path.
        assert get_story_by_token("any-token") is None


# ---------------------------------------------------------------------------
# list_archived_stories
# ---------------------------------------------------------------------------


class TestListArchivedStories:
    def test_returns_empty_list_when_no_archive(self, tmp_archive):
        assert list_archived_stories() == []

    def test_returns_summaries_without_history(self, tmp_archive):
        archive_story(_make_story_data())
        summaries = list_archived_stories()
        assert len(summaries) == 1
        assert "history" not in summaries[0]

    def test_returns_all_entries(self, tmp_archive):
        archive_story(_make_story_data(user_name="Alice"))
        archive_story(_make_story_data(user_name="Bob"))
        summaries = list_archived_stories()
        assert len(summaries) == 2

    def test_summary_contains_token(self, tmp_archive):
        token = archive_story(_make_story_data())
        summaries = list_archived_stories()
        assert summaries[0]["token"] == token

    def test_summary_contains_user_name(self, tmp_archive):
        archive_story(_make_story_data(user_name="Dave"))
        summaries = list_archived_stories()
        assert summaries[0]["user_name"] == "Dave"
