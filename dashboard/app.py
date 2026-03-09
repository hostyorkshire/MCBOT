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

import os
import sys

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

from flask import Blueprint, Flask, jsonify, render_template  # noqa: E402
from flask_socketio import SocketIO  # noqa: E402

from dashboard.active_stories import load_stories  # noqa: E402
from dashboard.state import STATE_FILE, get_session, get_sessions, get_status  # noqa: E402

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


@bp.route("/api/stories")
def api_stories():
    """Return last 20 story sessions (active, finished, or restarted) as JSON."""
    # Start with the persisted log of finished/restarted sessions.
    merged: dict[str, dict] = {s["user_key"]: s for s in load_stories() if s.get("user_key")}
    # Override with currently active sessions (most up-to-date data).
    for session in get_sessions():
        uk = session.get("user_key")
        if uk:
            merged[uk] = session
    # Return newest-first, capped at 20.
    stories = sorted(merged.values(), key=lambda s: s.get("started_at", 0), reverse=True)
    return jsonify(stories[:20])


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
# Socket.IO instance (initialised properly inside create_app)
# ---------------------------------------------------------------------------

socketio = SocketIO()


def _state_watcher(app: Flask) -> None:
    """Background thread: watch bot_state.json for changes and emit events.

    Runs inside the application context so that Flask-SocketIO helpers are
    accessible.  The thread is started by :func:`create_app` via
    ``socketio.start_background_task``.
    """
    last_mtime: float | None = None
    poll_interval = 1.0  # seconds between file-stat checks

    # Seed the initial mtime so the first loop iteration does not emit a
    # spurious event before any real change has occurred.
    try:
        last_mtime = os.path.getmtime(STATE_FILE)
    except OSError:
        last_mtime = None

    with app.app_context():
        while True:
            try:
                mtime = os.path.getmtime(STATE_FILE)
            except OSError:
                mtime = None

            if mtime != last_mtime:
                last_mtime = mtime
                merged: dict[str, dict] = {
                    s["user_key"]: s for s in load_stories() if s.get("user_key")
                }
                for session in get_sessions():
                    uk = session.get("user_key")
                    if uk:
                        merged[uk] = session
                stories = sorted(
                    merged.values(), key=lambda s: s.get("started_at", 0), reverse=True
                )
                payload = {
                    "status": get_status(),
                    "stories": stories[:20],
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
