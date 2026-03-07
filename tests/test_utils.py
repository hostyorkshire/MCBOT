"""Tests for utils.chunk_message."""

from utils import chunk_message


class TestChunkMessageBasic:
    def test_short_text_returned_as_single_chunk(self):
        result = chunk_message("Hello world", 200)
        assert result == ["Hello world"]

    def test_empty_string_returns_list_with_empty_string(self):
        result = chunk_message("", 200)
        assert result == [""]

    def test_exact_fit_is_not_split(self):
        text = "a" * 200
        result = chunk_message(text, 200)
        assert result == [text]

    def test_single_word_over_limit_is_hard_split(self):
        word = "x" * 50
        result = chunk_message(word, 20)
        assert all(len(c) <= 20 for c in result)
        assert "".join(result) == word

    def test_whitespace_only_string(self):
        # split() on whitespace-only gives [], so we expect [""]
        result = chunk_message("   ", 200)
        assert result == [""]


class TestChunkMessageSplitting:
    def test_long_text_is_split_into_multiple_chunks(self):
        # 10 words of 5 chars each + space = ~60 chars total, limit = 20
        text = " ".join(["hello"] * 10)  # 54 chars
        chunks = chunk_message(text, 20)
        assert len(chunks) > 1

    def test_all_chunks_within_size_limit(self):
        text = " ".join([f"word{i}" for i in range(100)])
        size = 30
        chunks = chunk_message(text, size)
        for chunk in chunks:
            assert len(chunk) <= size, f"Chunk too long: {chunk!r}"

    def test_chunks_reassemble_to_original(self):
        text = "The quick brown fox jumps over the lazy dog"
        chunks = chunk_message(text, 15)
        reassembled = " ".join(chunks)
        assert reassembled == text

    def test_size_one_splits_every_character(self):
        # Each word becomes its own chunk(s)
        text = "ab cd"
        chunks = chunk_message(text, 1)
        assert all(len(c) <= 1 for c in chunks)

    def test_multiple_spaces_between_words(self):
        text = "word1  word2   word3"
        chunks = chunk_message(text, 200)
        # The new line-aware implementation preserves spaces within a line.
        assert chunks == ["word1  word2   word3"]

    def test_chunk_count_grows_with_smaller_size(self):
        text = " ".join(["hello"] * 20)
        chunks_large = chunk_message(text, 100)
        chunks_small = chunk_message(text, 20)
        assert len(chunks_small) >= len(chunks_large)


class TestChunkMessageEdgeCases:
    def test_single_word_exactly_at_limit(self):
        word = "a" * 10
        result = chunk_message(word, 10)
        assert result == [word]

    def test_two_words_that_fit_together(self):
        result = chunk_message("hello world", 11)
        assert result == ["hello world"]

    def test_two_words_that_do_not_fit_together(self):
        result = chunk_message("hello world", 10)
        assert result == ["hello", "world"]

    def test_long_word_followed_by_short_word(self):
        long_word = "a" * 25
        text = f"{long_word} hi"
        chunks = chunk_message(text, 20)
        # Verify all characters from the original are present in the chunks
        assert "".join(chunks).replace(" ", "") == text.replace(" ", "")


class TestChunkMessageNewlineAware:
    def test_multiline_text_preserves_newlines_in_chunk(self):
        text = "Story.\n1. Go left\n2. Hide\n3. Run"
        chunks = chunk_message(text, 200)
        assert len(chunks) == 1
        assert "\n1. Go left" in chunks[0]
        assert "\n2. Hide" in chunks[0]
        assert "\n3. Run" in chunks[0]

    def test_choices_in_same_chunk_when_narrative_fits(self):
        # 170-char narrative + ~40-char choices block = under 200 → one chunk
        narrative = "A" * 150
        text = f"{narrative}\n1. Go left\n2. Hide\n3. Run"
        chunks = chunk_message(text, 200)
        choices_chunk = chunks[-1]
        assert "1. Go left" in choices_chunk
        assert "2. Hide" in choices_chunk
        assert "3. Run" in choices_chunk

    def test_choices_stay_together_when_narrative_overflows(self):
        # 195-char narrative forces a split; choices must be in the SAME chunk.
        narrative = "B" * 195
        text = f"{narrative}\n1. Option A\n2. Option B\n3. Option C"
        chunks = chunk_message(text, 200)
        assert len(chunks) == 2
        choices_chunk = chunks[-1]
        assert "1. Option A" in choices_chunk
        assert "2. Option B" in choices_chunk
        assert "3. Option C" in choices_chunk

    def test_newline_in_single_line_input_unchanged(self):
        # Single-line text with no newlines still works as before.
        result = chunk_message("hello world", 200)
        assert result == ["hello world"]

    def test_explicit_newline_creates_hard_break(self):
        # A newline in the source must NOT be swallowed – each line is distinct.
        text = "line one\nline two"
        chunks = chunk_message(text, 200)
        assert len(chunks) == 1
        assert chunks[0] == "line one\nline two"

    def test_newline_forces_new_chunk_when_lines_overflow(self):
        # First line fills chunk; second line must start a new chunk.
        line1 = "x" * 195
        line2 = "short"
        text = f"{line1}\n{line2}"
        chunks = chunk_message(text, 200)
        assert len(chunks) == 2
        assert chunks[0] == line1
        assert chunks[1] == line2

    def test_all_chunks_within_size_with_newlines(self):
        lines = [f"line {i} with some padding text" for i in range(20)]
        text = "\n".join(lines)
        for chunk in chunk_message(text, 50):
            assert len(chunk) <= 50
