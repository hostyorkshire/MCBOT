#!/usr/bin/env bash
# install-dashboard-service.sh – installs and enables the MCBOT dashboard
# as a systemd system service.  Requires sudo/root.
#
# Usage (from anywhere inside the repository):
#   sudo bash dashboard/install-dashboard-service.sh
#
# The script auto-detects the repository root, virtual-environment Python, and
# the non-root user to run the service as.  It then writes a correctly
# populated unit file to /etc/systemd/system/mcbot-dashboard.service.
#
# To uninstall:
#   sudo systemctl disable --now mcbot-dashboard.service
#   sudo rm /etc/systemd/system/mcbot-dashboard.service
#   sudo systemctl daemon-reload

set -euo pipefail

# ---------------------------------------------------------------------------
# Require root
# ---------------------------------------------------------------------------

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    echo "ERROR: This script must be run as root." >&2
    echo "       Re-run with: sudo bash dashboard/install-dashboard-service.sh" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# The repository root is one level above the dashboard/ directory.
REPO_DIR="$(dirname "${SCRIPT_DIR}")"
VENV_PYTHON="${REPO_DIR}/.venv/bin/python"
SERVICE_DEST="/etc/systemd/system/mcbot-dashboard.service"

# Determine the non-root user to run the service as:
#   1. $SUDO_USER – set when the script is invoked via sudo (most common case).
#   2. Owner of the repository directory – fallback for su-based root sessions.
#   3. "nobody"  – last-resort guard; the admin should fix this manually.
if [ -n "${SUDO_USER:-}" ]; then
    DASHBOARD_USER="${SUDO_USER}"
else
    # stat -c %U gives the owner name on Linux; fall back to "nobody" on error.
    DASHBOARD_USER="$(stat -c '%U' "${REPO_DIR}" 2>/dev/null || echo 'nobody')"
    if [ "${DASHBOARD_USER}" = "root" ] || [ "${DASHBOARD_USER}" = "nobody" ]; then
        echo "WARNING: Could not determine a non-root owner for ${REPO_DIR}." >&2
        echo "         The service will run as '${DASHBOARD_USER}'." >&2
        echo "         Consider re-running via sudo to set the correct user." >&2
    fi
fi

# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------

if [ ! -f "${VENV_PYTHON}" ]; then
    echo "ERROR: Python venv not found at ${VENV_PYTHON}" >&2
    echo "       Run setup.sh first to create the virtual environment." >&2
    exit 1
fi

# Verify minimum Python version (3.10+).
if ! "${VENV_PYTHON}" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
    PY_VER="$("${VENV_PYTHON}" --version 2>&1)"
    echo "ERROR: Python 3.10 or newer is required, but the venv contains: ${PY_VER}" >&2
    echo "       Re-create the venv using a supported Python version:" >&2
    echo "         rm -rf ${REPO_DIR}/.venv && python3.10 -m venv ${REPO_DIR}/.venv" >&2
    echo "       Then re-run setup.sh to reinstall dependencies." >&2
    exit 1
fi

# Verify that flask-socketio is installed in the venv (requirements installed).
if ! "${VENV_PYTHON}" -c "import flask_socketio" 2>/dev/null; then
    echo "ERROR: flask_socketio is not installed in the venv at ${VENV_PYTHON}" >&2
    echo "       Run: ${VENV_PYTHON} -m pip install -r ${REPO_DIR}/dashboard/requirements.txt" >&2
    echo "       Or re-run setup.sh to reinstall all dependencies." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Migrate away from the old service name (if still active)
# ---------------------------------------------------------------------------

if systemctl is-active --quiet dashboard-dashboard.service 2>/dev/null; then
    echo "Stopping legacy dashboard-dashboard service before replacing it..."
    systemctl stop dashboard-dashboard.service || true
fi
if systemctl is-enabled --quiet dashboard-dashboard.service 2>/dev/null; then
    echo "Disabling legacy dashboard-dashboard service..."
    systemctl disable dashboard-dashboard.service || true
fi
# Remove the old unit file so systemd won't complain about duplicate names.
if [ -f /etc/systemd/system/dashboard-dashboard.service ]; then
    rm -f /etc/systemd/system/dashboard-dashboard.service
fi

# Stop the current service (if running) before replacing the unit file.
systemctl stop mcbot-dashboard.service 2>/dev/null || true

# ---------------------------------------------------------------------------
# Write the unit file with actual paths substituted in
# ---------------------------------------------------------------------------

echo "Installing MCBOT dashboard systemd service..."
echo "  User:             ${DASHBOARD_USER}"
echo "  WorkingDirectory: ${REPO_DIR}"
echo "  ExecStart:        ${VENV_PYTHON} -m dashboard.app"
echo "  Destination:      ${SERVICE_DEST}"

cat > "${SERVICE_DEST}" << UNIT
[Unit]
Description=MCBOT Web Dashboard
# Require the network stack to be fully online so the dashboard is reachable
# from other devices on the local network immediately after boot.
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${DASHBOARD_USER}
WorkingDirectory=${REPO_DIR}
ExecStart=${VENV_PYTHON} -m dashboard.app
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

chmod 644 "${SERVICE_DEST}"
systemctl daemon-reload
systemctl enable --now mcbot-dashboard.service

echo ""
echo "mcbot-dashboard.service installed and enabled successfully!"
echo "  Status:  sudo systemctl status mcbot-dashboard"
echo "  Logs:    sudo journalctl -u mcbot-dashboard -f"
echo "  Stop:    sudo systemctl stop mcbot-dashboard"
echo "  Disable: sudo systemctl disable mcbot-dashboard"
echo ""
echo "The dashboard will now start automatically on every reboot."
echo "Open http://localhost:5000/dashboard/ in your browser."
