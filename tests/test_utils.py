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
        # str.split() normalises multiple spaces
        assert chunks == ["word1 word2 word3"]

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
