"""Story engine for the MeshCore CYOA bot.

Manages per-user adventure sessions and calls the Groq cloud LLM API to
generate story text and choices.  Designed to be lightweight enough to run
on a Raspberry Pi Zero 2W.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from groq import AsyncGroq

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt sent with every request.  It primes the model to produce
# short, numbered-choice output that fits within LoRa packet constraints.
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = (
    "You are a narrator for a text-based 'Create Your Own Adventure' (CYOA) "
    "story delivered over a LoRa mesh radio network.  STRICT RULES:\n"
    "1. Keep EVERY response under 220 characters total (including choices).\n"
    "2. End EVERY response with exactly 3 numbered choices, each on its own "
    "line, using the format '1.' '2.' '3.' — for example:\n"
    "1. Go left\n"
    "2. Hide\n"
    "3. Call out\n"
    "3. Use vivid but very short sentences.  No filler text.\n"
    "4. If the story reaches a definitive end, write [END] and offer:\n"
    "1. Restart\n"
    "2. New adventure\n"
    "3. Quit"
)

# ---------------------------------------------------------------------------
# Regex helpers for normalising LLM choice output.
# ---------------------------------------------------------------------------

# Matches three inline choices: "1) foo  2) bar  3) baz" or "1. foo  2. bar  3. baz"
# Uses [^\n]+ to avoid accidentally matching choices already on separate lines.
_INLINE_CHOICES_RE = re.compile(
    r"1\s*[).]\s*(?P<c1>[^\n]+?)\s*(?=2\s*[).])"
    r"2\s*[).]\s*(?P<c2>[^\n]+?)\s*(?=3\s*[).])"
    r"3\s*[).]\s*(?P<c3>[^\n]+?)\s*$",
    re.MULTILINE,
)


def _format_reply(text: str) -> str:
    """Normalise an LLM reply so choices are on separate lines in ``N.`` format.

    Handles three common LLM output patterns:

    * Already-correct multiline ``\\n1. foo\\n2. bar\\n3. baz`` – returned unchanged.
    * Multiline with parentheses ``\\n1) foo\\n2) bar\\n3) baz`` – ``)`` → ``.``.
    * Inline ``1) foo  2) bar  3) baz`` or ``1. foo  2. bar  3. baz`` – split into
      separate lines.

    Args:
        text: Raw LLM reply string.

    Returns:
        Reply with choices on separate ``N.`` lines.
    """
    # Already on separate lines with '.' format for all three choices – no change needed.
    if re.search(r"\n1\. .+\n2\. .+\n3\. ", text):
        return text

    # Separate lines with ')' format – convert ')' to '.'.
    if "\n1) " in text:
        return re.sub(r"(?m)^([1-3])\) ", r"\1. ", text)

    # Inline format – reformat into separate lines.
    m = _INLINE_CHOICES_RE.search(text)
    if m:
        narrative = text[: m.start()].rstrip()
        c1 = m.group("c1").strip()
        c2 = m.group("c2").strip()
        c3 = m.group("c3").strip()
        prefix = narrative + "\n" if narrative else ""
        return f"{prefix}1. {c1}\n2. {c2}\n3. {c3}"

    return text


class Session:
    """Conversation state for a single user.

    Attributes:
        user_key: Unique identifier (pubkey_prefix hex string).
        user_name: Human-readable name used in prompts.
        history: Ordered list of ``{"role": ..., "content": ...}`` dicts.
        max_history: Maximum number of messages to retain (saves RAM).
    """

    def __init__(self, user_key: str, user_name: str, max_history: int = 10) -> None:
        self.user_key = user_key
        self.user_name = user_name
        self.max_history = max_history
        self.history: list[dict[str, str]] = []

    def add_message(self, role: str, content: str) -> None:
        """Append a message and prune history to *max_history* entries."""
        self.history.append({"role": role, "content": content})
        if len(self.history) > self.max_history:
            # Always keep the first message (the story-start prompt) so the
            # model keeps context, then trim the oldest subsequent entries.
            self.history = self.history[:1] + self.history[-(self.max_history - 1):]

    def get_messages(self) -> list[dict[str, str]]:
        """Return a shallow copy of the message history."""
        return list(self.history)


class StoryEngine:
    """Generates CYOA story content using the Groq LLM API.

    Args:
        api_key: Groq API key.
        model: Groq model name (default ``"llama3-8b-8192"``).
        max_history: Maximum conversation turns to keep per session.
        max_tokens: Maximum tokens for each LLM response.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "llama-3.1-8b-instant",
        max_history: int = 10,
        max_tokens: int = 120,
    ) -> None:
        self._client = AsyncGroq(api_key=api_key)
        self._model = model
        self._max_history = max_history
        self._max_tokens = max_tokens
        self._sessions: dict[str, Session] = {}

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------

    def has_session(self, user_key: str) -> bool:
        """Return ``True`` if an active session exists for *user_key*."""
        return user_key in self._sessions

    def clear_session(self, user_key: str) -> None:
        """Delete the session for *user_key*, if any."""
        self._sessions.pop(user_key, None)
        log.info("Cleared session for %s", user_key)

    # ------------------------------------------------------------------
    # Story actions
    # ------------------------------------------------------------------

    async def start_story(self, user_key: str, user_name: str) -> str:
        """Begin a fresh adventure for *user_key* and return the opening text.

        A new :class:`Session` is always created, replacing any existing one.
        """
        session = Session(user_key, user_name, self._max_history)
        self._sessions[user_key] = session

        prompt = (
            f"Begin a new CYOA adventure for {user_name}. "
            "Opening scene + 3 numbered choices. Under 220 chars total."
        )
        session.add_message("user", prompt)
        reply = await self._call_llm(session)
        session.add_message("assistant", reply)
        log.info("Started new story for %s (%s)", user_name, user_key)
        return reply

    async def advance_story(self, user_key: str, choice: Any) -> str:
        """Advance the story for *user_key* by the given *choice*.

        *choice* can be ``1``, ``2``, ``3`` (int or str), or free text.
        Returns a fallback message if no active session exists.
        """
        session = self._sessions.get(user_key)
        if not session:
            return "No active story. Send 'start' to begin your adventure."

        choice_text = str(choice).strip()
        session.add_message("user", f"I choose option {choice_text}.")
        reply = await self._call_llm(session)
        session.add_message("assistant", reply)
        return reply

    # ------------------------------------------------------------------
    # Internal LLM call
    # ------------------------------------------------------------------

    async def _call_llm(self, session: Session) -> str:
        """Call the Groq API and return the assistant's response text."""
        messages: list[dict[str, str]] = [
            {"role": "system", "content": _SYSTEM_PROMPT}
        ] + session.get_messages()

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,  # type: ignore[arg-type]
                temperature=0.8,
                max_tokens=self._max_tokens,
            )
            content = response.choices[0].message.content
            return _format_reply(content.strip()) if content else ""
        except Exception as exc:  # noqa: BLE001
            exc_str = str(exc)
            if "model_decommissioned" in exc_str or (
                hasattr(exc, "code") and getattr(exc, "code", None) == "model_decommissioned"
            ):
                log.error(
                    "Groq model '%s' has been decommissioned. "
                    "Update the GROQ_MODEL environment variable in your .env file "
                    "and restart the service. "
                    "See current models at https://console.groq.com/docs/deprecations",
                    self._model,
                )
            else:
                log.error("Groq API error: %s", exc)
            return "The story pauses… (API error). Try again in a moment."
