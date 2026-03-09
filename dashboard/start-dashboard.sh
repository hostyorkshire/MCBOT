#!/usr/bin/env bash
# start-dashboard.sh – install requirements and start the MCBOT dashboard.
#
# Usage (from the repository root or the dashboard/ directory):
#
#   bash dashboard/start-dashboard.sh
#
# The script:
#   1. Locates the repository root (the directory containing this script's
#      parent, i.e. the repo root when the script lives in dashboard/).
#   2. Installs / refreshes dashboard/requirements.txt.
#   3. Launches the dashboard via `python -m dashboard.app` so that the
#      Flask-SocketIO runner is used and real-time updates work correctly.
#
# NOTE: Do NOT use `flask run` – the Werkzeug server does not support
# Socket.IO, so live updates will silently break.

set -euo pipefail

# ---------------------------------------------------------------------------
# Locate the repository root (one level above this script).
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ---------------------------------------------------------------------------
# Log startup failures to a file so they are visible without journalctl.
# ---------------------------------------------------------------------------
LOGFILE="${SCRIPT_DIR}/dashboard-error.log"
trap 'echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] start-dashboard.sh: startup failed (exit $?)" >> "${LOGFILE}"' ERR

echo "=== MCBOT Dashboard ==="
echo "Repository root: ${REPO_ROOT}"

# ---------------------------------------------------------------------------
# Detect Python interpreter (prefer virtualenv or system python3).
# ---------------------------------------------------------------------------
if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    PYTHON="${REPO_ROOT}/.venv/bin/python"
    echo "Using virtualenv Python: ${PYTHON}"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
    echo "Using system Python: $(command -v python3)"
elif command -v python &>/dev/null; then
    PYTHON="python"
    echo "Using system Python: $(command -v python)"
else
    echo "ERROR: No Python interpreter found." >&2
    echo "  Install Python 3.10+ and try again." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Verify minimum Python version (3.10+).
# ---------------------------------------------------------------------------
if ! "${PYTHON}" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
    PY_VER="$("${PYTHON}" --version 2>&1)"
    echo "ERROR: Python 3.10 or newer is required, but found: ${PY_VER}" >&2
    echo "  Install a supported Python version and try again." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Install / refresh dashboard requirements.
# ---------------------------------------------------------------------------
REQ_FILE="${REPO_ROOT}/dashboard/requirements.txt"
if [[ ! -f "${REQ_FILE}" ]]; then
    echo "ERROR: Requirements file not found: ${REQ_FILE}" >&2
    exit 1
fi

echo ""
echo "Installing dashboard requirements from ${REQ_FILE} ..."
"${PYTHON}" -m pip install --quiet -r "${REQ_FILE}"
echo "Requirements OK."

# ---------------------------------------------------------------------------
# Verify that flask-socketio is importable before starting.
# ---------------------------------------------------------------------------
if ! "${PYTHON}" -c "import flask_socketio" 2>/dev/null; then
    echo "" >&2
    echo "ERROR: flask_socketio could not be imported after installation." >&2
    echo "  Try running: ${PYTHON} -m pip install -r ${REQ_FILE}" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Start the dashboard.
# ---------------------------------------------------------------------------
echo ""
echo "Starting MCBOT dashboard on http://localhost:5000/dashboard/ ..."
echo "(Press Ctrl+C to stop)"
echo ""
cd "${REPO_ROOT}"
exec "${PYTHON}" -m dashboard.app
