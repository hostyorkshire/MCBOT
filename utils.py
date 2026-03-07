"""Utility helpers for the MeshCore CYOA bot."""


def chunk_message(text: str, size: int = 200) -> list[str]:
    """Split *text* into chunks that each fit within *size* characters.

    Lines separated by ``\\n`` are treated as hard breaks and are never
    merged across a chunk boundary.  Within a single line, words are never
    broken mid-word.  Keeping newlines intact ensures that a block of
    numbered choices (each on its own line) always lands in a single outbound
    chunk.

    An empty or whitespace-only input returns ``[""]`` so callers always get
    at least one sendable string.

    Args:
        text: The message text to split.
        size: Maximum character length for each chunk (default 200).

    Returns:
        A list of non-empty strings, each at most *size* characters long.
        Returns ``[""]`` when *text* is empty or whitespace-only.
    """
    if not text or not text.strip():
        return [""]

    lines = text.split("\n")
    chunks: list[str] = []
    pending: list[str] = []   # lines accumulated into the current chunk
    pending_len = 0            # char length of "\n".join(pending)

    for line in lines:
        # If a single line exceeds *size*, word-wrap it.
        if len(line) > size:
            if pending:
                chunks.append("\n".join(pending))
                pending = []
                pending_len = 0
            words = line.split()
            current = ""
            for word in words:
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
                pending = [current]
                pending_len = len(current)
            continue

        # Work out the total length if this line is appended to *pending*.
        # Adding a line to a non-empty chunk costs one extra '\n' character.
        newline_cost = 1 if pending else 0
        new_len = pending_len + newline_cost + len(line)

        if new_len > size and pending:
            # Flush the accumulated lines and start a fresh chunk.
            chunks.append("\n".join(pending))
            pending = [line]
            pending_len = len(line)
        else:
            pending.append(line)
            pending_len = new_len

    if pending:
        chunks.append("\n".join(pending))

    return chunks or [""]
