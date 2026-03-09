#!/usr/bin/env bash
# install-dashboard-service.sh – installs and enables the MCBOT dashboard
# as a systemd system service.  Requires sudo.
#
# Usage (from the repository root):
#   bash dashboard/install-dashboard-service.sh
#
# The script:
#   1. Copies dashboard/dashboard-dashboard.service to /etc/systemd/system/.
#   2. Reloads the systemd daemon.
#   3. Enables and starts the dashboard-dashboard service.
#
# To uninstall:
#   sudo systemctl disable --now dashboard-dashboard.service
#   sudo rm /etc/systemd/system/dashboard-dashboard.service
#   sudo systemctl daemon-reload

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_SRC="${SCRIPT_DIR}/dashboard-dashboard.service"
SERVICE_DEST="/etc/systemd/system/dashboard-dashboard.service"

if [ ! -f "${SERVICE_SRC}" ]; then
    echo "ERROR: Service file not found at ${SERVICE_SRC}" >&2
    exit 1
fi

echo "Installing dashboard systemd service..."
echo "  Source:      ${SERVICE_SRC}"
echo "  Destination: ${SERVICE_DEST}"

sudo cp "${SERVICE_SRC}" "${SERVICE_DEST}"
sudo chmod 644 "${SERVICE_DEST}"

sudo systemctl daemon-reload
sudo systemctl enable --now dashboard-dashboard.service

echo ""
echo "dashboard-dashboard.service installed and enabled successfully!"
echo "  Status:  sudo systemctl status dashboard-dashboard"
echo "  Logs:    sudo journalctl -u dashboard-dashboard -f"
echo "  Stop:    sudo systemctl stop dashboard-dashboard"
echo "  Disable: sudo systemctl disable dashboard-dashboard"
echo ""
echo "The dashboard will now start automatically on every reboot."
echo "Open http://localhost:5000/dashboard/ in your browser."
