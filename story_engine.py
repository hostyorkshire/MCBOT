"""Story engine for the MeshCore CYOA bot.

Manages per-user adventure sessions and calls the Groq cloud LLM API to
generate story text and choices.  Designed to be lightweight enough to run
on a Raspberry Pi Zero 2W.

Invisible pacing / doom system
-------------------------------
Every call to :meth:`StoryEngine.advance_story` accumulates *doom* for the
active session.  When doom reaches :data:`DOOM_MAX` the story ends in a peril
finale.  Every :data:`SCENES_PER_CHAPTER` scenes (without doom triggering) the
chapter ends with an in-world cliffhanger and the user is offered three
choices: **Continue**, **Pause**, or **End** the story.  After
:data:`MAX_CHAPTERS` chapters the story is force-ended in peril.

None of these counters are ever shown to the user.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from groq import AsyncGroq

log = logging.getLogger(__name__)

_API_ERROR_MSG = "The story pauses… (API error). Try again in a moment."


class LLMError(Exception):
    """Raised when the Groq LLM API call fails."""


# ---------------------------------------------------------------------------
# Optional dashboard story log – imported lazily so the engine starts even
# if the dashboard package is not installed.
# ---------------------------------------------------------------------------
try:
    from dashboard.active_stories import upsert_story as _log_story
except ImportError:  # pragma: no cover – optional dependency

    def _log_story(_story: dict) -> None:  # type: ignore[misc]
        """No-op fallback when the dashboard package is unavailable."""


# ---------------------------------------------------------------------------
# System prompt sent with every request.  It primes the model to produce
# short, numbered-choice output that fits within LoRa packet constraints.
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

#: System prompt used when doom reaches :data:`DOOM_MAX` or MAX_CHAPTERS is hit.
_PERIL_FINALE_SYSTEM: str = (
    "You are a narrator for a text-based 'Create Your Own Adventure' (CYOA) "
    "story delivered over a LoRa mesh radio network.  STRICT RULES:\n"
    "1. Keep EVERY response under 220 characters total.\n"
    "2. Write a vivid, dramatic scene where the adventurer faces deadly peril "
    "and meets their doom.  The ending must feel earned and final.\n"
    "3. End with '[END]' on its own line, then offer exactly these 3 options:\n"
    "1. Start over\n"
    "2. New adventure\n"
    "3. Quit\n"
    "4. Do NOT mention doom counters, chapter numbers, or scene numbers."
)

#: System prompt used when a chapter ends without doom triggering.
#: The LLM is asked for a short cliffhanger narrative only; the fixed
#: chapter-choice options are appended in code via :data:`_CHAPTER_CHOICE_SUFFIX`.
_CLIFFHANGER_SYSTEM: str = (
    "You are a narrator for a text-based 'Create Your Own Adventure' (CYOA) "
    "story delivered over a LoRa mesh radio network.  STRICT RULES:\n"
    "1. Keep EVERY response under 160 characters total.\n"
    "2. Write a vivid in-world cliffhanger that leaves the adventurer in "
    "nail-biting suspense.\n"
    "3. Do NOT include numbered choices.\n"
    "4. Do NOT mention doom counters, chapter numbers, or scene numbers."
)

#: Fixed choices appended after the cliffhanger when a chapter ends.
_CHAPTER_CHOICE_SUFFIX: str = "\n1. Continue\n2. Pause\n3. End"

#: The suffix without its leading newline, used when it heads a fresh line.
_CHAPTER_CHOICE_BODY: str = _CHAPTER_CHOICE_SUFFIX.lstrip("\n")

#: Fallback choices appended to a normal scene reply when the LLM omits them.
_STORY_FALLBACK_CHOICES: str = "\n1. Continue\n2. Try something else\n3. Explore"

#: Fallback choices appended to a finale reply when the LLM omits them.
_FINALE_FALLBACK_CHOICES: str = "\n[END]\n1. Start over\n2. New adventure\n3. Quit"

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


def _ensure_choices(reply: str, fallback: str = _STORY_FALLBACK_CHOICES) -> str:
    """Return *reply* unchanged when it already contains numbered choices.

    When the LLM omits the required ``1. / 2. / 3.`` choices block, *fallback*
    is appended so the user always has options to select from.

    Args:
        reply: Post-processed LLM reply (output of :func:`_format_reply`).
        fallback: Choice block to append when none is detected.  Defaults to
            :data:`_STORY_FALLBACK_CHOICES` for normal scenes; pass
            :data:`_FINALE_FALLBACK_CHOICES` for end-of-story replies.

    Returns:
        *reply* with a guaranteed choices block.
    """
    if re.search(r"(?:^|\n)1[.)]\s", reply):
        return reply
    log.warning("LLM reply missing choices – appending fallback: %r", reply[:60])
    return reply + fallback


# ---------------------------------------------------------------------------
# Pacing constants
# ---------------------------------------------------------------------------

#: Doom score at which the story ends in a peril finale.
DOOM_MAX: int = 500

#: Number of scenes per chapter before a chapter-choice prompt is triggered.
SCENES_PER_CHAPTER: int = 150

#: Kept for reference; no longer used to gate chapter transitions.
CHAPTER_COOLDOWN: float = 86400.0

#: Maximum number of chapters before a forced peril finale.
MAX_CHAPTERS: int = 10

# ---------------------------------------------------------------------------
# Genre registry
# ---------------------------------------------------------------------------

#: Default genre used when ``start`` is issued without an argument.
DEFAULT_GENRE: str = "wasteland"

#: Mapping of genre ID → metadata dict with ``name`` and ``desc`` keys.
GENRES: dict[str, dict[str, str]] = {
    "wasteland": {
        "name": "Post-Apocalyptic Wasteland",
        "desc": "Survival in a radioactive wasteland after civilisation collapsed.",
    },
    "cozy": {
        "name": "Cozy Village",
        "desc": "Gentle adventures in a peaceful village full of quirky characters.",
    },
    "horror": {
        "name": "Horror",
        "desc": "A terrifying tale of dread, monsters, and the unknown.",
    },
    "mil": {
        "name": "Military",
        "desc": "High-stakes special-forces missions behind enemy lines.",
    },
    "comedy": {
        "name": "Comedy",
        "desc": "A hilariously chaotic adventure where nothing goes to plan.",
    },
}

# ---------------------------------------------------------------------------
# Choice risk classifier
# ---------------------------------------------------------------------------

# Keywords that indicate a high-risk choice (worth 2 doom points).
_HIGH_RISK_KEYWORDS: frozenset[str] = frozenset(
    {
        "attack",
        "fight",
        "charge",
        "confront",
        "challenge",
        "assault",
        "rush",
        "ambush",
        "storm",
        "battle",
        "shoot",
        "stab",
        "kill",
        "steal",
        "grab",
        "snatch",
        "gamble",
        "risk",
        "dare",
        "reckless",
        "sacrifice",
        "detonate",
        "explode",
        "dive",
        "leap",
        "jump",
    }
)

# Keywords that indicate a low-risk (safe) choice (worth 0 doom points).
_LOW_RISK_KEYWORDS: frozenset[str] = frozenset(
    {
        "hide",
        "sneak",
        "run",
        "flee",
        "retreat",
        "avoid",
        "evade",
        "wait",
        "watch",
        "observe",
        "listen",
        "rest",
        "sleep",
        "heal",
        "tend",
        "help",
        "assist",
        "negotiate",
        "talk",
        "ask",
        "plead",
        "beg",
        "surrender",
        "calm",
        "soothe",
        "careful",
        "cautious",
    }
)


def classify_choice(choice_text: str) -> int:
    """Return a doom-risk score for *choice_text*.

    Scans the choice text for keywords that indicate high-risk or low-risk
    actions and returns an integer doom increment:

    * ``2`` – high-risk / aggressive action detected.
    * ``0`` – low-risk / cautious action detected.
    * ``1`` – neutral / no recognisable keywords.

    Args:
        choice_text: The raw choice string entered by the user.

    Returns:
        An integer in ``{0, 1, 2}``.
    """
    words = re.findall(r"[a-z]+", choice_text.lower())
    word_set = frozenset(words)
    if word_set & _HIGH_RISK_KEYWORDS:
        return 2
    if word_set & _LOW_RISK_KEYWORDS:
        return 0
    return 1


class Session:
    """Conversation state for a single user.

    Attributes:
        user_key: Unique identifier (pubkey_prefix hex string).
        user_name: Human-readable name used in prompts.
        history: Ordered list of ``{"role": ..., "content": ...}`` dicts.
        max_history: Maximum number of messages to retain (saves RAM).
        chapter: Current chapter number (starts at 1).
        scene_in_chapter: Number of scenes completed in the current chapter.
        doom: Accumulated doom score; triggers a peril finale at
            :data:`DOOM_MAX`.
        continue_after_ts: Epoch-seconds timestamp after which the user may
            continue (``None`` means no gate is active).  Reserved for future
            use; not set by chapter boundaries.
        awaiting_chapter_choice: ``True`` when the engine is waiting for the
            user to choose Continue (1), Pause (2), or End (3) at a chapter
            boundary.
        finished: ``True`` once the story has reached a peril finale.
        genre: Genre ID for this session (e.g. ``"wasteland"``).
        started_at: Unix timestamp when the session was created.
    """

    def __init__(self, user_key: str, user_name: str, max_history: int = 10) -> None:
        self.user_key = user_key
        self.user_name = user_name
        self.max_history = max_history
        self.history: list[dict[str, str]] = []
        # Pacing state (invisible to the user)
        self.chapter: int = 1
        self.scene_in_chapter: int = 0
        self.doom: int = 0
        self.continue_after_ts: float | None = None
        self.awaiting_chapter_choice: bool = False
        self.finished: bool = False
        # Dashboard metadata
        self.genre: str = DEFAULT_GENRE
        self.started_at: float = time.time()

    def add_message(self, role: str, content: str) -> None:
        """Append a message and prune history to *max_history* entries."""
        self.history.append({"role": role, "content": content, "ts": time.time()})
        if len(self.history) > self.max_history:
            # Always keep the first message (the story-start prompt) so the
            # model keeps context, then trim the oldest subsequent entries.
            self.history = self.history[:1] + self.history[-(self.max_history - 1) :]

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

    def get_sessions_info(self) -> list[dict]:
        """Return a snapshot of all active sessions for dashboard display.

        Each entry is a plain dict with keys suitable for JSON serialisation.
        """
        result = []
        for s in self._sessions.values():
            result.append(self._session_to_dict(s))
        return result

    def _session_to_dict(self, session: Session) -> dict:
        """Return a plain dict representation of *session* for JSON serialisation."""
        genre_info = GENRES.get(session.genre, GENRES[DEFAULT_GENRE])
        return {
            "user_key": session.user_key,
            "user_name": session.user_name,
            "genre": session.genre,
            "genre_name": genre_info["name"],
            "chapter": session.chapter,
            "scene_in_chapter": session.scene_in_chapter,
            "doom": session.doom,
            "finished": session.finished,
            "awaiting_chapter_choice": session.awaiting_chapter_choice,
            "started_at": session.started_at,
            "history": session.get_messages(),
        }

    # ------------------------------------------------------------------
    # Story actions
    # ------------------------------------------------------------------

    async def start_story(self, user_key: str, user_name: str, genre: str = DEFAULT_GENRE) -> str:
        """Begin a fresh adventure for *user_key* and return the opening text.

        A new :class:`Session` is always created, replacing any existing one.
        If a session already exists it is first persisted to the story log as a
        restarted session.

        Args:
            user_key: Unique identifier for the user (pubkey_prefix).
            user_name: Human-readable name used in the opening prompt.
            genre: Genre ID from :data:`GENRES` (default: ``"wasteland"``).
        """
        # Persist the old session before replacing it (counts as a restart).
        existing = self._sessions.get(user_key)
        if existing is not None:
            _log_story(self._session_to_dict(existing))
            log.debug(
                "_log_story called (restart): user_key=%s started_at=%s",
                user_key,
                existing.started_at,
            )
            self._sessions.pop(user_key, None)

        session = Session(user_key, user_name, self._max_history)
        session.genre = genre

        genre_info = GENRES.get(genre, GENRES[DEFAULT_GENRE])
        prompt = (
            f"Begin a new CYOA adventure for {user_name} in the "
            f"{genre_info['name']} genre ({genre_info['desc']}). "
            "Opening scene + 3 numbered choices. Under 220 chars total."
        )
        session.add_message("user", prompt)
        try:
            reply = await self._call_llm(session)
        except LLMError as exc:
            # LLM failed – discard the incomplete session so the user can
            # cleanly retry "start" without a broken session persisting.
            self._sessions.pop(user_key, None)
            return str(exc)
        reply = _ensure_choices(reply)
        session.add_message("assistant", reply)
        self._sessions[user_key] = session
        log.info(
            "Started new story for %s (%s) in genre '%s'",
            user_name,
            user_key,
            genre,
        )
        return reply

    async def advance_story(self, user_key: str, choice: Any) -> str:
        """Advance the story for *user_key* by the given *choice*.

        *choice* can be ``1``, ``2``, ``3`` (int or str), or free text.
        Returns a fallback message if no active session exists.

        Pacing control flow (all invisible to the user):

        1. If the story is already finished, return a prompt to start a new one.
        2. If ``awaiting_chapter_choice`` is set, interpret the choice as a
           chapter-boundary decision:

           * ``1`` – Continue: advance to the next chapter immediately and
             generate the opening scene of the new chapter.
           * ``2`` – Pause: leave the session open (no cooldown) and return a
             message that the player can continue anytime.
           * ``3`` – End: mark the story finished and return an end screen.
           * Any other input: re-show the chapter-choice prompt.

        3. Otherwise increment ``scene_in_chapter`` and accumulate doom
           (``chapter + classify_choice(choice)``).
        4. ``doom >= DOOM_MAX`` → peril finale; mark ``finished``.
        5. ``scene_in_chapter >= SCENES_PER_CHAPTER`` → chapter cliffhanger +
           chapter-choice prompt (1/2/3); set ``awaiting_chapter_choice``.
           If ``chapter >= MAX_CHAPTERS`` → forced peril finale instead.
        """
        session = self._sessions.get(user_key)
        if not session:
            return "No active story. Send 'start' to begin your adventure."

        # Story already finished.
        if session.finished:
            return "Your tale has ended. Send 'start' to begin a new adventure."

        choice_text = str(choice).strip()

        # ------------------------------------------------------------------
        # Chapter-boundary choice handling
        # ------------------------------------------------------------------
        if session.awaiting_chapter_choice:
            # Normalise to the first character so "1 Continue" etc. also work.
            digit = choice_text[:1]

            if digit == "1":
                # Continue – start the new chapter immediately.
                session.awaiting_chapter_choice = False
                session.add_message("user", "Continue the adventure.")
                try:
                    reply = await self._call_llm(session)
                except LLMError as exc:
                    # Roll back: undo the user message and restore the boundary flag
                    # so the user can retry cleanly.
                    session.history.pop()
                    session.awaiting_chapter_choice = True
                    return str(exc)
                reply = _ensure_choices(reply)
                session.add_message("assistant", reply)
                log.info(
                    "Chapter resumed for %s (chapter=%d)",
                    user_key,
                    session.chapter,
                )
                return reply

            if digit == "2":
                # Pause – leave session open; player can continue anytime.
                session.awaiting_chapter_choice = False
                log.info("Story paused for %s (chapter=%d)", user_key, session.chapter)
                return "Story paused. Send any choice when you're ready to continue your adventure."

            if digit == "3":
                # End – close the story.
                session.awaiting_chapter_choice = False
                session.finished = True
                _log_story(self._session_to_dict(session))
                log.info("Story ended by player %s", user_key)
                log.debug(
                    "_log_story called (player-ended): user_key=%s started_at=%s",
                    user_key,
                    session.started_at,
                )
                self._sessions.pop(user_key, None)
                return "Your adventure ends here.\n[END]\n1. Start over\n2. New adventure\n3. Quit"

            # Unrecognised input – re-show the chapter prompt.
            return f"Chapter complete! Choose:\n{_CHAPTER_CHOICE_BODY}"

        # ------------------------------------------------------------------
        # Normal story advancement
        # ------------------------------------------------------------------

        # Accumulate doom.
        session.scene_in_chapter += 1
        baseline_gain = session.chapter
        risk_gain = classify_choice(choice_text)
        session.doom += baseline_gain + risk_gain

        log.debug(
            "Pacing: user=%s chapter=%d scene=%d doom=%d (baseline=%d risk=%d)",
            user_key,
            session.chapter,
            session.scene_in_chapter,
            session.doom,
            baseline_gain,
            risk_gain,
        )

        # --- Early peril finale ---
        if session.doom >= DOOM_MAX:
            session.add_message("user", f"I choose: {choice_text}.")
            try:
                reply = await self._call_llm(session, system_prompt=_PERIL_FINALE_SYSTEM)
            except LLMError as exc:
                session.history.pop()
                return str(exc)
            reply = _ensure_choices(reply, _FINALE_FALLBACK_CHOICES)
            session.add_message("assistant", reply)
            session.finished = True
            _log_story(self._session_to_dict(session))
            log.info(
                "Peril finale for %s (doom=%d >= DOOM_MAX=%d)",
                user_key,
                session.doom,
                DOOM_MAX,
            )
            log.debug(
                "_log_story called (doom finale): user_key=%s started_at=%s",
                user_key,
                session.started_at,
            )
            self._sessions.pop(user_key, None)
            return reply

        # --- Chapter end ---
        if session.scene_in_chapter >= SCENES_PER_CHAPTER:
            # Hard stop: max chapters exceeded → forced finale.
            if session.chapter >= MAX_CHAPTERS:
                session.add_message("user", f"I choose: {choice_text}.")
                try:
                    reply = await self._call_llm(session, system_prompt=_PERIL_FINALE_SYSTEM)
                except LLMError as exc:
                    session.history.pop()
                    return str(exc)
                reply = _ensure_choices(reply, _FINALE_FALLBACK_CHOICES)
                session.add_message("assistant", reply)
                session.finished = True
                _log_story(self._session_to_dict(session))
                log.info(
                    "Forced peril finale for %s (chapter=%d >= MAX_CHAPTERS=%d)",
                    user_key,
                    session.chapter,
                    MAX_CHAPTERS,
                )
                log.debug(
                    "_log_story called (forced finale): user_key=%s started_at=%s",
                    user_key,
                    session.started_at,
                )
                self._sessions.pop(user_key, None)
                return reply

            # Normal chapter end: cliffhanger + chapter-choice prompt.
            session.add_message("user", f"I choose: {choice_text}.")
            try:
                reply = await self._call_llm(session, system_prompt=_CLIFFHANGER_SYSTEM)
            except LLMError as exc:
                session.history.pop()
                return str(exc)
            reply = f"{reply}{_CHAPTER_CHOICE_SUFFIX}"
            session.add_message("assistant", reply)
            completed_chapter = session.chapter
            session.chapter += 1
            session.scene_in_chapter = 0
            session.awaiting_chapter_choice = True
            log.info(
                "Chapter %d complete for %s – cliffhanger sent, awaiting choice",
                completed_chapter,
                user_key,
            )
            return reply

        # --- Normal scene advance ---
        session.add_message("user", f"I choose option {choice_text}.")
        try:
            reply = await self._call_llm(session)
        except LLMError as exc:
            session.history.pop()
            return str(exc)
        reply = _ensure_choices(reply)
        session.add_message("assistant", reply)
        return reply

    # ------------------------------------------------------------------
    # Internal LLM call
    # ------------------------------------------------------------------

    async def _call_llm(self, session: Session, *, system_prompt: str | None = None) -> str:
        """Call the Groq API and return the assistant's response text.

        Only ``role`` and ``content`` are forwarded to the API; any extra
        fields stored in the history (e.g. ``ts`` for the dashboard) are
        intentionally stripped to avoid an ``invalid_request_error`` from
        the Groq endpoint.

        Args:
            session: The active :class:`Session` whose history is sent.
            system_prompt: Override the default :data:`_SYSTEM_PROMPT`.  Used
                by the pacing system to inject peril-finale or cliffhanger
                instructions without exposing them to the user.
        """
        sp = system_prompt if system_prompt is not None else _SYSTEM_PROMPT
        messages: list[dict[str, str]] = [
            {"role": "system", "content": sp},
            *[{"role": m["role"], "content": m["content"]} for m in session.get_messages()],
        ]

        last_exc: Exception | None = None
        delays = [1, 2]  # seconds to wait after attempt 1 and attempt 2 failures
        for attempt in range(3):
            try:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=0.8,
                    max_tokens=self._max_tokens,
                )
                content = response.choices[0].message.content
                return _format_reply(content.strip()) if content else ""
            except Exception as exc:
                last_exc = exc
                exc_str = str(exc)
                exc_code = getattr(exc, "code", None)

                if "model_decommissioned" in exc_str or exc_code == "model_decommissioned":
                    log.error(
                        "Groq model '%s' has been decommissioned. "
                        "Update the GROQ_MODEL environment variable in your .env file "
                        "and restart the service. "
                        "See current models at https://console.groq.com/docs/deprecations",
                        self._model,
                    )
                    break  # non-retryable
                elif (
                    "invalid_api_key" in exc_str
                    or "authentication_error" in exc_str
                    or (exc_code in ("invalid_api_key", "authentication_error"))
                ):
                    log.error(
                        "Groq authentication failed. "
                        "Check that GROQ_API_KEY in your .env file is correct and active. "
                        "Get a key at https://console.groq.com"
                    )
                    break  # non-retryable
                else:
                    if attempt < 2:
                        log.warning(
                            "Groq API error (attempt %d/3): %s – retrying in %ds",
                            attempt + 1,
                            exc,
                            delays[attempt],
                        )
                        await asyncio.sleep(delays[attempt])
                    else:
                        log.error("Groq API error: %s", exc)

        raise LLMError(_API_ERROR_MSG) from last_exc
