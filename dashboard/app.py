"""MCBOT local dashboard – Flask application.

Run with::

    cd <repo-root>
    python -m dashboard.app

or, for auto-reload during development::

    flask --app dashboard.app run --debug

The dashboard is served at ``/dashboard/`` and exposes two JSON API endpoints:

* ``/dashboard/api/status``  – bot status (running/idle/offline, uptime, errors)
* ``/dashboard/api/stories`` – list of currently active story sessions
"""

from __future__ import annotations

import os

from flask import Blueprint, Flask, jsonify, render_template

from dashboard.state import get_sessions, get_status

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


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__, instance_relative_config=False)
    app.secret_key = os.urandom(24)
    app.register_blueprint(bp, url_prefix="/dashboard")
    return app


# ---------------------------------------------------------------------------
# Dev-server entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    _debug = os.getenv("FLASK_DEBUG", "0") == "1"
    create_app().run(debug=_debug, host="127.0.0.1", port=5000)
