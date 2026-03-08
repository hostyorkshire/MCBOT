"""Tests for dashboard.story_archive."""

from __future__ import annotations

import re

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
    """Redirect the archive database to a temporary directory for each test."""
    db_path = str(tmp_path / "stories.db")
    monkeypatch.setattr("dashboard.story_archive.ARCHIVE_DB_PATH", db_path)
    return db_path


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
        token = archive_story(_make_story_data())
        assert re.fullmatch(r"[A-Za-z0-9_\-]+", token)

    def test_database_file_created(self, tmp_archive):
        import os

        archive_story(_make_story_data())
        assert os.path.exists(tmp_archive)

    def test_archived_entry_retrievable(self, tmp_archive):
        """A story archived via archive_story can be fetched by its token."""
        token = archive_story(_make_story_data(user_name="Bob"))
        story = get_story_by_token(token)
        assert story is not None
        assert story["user_name"] == "Bob"

    def test_archived_entry_contains_archived_at(self, tmp_archive):
        token = archive_story(_make_story_data())
        story = get_story_by_token(token)
        assert story is not None
        assert "archived_at" in story
        assert story["archived_at"] > 0

    def test_multiple_stories_stored(self, tmp_archive):
        archive_story(_make_story_data(user_name="Alice"))
        archive_story(_make_story_data(user_name="Bob"))
        summaries = list_archived_stories()
        assert len(summaries) == 2

    def test_tokens_are_unique(self, tmp_archive):
        t1 = archive_story(_make_story_data())
        t2 = archive_story(_make_story_data())
        assert t1 != t2

    def test_many_stories_all_stored(self, tmp_archive):
        """SQLite stores every entry; there is no hard pruning cap."""
        for i in range(20):
            archive_story(_make_story_data(user_name=f"User{i}"))
        assert len(list_archived_stories()) == 20


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

    def test_history_is_list_of_dicts(self, tmp_archive):
        token = archive_story(_make_story_data())
        story = get_story_by_token(token)
        assert isinstance(story["history"], list)
        assert all(isinstance(m, dict) for m in story["history"])

    def test_returns_none_when_db_missing(self, tmp_archive):
        # DB file was never created – the connection initialises it, so we
        # expect an empty result rather than an exception.
        assert get_story_by_token("any-token") is None

    def test_story_includes_end_reason(self, tmp_archive):
        token = archive_story(_make_story_data(end_reason="doom"))
        story = get_story_by_token(token)
        assert story["end_reason"] == "doom"

    def test_story_includes_chapters(self, tmp_archive):
        token = archive_story(_make_story_data(chapters=5))
        story = get_story_by_token(token)
        assert story["chapters"] == 5


# ---------------------------------------------------------------------------
# list_archived_stories
# ---------------------------------------------------------------------------


class TestListArchivedStories:
    def test_returns_empty_list_when_no_stories(self, tmp_archive):
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

    def test_ordered_oldest_first(self, tmp_archive):
        """list_archived_stories returns entries in insertion order."""
        archive_story(_make_story_data(user_name="First"))
        archive_story(_make_story_data(user_name="Second"))
        summaries = list_archived_stories()
        assert summaries[0]["user_name"] == "First"
        assert summaries[1]["user_name"] == "Second"
