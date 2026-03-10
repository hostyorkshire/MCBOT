"""MCBOT local dashboard – Flask application.

Run with::

    cd <repo-root>
    python -m dashboard.app

or use the provided helper script::

    bash dashboard/start-dashboard.sh

The dashboard uses **eventlet** as the WSGI server (via Flask-SocketIO's
``async_mode="eventlet"``).  Eventlet must be installed (it is listed in
``dashboard/requirements.txt``) before starting the dashboard.  The
``python -m dashboard.app`` entry point invokes ``socketio.run()``, which
automatically uses eventlet's built-in WSGI server – no separate gunicorn or
uWSGI process is required.

.. warning::
    **Do NOT use** ``flask run`` to start the dashboard.  Flask's built-in
    Werkzeug server does not support the Socket.IO transport required for
    real-time live updates.  Always start the dashboard via
    ``python -m dashboard.app`` or ``bash dashboard/start-dashboard.sh``.

The dashboard is served at ``/dashboard/`` and exposes two JSON API endpoints:

* ``/dashboard/api/status``  – bot status (running/idle/offline, uptime, errors)
* ``/dashboard/api/stories`` – last 20 story sessions (active, finished, or restarted)

Real-time updates are delivered via Socket.IO.  Whenever ``bot_state.json``
changes on disk a ``story_update`` event is broadcast to all connected clients
so the page refreshes instantly without polling.  The client-side polling loop
is kept as a graceful fallback for environments that do not support WebSockets.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import uuid
from collections import deque

# ---------------------------------------------------------------------------
# Guard: refuse to start when invoked via `flask run`.
# The Flask/Werkzeug development server does not support the Socket.IO
# transport, so real-time updates will silently break.  Always use
# `python -m dashboard.app` or `bash dashboard/start-dashboard.sh` instead.
# ---------------------------------------------------------------------------
_argv0 = os.path.basename(sys.argv[0]) if sys.argv else ""
if _argv0 in ("flask", "flask.exe") and "run" in sys.argv[1:]:
    sys.exit(
        "\n"
        "ERROR: Do not start the MCBOT dashboard with 'flask run'.\n"
        "\n"
        "  Flask's Werkzeug server does not support Socket.IO, so real-time\n"
        "  live updates will NOT work.\n"
        "\n"
        "  Use one of these instead:\n"
        "    python -m dashboard.app\n"
        "    bash dashboard/start-dashboard.sh\n"
    )

from flask import Blueprint, Flask, jsonify, render_template, request  # noqa: E402
from flask_socketio import SocketIO  # noqa: E402

import time as _time  # noqa: E402

from dashboard.active_stories import STORIES_FILE as ACTIVE_STORIES_FILE  # noqa: E402
from dashboard.active_stories import load_stories  # noqa: E402
from dashboard.active_stories import upsert_story as _upsert_story  # noqa: E402
from dashboard.state import STATE_FILE, get_session, get_sessions, get_status  # noqa: E402

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory web-chat session store
# ---------------------------------------------------------------------------
# Maps a client-supplied user_id (UUID string) to a deque of conversation
# turns: [{"role": "user"|"assistant", "content": str}, ...].
# Sessions are ephemeral – they reset when the dashboard process restarts.
_chat_lock: threading.Lock = threading.Lock()
_chat_sessions: dict[str, deque] = {}
_CHAT_MAX_TURNS: int = 20  # maximum conversation turns retained per session
_CHAT_MAX_SESSIONS: int = 500  # maximum concurrent sessions in memory

_CHAT_SYSTEM_PROMPT: str = (
    "You are StoryBoT, a friendly narrator for a text-based 'Create Your Own "
    "Adventure' (CYOA) story.  Guide the user through an engaging adventure. "
    "Keep every response under 400 characters.  When the story is progressing "
    "end each response with exactly 3 numbered choices on separate lines using "
    "the format '1. …', '2. …', '3. …'.  Use vivid but concise sentences."
)


def _get_history(user_id: str) -> list[dict]:
    """Return a snapshot of the conversation history for *user_id*."""
    with _chat_lock:
        return list(_chat_sessions.get(user_id, []))


def _append_history(user_id: str, role: str, content: str) -> None:
    """Append a turn to *user_id*'s conversation history.

    When the number of stored sessions reaches :data:`_CHAT_MAX_SESSIONS` the
    oldest session is evicted to keep memory usage bounded.
    """
    with _chat_lock:
        if user_id not in _chat_sessions:
            # Evict the oldest session if the cap is reached.
            if len(_chat_sessions) >= _CHAT_MAX_SESSIONS:
                oldest_key = next(iter(_chat_sessions))
                del _chat_sessions[oldest_key]
            _chat_sessions[user_id] = deque(maxlen=_CHAT_MAX_TURNS)
            _upsert_story({
                "user_key": user_id,
                "user_name": "Web User",
                "genre": "web",
                "genre_name": "Web Chat",
                "chapter": 1,
                "scene_in_chapter": 0,
                "doom": 0,
                "finished": False,
                "awaiting_chapter_choice": False,
                "started_at": _time.time(),
                "source": "web",
            })
        _chat_sessions[user_id].append({"role": role, "content": content})


# ---------------------------------------------------------------------------
# Blueprint – all routes live under /dashboard/
# ---------------------------------------------------------------------------
bp = Blueprint(
    "dashboard",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/dashboard/static",
)


@bp.route("/")
def index():
    """Render the main dashboard page."""
    return render_template("index.html")


@bp.route("/api/status")
def api_status():
    """Return bot status as JSON."""
    return jsonify(get_status())


def _merge_stories() -> list[dict]:
    """Merge persisted and active session data, newest-first (max 20).

    Persisted entries from ``active_stories.json`` are loaded first.  Active
    sessions from ``bot_state.json`` are layered on top **unless** the active
    session is *not* finished while the persisted copy *is* finished.  In that
    case the persisted (finished) entry wins — this prevents a stale
    ``bot_state.json`` snapshot (written every ~5 s) from reverting a story
    that has already been logged as finished.
    """
    merged: dict[str, dict] = {s["user_key"]: s for s in load_stories() if s.get("user_key")}
    for session in get_sessions():
        uk = session.get("user_key")
        if not uk:
            continue
        # Never let a stale unfinished session overwrite a finished record.
        if not session.get("finished") and uk in merged and merged[uk].get("finished"):
            continue
        merged[uk] = session
    stories = sorted(merged.values(), key=lambda s: s.get("started_at", 0), reverse=True)
    return stories[:20]


@bp.route("/api/stories")
def api_stories():
    """Return last 20 story sessions (active, finished, or restarted) as JSON."""
    return jsonify(_merge_stories())


@bp.route("/story/<user_key>")
def story_live(user_key: str):
    """Render the live detail page for a single story session."""
    session = get_session(user_key)
    if session is None:
        # Fall back to the persisted story log for finished/restarted sessions.
        for s in load_stories():
            if s.get("user_key") == user_key:
                session = s
                break
    if session is None:
        return render_template("story_not_found.html", user_key=user_key), 404
    return render_template("story_live.html", session=session)


# ---------------------------------------------------------------------------
# /chat  – public endpoint for the website chat page
# ---------------------------------------------------------------------------
# Exposed at the Flask application root (not under /dashboard/) so that the
# Cloudflare Tunnel URL can be used directly as https://bot.example.com/chat.
# CORS headers are added to every response so that the cPanel-hosted
# website/chat.html can call this endpoint cross-origin.

chat_bp = Blueprint("chat", __name__)


def _cors(response):
    """Attach CORS headers required for cross-origin website access.

    The allowed origin is read from the ``CHAT_CORS_ORIGIN`` environment
    variable so that production deployments can restrict access to the
    cPanel website domain.  Defaults to ``*`` (any origin) which is
    acceptable for a local development setup.

    Example (restrict to your website domain)::

        export CHAT_CORS_ORIGIN="https://www.yourdomain.com"
    """
    origin = os.getenv("CHAT_CORS_ORIGIN", "*")
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@chat_bp.route("/chat", methods=["POST", "OPTIONS"])
def web_chat():
    """Accept a chat message from a web user and return the bot's reply.

    Request body (JSON)::

        { "message": "...", "user_id": "<uuid>" }

    Response body (JSON)::

        { "reply": "..." }

    The *user_id* is generated client-side and stored in the browser's
    ``localStorage`` so that conversation history is preserved across page
    reloads within the same session.  No username handling is required on
    the server – the chat.html page manages display names entirely in the
    browser.
    """
    # Handle CORS pre-flight.
    if request.method == "OPTIONS":
        return _cors(jsonify({}))

    data = request.get_json(force=True, silent=True) or {}
    raw_message = data.get("message", "")
    raw_user_id = data.get("user_id", "")

    # Sanitise inputs.
    message = str(raw_message).strip()[:500]
    # Accept only well-formed UUID strings from the client; fall back to a
    # random UUID to prevent injection via the user_id key.
    try:
        user_id = str(uuid.UUID(str(raw_user_id)))
    except (ValueError, AttributeError):
        user_id = str(uuid.uuid4())

    if not message:
        return _cors(jsonify({"error": "message is required"})), 400

    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        _log.error("web_chat: GROQ_API_KEY is not set")
        return _cors(jsonify({"error": "Bot is not configured"})), 503

    try:
        from groq import AuthenticationError, Groq, RateLimitError

        history = _get_history(user_id)
        messages = [{"role": "system", "content": _CHAT_SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": message})

        model = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
        completion = Groq(api_key=api_key).chat.completions.create(
            model=model,
            messages=messages,
        )
        reply = completion.choices[0].message.content or ""
    except AuthenticationError:
        _log.error("web_chat: Groq authentication failed – check GROQ_API_KEY")
        return _cors(jsonify({"error": "Bot API key is invalid"})), 503
    except RateLimitError:
        _log.warning("web_chat: Groq rate limit exceeded")
        return _cors(jsonify({"error": "Bot is busy, please try again shortly"})), 429
    except Exception as exc:
        _log.error("web_chat: Groq API error: %s", exc)
        return _cors(jsonify({"error": "Bot is unavailable, please try again"})), 503

    # Persist this turn so that subsequent requests maintain context.
    _append_history(user_id, "user", message)
    _append_history(user_id, "assistant", reply)

    return _cors(jsonify({"reply": reply}))


# ---------------------------------------------------------------------------
# Socket.IO instance (initialised properly inside create_app)
# ---------------------------------------------------------------------------

socketio = SocketIO()


def _state_watcher(app: Flask) -> None:
    """Background thread: watch bot_state.json and active_stories.json for changes.

    Runs inside the application context so that Flask-SocketIO helpers are
    accessible.  The thread is started by :func:`create_app` via
    ``socketio.start_background_task``.

    A ``story_update`` event is emitted whenever either file's mtime changes,
    so the dashboard refreshes instantly when a story finishes (written to
    ``active_stories.json``) as well as when the live session state changes
    (written to ``bot_state.json``).
    """
    poll_interval = 1.0  # seconds between file-stat checks

    # Seed both mtimes so the first loop iteration does not emit a spurious
    # event before any real change has occurred.
    try:
        last_mtime_state = os.path.getmtime(STATE_FILE)
    except OSError:
        last_mtime_state = None

    try:
        last_mtime_stories = os.path.getmtime(ACTIVE_STORIES_FILE)
    except OSError:
        last_mtime_stories = None

    with app.app_context():
        while True:
            try:
                mtime_state = os.path.getmtime(STATE_FILE)
            except OSError:
                mtime_state = None

            try:
                mtime_stories = os.path.getmtime(ACTIVE_STORIES_FILE)
            except OSError:
                mtime_stories = None

            if mtime_state != last_mtime_state or mtime_stories != last_mtime_stories:
                last_mtime_state = mtime_state
                last_mtime_stories = mtime_stories
                payload = {
                    "status": get_status(),
                    "stories": _merge_stories(),
                }
                socketio.emit("story_update", payload)

            socketio.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__, instance_relative_config=False)
    app.secret_key = os.urandom(24)
    app.register_blueprint(bp, url_prefix="/dashboard")
    app.register_blueprint(chat_bp)  # /chat at the application root

    # async_mode="eventlet" tells Flask-SocketIO to use the eventlet WSGI server,
    # which is required for reliable operation under systemd/production environments.
    # eventlet must be installed (listed in dashboard/requirements.txt).
    socketio.init_app(app, async_mode="eventlet", cors_allowed_origins="*")

    # Start the background watcher thread once the app is fully constructed.
    socketio.start_background_task(_state_watcher, app)

    return app


# ---------------------------------------------------------------------------
# Dev-server entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    _debug = os.getenv("FLASK_DEBUG", "0") == "1"
    _app = create_app()
    socketio.run(_app, debug=_debug, host="0.0.0.0", port=5000)
