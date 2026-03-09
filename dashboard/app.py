"""MCBOT local dashboard – Flask application.

Run with::

    cd <repo-root>
    python -m dashboard.app

or, for auto-reload during development::

    flask --app dashboard.app run --debug --host=0.0.0.0

The dashboard is served at ``/dashboard/`` and exposes two JSON API endpoints:

* ``/dashboard/api/status``  – bot status (running/idle/offline, uptime, errors)
* ``/dashboard/api/stories`` – list of currently active story sessions

Real-time updates are delivered via Socket.IO.  Whenever ``bot_state.json``
changes on disk a ``story_update`` event is broadcast to all connected clients
so the page refreshes instantly without polling.  The client-side polling loop
is kept as a graceful fallback for environments that do not support WebSockets.
"""

from __future__ import annotations

import os
import time

from flask import Blueprint, Flask, jsonify, render_template
from flask_socketio import SocketIO

from dashboard.state import STATE_FILE, get_session, get_sessions, get_status

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
    """Return active story sessions as JSON."""
    return jsonify(get_sessions())


@bp.route("/story/<user_key>")
def story_live(user_key: str):
    """Render the live detail page for a single story session."""
    session = get_session(user_key)
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
                payload = {
                    "status": get_status(),
                    "stories": get_sessions(),
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

    # async_mode="threading" works with the standard WSGI dev server and keeps
    # compatibility with gunicorn/eventlet if present.
    socketio.init_app(app, async_mode="threading", cors_allowed_origins="*")

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
