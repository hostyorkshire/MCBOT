"""Utility helpers for the MeshCore CYOA bot."""


def chunk_message(text: str, size: int = 200) -> list[str]:
    """Split *text* into chunks that each fit within *size* characters.

    Words are never broken mid-word.  An empty input returns ``[""]`` so
    callers always get at least one sendable string.

    Args:
        text: The message text to split.
        size: Maximum character length for each chunk (default 200).

    Returns:
        A list of non-empty strings, each at most *size* characters long.
        Returns ``[""]`` when *text* is empty.
    """
    if not text:
        return [""]

    words = text.split()
    chunks: list[str] = []
    current = ""

    for word in words:
        # If the single word itself exceeds size, hard-split it.
        if len(word) > size:
            if current:
                chunks.append(current)
                current = ""
            while len(word) > size:
                chunks.append(word[:size])
                word = word[size:]
            current = word
            continue

        candidate = (current + " " + word).strip() if current else word
        if len(candidate) > size:
            chunks.append(current)
            current = word
        else:
            current = candidate

    if current:
        chunks.append(current)

    return chunks or [""]
