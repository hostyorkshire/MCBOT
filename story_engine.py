"""Story engine for the MeshCore CYOA bot.

Manages per-user adventure sessions and calls the Groq cloud LLM API to
generate story text and choices.  Designed to be lightweight enough to run
on a Raspberry Pi Zero 2W.

Invisible pacing / doom system
-------------------------------
Every call to :meth:`StoryEngine.advance_story` accumulates *doom* for the
active session.  When doom reaches :data:`DOOM_MAX` the story ends in a peril
finale.  Every :data:`SCENES_PER_CHAPTER` scenes (without doom triggering) the
chapter ends with an in-world cliffhanger and the user must wait
:data:`CHAPTER_COOLDOWN` seconds (24 h) before continuing.  After
:data:`MAX_CHAPTERS` chapters the story is force-ended in peril.

None of these counters are ever shown to the user.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from groq import AsyncGroq

log = logging.getLogger(__name__)

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
_CLIFFHANGER_SYSTEM: str = (
    "You are a narrator for a text-based 'Create Your Own Adventure' (CYOA) "
    "story delivered over a LoRa mesh radio network.  STRICT RULES:\n"
    "1. Keep EVERY response under 220 characters total.\n"
    "2. Write a vivid in-world cliffhanger that leaves the adventurer in "
    "nail-biting suspense.  Use an in-world reason (nightfall, a magic seal, "
    "exhaustion, etc.) to explain why they must rest and continue tomorrow.\n"
    "3. Do NOT include numbered choices — the journey resumes tomorrow.\n"
    "4. Do NOT mention doom counters, chapter numbers, or scene numbers."
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


# ---------------------------------------------------------------------------
# Pacing constants
# ---------------------------------------------------------------------------

#: Doom score at which the story ends in a peril finale.
DOOM_MAX: int = 20

#: Number of scenes per chapter before a cliffhanger is triggered.
SCENES_PER_CHAPTER: int = 4

#: Seconds the user must wait between chapters (24 hours).
CHAPTER_COOLDOWN: float = 86400.0

#: Maximum number of chapters before a forced peril finale.
MAX_CHAPTERS: int = 3

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
_HIGH_RISK_KEYWORDS: frozenset[str] = frozenset({
    "attack", "fight", "charge", "confront", "challenge", "assault",
    "rush", "ambush", "storm", "battle", "shoot", "stab", "kill",
    "steal", "grab", "snatch", "gamble", "risk", "dare", "reckless",
    "sacrifice", "detonate", "explode", "dive", "leap", "jump",
})

# Keywords that indicate a low-risk (safe) choice (worth 0 doom points).
_LOW_RISK_KEYWORDS: frozenset[str] = frozenset({
    "hide", "sneak", "run", "flee", "retreat", "avoid", "evade",
    "wait", "watch", "observe", "listen", "rest", "sleep", "heal",
    "tend", "help", "assist", "negotiate", "talk", "ask", "plead",
    "beg", "surrender", "calm", "soothe", "careful", "cautious",
})


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
            continue (``None`` means no gate is active).
        finished: ``True`` once the story has reached a peril finale.
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
        self.finished: bool = False

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

    async def start_story(
        self, user_key: str, user_name: str, genre: str = DEFAULT_GENRE
    ) -> str:
        """Begin a fresh adventure for *user_key* and return the opening text.

        A new :class:`Session` is always created, replacing any existing one.

        Args:
            user_key: Unique identifier for the user (pubkey_prefix).
            user_name: Human-readable name used in the opening prompt.
            genre: Genre ID from :data:`GENRES` (default: ``"wasteland"``).
        """
        session = Session(user_key, user_name, self._max_history)
        self._sessions[user_key] = session

        genre_info = GENRES.get(genre, GENRES[DEFAULT_GENRE])
        prompt = (
            f"Begin a new CYOA adventure for {user_name} in the "
            f"{genre_info['name']} genre ({genre_info['desc']}). "
            "Opening scene + 3 numbered choices. Under 220 chars total."
        )
        session.add_message("user", prompt)
        reply = await self._call_llm(session)
        session.add_message("assistant", reply)
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
        2. If a 24 h continuation gate is active, return an in-world wait message.
        3. Otherwise increment ``scene_in_chapter`` and accumulate doom
           (``chapter + classify_choice(choice)``).
        4. ``doom >= DOOM_MAX`` → peril finale; mark ``finished``.
        5. ``scene_in_chapter >= SCENES_PER_CHAPTER`` → chapter cliffhanger,
           advance chapter, set ``continue_after_ts``.
           If ``chapter >= MAX_CHAPTERS`` → forced peril finale instead.
        """
        session = self._sessions.get(user_key)
        if not session:
            return "No active story. Send 'start' to begin your adventure."

        # Story already finished.
        if session.finished:
            return "Your tale has ended. Send 'start' to begin a new adventure."

        # Continuation gate: enforce 24 h cooldown between chapters.
        now = time.time()
        if session.continue_after_ts is not None and now < session.continue_after_ts:
            remaining = int(session.continue_after_ts - now)
            hours, remainder = divmod(remaining, 3600)
            minutes = remainder // 60
            when = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
            return (
                f"The path is sealed by ancient magic. "
                f"Return in {when} to continue your journey."
            )

        # Gate expired – clear it before advancing.
        session.continue_after_ts = None

        choice_text = str(choice).strip()

        # Accumulate doom.
        session.scene_in_chapter += 1
        baseline_gain = session.chapter
        risk_gain = classify_choice(choice_text)
        session.doom += baseline_gain + risk_gain

        log.debug(
            "Pacing: user=%s chapter=%d scene=%d doom=%d "
            "(baseline=%d risk=%d)",
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
            reply = await self._call_llm(session, system_prompt=_PERIL_FINALE_SYSTEM)
            session.add_message("assistant", reply)
            session.finished = True
            log.info(
                "Peril finale for %s (doom=%d >= DOOM_MAX=%d)",
                user_key,
                session.doom,
                DOOM_MAX,
            )
            return reply

        # --- Chapter end ---
        if session.scene_in_chapter >= SCENES_PER_CHAPTER:
            # Hard stop: max chapters exceeded → forced finale.
            if session.chapter >= MAX_CHAPTERS:
                session.add_message("user", f"I choose: {choice_text}.")
                reply = await self._call_llm(
                    session, system_prompt=_PERIL_FINALE_SYSTEM
                )
                session.add_message("assistant", reply)
                session.finished = True
                log.info(
                    "Forced peril finale for %s (chapter=%d >= MAX_CHAPTERS=%d)",
                    user_key,
                    session.chapter,
                    MAX_CHAPTERS,
                )
                return reply

            # Normal chapter end: cliffhanger + gate.
            session.add_message("user", f"I choose: {choice_text}.")
            reply = await self._call_llm(session, system_prompt=_CLIFFHANGER_SYSTEM)
            session.add_message("assistant", reply)
            completed_chapter = session.chapter
            session.chapter += 1
            session.scene_in_chapter = 0
            session.continue_after_ts = now + CHAPTER_COOLDOWN
            log.info(
                "Chapter %d complete for %s – cliffhanger sent, "
                "gate set until %.0f",
                completed_chapter,
                user_key,
                session.continue_after_ts,
            )
            return reply

        # --- Normal scene advance ---
        session.add_message("user", f"I choose option {choice_text}.")
        reply = await self._call_llm(session)
        session.add_message("assistant", reply)
        return reply

    # ------------------------------------------------------------------
    # Internal LLM call
    # ------------------------------------------------------------------

    async def _call_llm(
        self, session: Session, *, system_prompt: str | None = None
    ) -> str:
        """Call the Groq API and return the assistant's response text.

        Args:
            session: The active :class:`Session` whose history is sent.
            system_prompt: Override the default :data:`_SYSTEM_PROMPT`.  Used
                by the pacing system to inject peril-finale or cliffhanger
                instructions without exposing them to the user.
        """
        sp = system_prompt if system_prompt is not None else _SYSTEM_PROMPT
        messages: list[dict[str, str]] = [
            {"role": "system", "content": sp}
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
