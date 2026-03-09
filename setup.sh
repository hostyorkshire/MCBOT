#!/bin/bash
set -euo pipefail

# Resolve the absolute path of this script so that a reliable sudo command
# can be printed (or used for auto re-exec) regardless of how the script was
# invoked (./setup.sh, bash setup.sh, from another directory, etc.).
SCRIPT_ABS="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

# Check if .env.example exists
if [ ! -f .env.example ]; then
    echo ".env.example file is missing!"
    exit 1
fi

# ---------------------------------------------------------------------------
# Virtual environment setup
# ---------------------------------------------------------------------------

# Validate that python3-venv is available before attempting to create the venv.
if ! python3 -m venv --help > /dev/null 2>&1; then
    echo "ERROR: python3-venv is not available."
    echo "Install it with:  sudo apt install python3-venv"
    exit 1
fi

VENV_DIR="$(cd "$(dirname "$0")" && pwd)/.venv"

if [ -d "$VENV_DIR" ]; then
    echo "Reusing existing virtual environment at ${VENV_DIR}"
else
    echo "Creating virtual environment at ${VENV_DIR} ..."
    python3 -m venv "$VENV_DIR"
fi

echo "Upgrading pip inside the virtual environment ..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip

echo "Installing Python requirements into the virtual environment ..."
"$VENV_DIR/bin/pip" install --quiet -r requirements.txt

echo "Installing dev/monitor requirements into the virtual environment ..."
"$VENV_DIR/bin/pip" install --quiet -r requirements-dev.txt

echo "Installing dashboard requirements into the virtual environment ..."
"$VENV_DIR/bin/pip" install --quiet -r dashboard/requirements.txt

# Verify that flask-socketio was installed correctly (quick import check).
if ! "$VENV_DIR/bin/python" -c "import flask_socketio" 2>/dev/null; then
    echo "WARNING: flask_socketio could not be imported after installation." >&2
    echo "         Check for errors in the pip install output above." >&2
    echo "         To retry: ${VENV_DIR}/bin/pip install -r dashboard/requirements.txt" >&2
fi

echo "Python dependencies installed successfully."

# Verify that the venv Python binary is usable after installation.
if [ ! -f "${VENV_DIR}/bin/python" ]; then
    echo "ERROR: ${VENV_DIR}/bin/python not found after venv setup." >&2
    echo "       Check for errors in the pip install steps above." >&2
    exit 1
fi

printf "\n"

# ---------------------------------------------------------------------------
# .env configuration
# ---------------------------------------------------------------------------

# Detect sudo re-run: root, invoked via sudo, and .env already exists.
# In this case skip all .env prompts to avoid double-prompting the user and
# to prevent an accidental overwrite of the .env they just created.
if [ "$EUID" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ -f .env ]; then
    echo ""
    echo "Detected sudo re-run and existing .env; skipping .env prompts."
else
    echo "Creating/updating .env file from .env.example..."

    # Load defaults if .env exists
    if [ -f .env ]; then
        echo "A .env file already exists."
        read -p "Would you like to overwrite it? (Y/n): " overwrite
        if [ "$overwrite" != "Y" ] && [ "$overwrite" != "y" ]; then
            echo "Creating a backup of .env file..."
            cp .env ".env.backup_$(date +%Y%m%d_%H%M%S)"
            echo "Backup created."
        fi
    fi

    # Read values from .env.example with default values
    GROQ_API_KEY=$(grep 'GROQ_API_KEY' .env.example | cut -d '=' -f2- | xargs)
    GROQ_MODEL=$(grep 'GROQ_MODEL' .env.example | cut -d '=' -f2- | xargs)
    SERIAL_PORT=$(grep 'SERIAL_PORT' .env.example | cut -d '=' -f2- | xargs)
    BAUD_RATE=$(grep 'BAUD_RATE' .env.example | cut -d '=' -f2- | xargs)
    MAX_CHUNK_SIZE=$(grep 'MAX_CHUNK_SIZE' .env.example | cut -d '=' -f2- | xargs)
    CHUNK_DELAY=$(grep 'CHUNK_DELAY' .env.example | cut -d '=' -f2- | xargs)
    MAX_HISTORY=$(grep 'MAX_HISTORY' .env.example | cut -d '=' -f2- | xargs)

    # Prompt for each variable
    read -p "GROQ_API_KEY (Default: $GROQ_API_KEY): " input
    GROQ_API_KEY=${input:-$GROQ_API_KEY}

    # Validate GROQ_API_KEY
    if [ -z "$GROQ_API_KEY" ]; then
        echo "GROQ_API_KEY cannot be empty!"
        exit 1
    fi

    read -p "GROQ_MODEL (Default: $GROQ_MODEL): " input
    GROQ_MODEL=${input:-$GROQ_MODEL}
    read -p "SERIAL_PORT (Default: $SERIAL_PORT): " input
    SERIAL_PORT=${input:-$SERIAL_PORT}
    read -p "BAUD_RATE (Default: $BAUD_RATE): " input
    BAUD_RATE=${input:-$BAUD_RATE}
    read -p "MAX_CHUNK_SIZE (Default: $MAX_CHUNK_SIZE): " input
    MAX_CHUNK_SIZE=${input:-$MAX_CHUNK_SIZE}
    read -p "CHUNK_DELAY (Default: $CHUNK_DELAY): " input
    CHUNK_DELAY=${input:-$CHUNK_DELAY}
    read -p "MAX_HISTORY (Default: $MAX_HISTORY): " input
    MAX_HISTORY=${input:-$MAX_HISTORY}

    # Write to .env file

    printf "# .env configuration file\n# Auto-generated script: setup.sh\n# Make sure to set these values correctly\n" > .env

    echo "GROQ_API_KEY=$GROQ_API_KEY" >> .env

    echo "GROQ_MODEL=$GROQ_MODEL" >> .env

    echo "SERIAL_PORT=$SERIAL_PORT" >> .env

    echo "BAUD_RATE=$BAUD_RATE" >> .env

    echo "MAX_CHUNK_SIZE=$MAX_CHUNK_SIZE" >> .env

    echo "CHUNK_DELAY=$CHUNK_DELAY" >> .env

    echo "MAX_HISTORY=$MAX_HISTORY" >> .env
fi

# ---------------------------------------------------------------------------
# Systemd service installation (bot + dashboard together, optional)
# ---------------------------------------------------------------------------

printf "\n"
read -rp "Would you like to install and enable both systemd services (mcbot + dashboard) for auto-start on reboot? (y/N): " install_service
if [ "$install_service" = "y" ] || [ "$install_service" = "Y" ]; then

    # Require root/sudo for systemd installation
    if [ "$EUID" -ne 0 ]; then
        echo ""
        echo "ERROR: Systemd service installation requires root privileges."
        echo ""
        echo "Re-run the script with sudo using its full path:"
        echo "  sudo bash \"${SCRIPT_ABS}\""
        echo ""
        echo "  (Running 'sudo ./setup.sh' fails when sudo resets PATH or the"
        echo "   working directory differs from the script's location.)"
        echo ""
        read -rp "Re-run now with sudo? (y/N): " _rerun_sudo
        if [ "$_rerun_sudo" = "y" ] || [ "$_rerun_sudo" = "Y" ]; then
            exec sudo bash "${SCRIPT_ABS}"
        fi
        exit 1
    fi

    WORKDIR="$(cd "$(dirname "$0")" && pwd)"
    # When called via sudo, use SUDO_USER to get the original user name;
    # fall back to the current user if SUDO_USER is unset or empty.
    BOT_USER="${SUDO_USER:-$(whoami)}"
    PYTHON_BIN="${WORKDIR}/.venv/bin/python"

    # Verify the venv Python binary is present before writing service files.
    if [ ! -f "${PYTHON_BIN}" ]; then
        echo "ERROR: Venv Python not found at ${PYTHON_BIN}." >&2
        echo "       The virtual environment may not have been created correctly." >&2
        echo "       Re-run setup.sh without sudo to recreate the venv, then try again." >&2
        exit 1
    fi

    # --- Install mcbot.service (CYOA bot) ---
    BOT_SERVICE_DEST="/etc/systemd/system/mcbot.service"
    echo ""
    echo "Installing ${BOT_SERVICE_DEST} ..."
    echo "  User:             ${BOT_USER}"
    echo "  WorkingDirectory: ${WORKDIR}"
    echo "  ExecStart:        ${PYTHON_BIN} ${WORKDIR}/cyoa_bot.py"

    # Stop the service if it is already running before replacing the unit file;
    # this avoids leaving a running process tied to the old unit definition
    # while the new file is being written.
    systemctl stop mcbot.service 2>/dev/null || true

    # Write the unit file with actual paths substituted in
    cat > "${BOT_SERVICE_DEST}" << UNIT
[Unit]
Description=MeshCore CYOA Story Bot
After=network.target

[Service]
Type=simple
User=${BOT_USER}
WorkingDirectory=${WORKDIR}
EnvironmentFile=${WORKDIR}/.env
ExecStart=${PYTHON_BIN} ${WORKDIR}/cyoa_bot.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

    chmod 644 "${BOT_SERVICE_DEST}"
    systemctl daemon-reload
    systemctl enable --now mcbot.service
    echo "mcbot.service installed and enabled."

    # --- Install dashboard.service (web dashboard) ---
    DASHBOARD_SERVICE_DEST="/etc/systemd/system/dashboard.service"
    echo ""
    echo "Installing ${DASHBOARD_SERVICE_DEST} ..."
    echo "  User:             ${BOT_USER}"
    echo "  WorkingDirectory: ${WORKDIR}/dashboard"
    echo "  ExecStart:        ${WORKDIR}/dashboard/start-dashboard.sh"

    # Ensure the launch script is executable.
    chmod +x "${WORKDIR}/dashboard/start-dashboard.sh"

    # Migrate away from legacy service names if they are still present.
    for _legacy_svc in dashboard-dashboard.service mcbot-dashboard.service; do
        if systemctl is-active --quiet "${_legacy_svc}" 2>/dev/null; then
            echo "Stopping legacy ${_legacy_svc} service..."
            systemctl stop "${_legacy_svc}" || true
        fi
        if systemctl is-enabled --quiet "${_legacy_svc}" 2>/dev/null; then
            echo "Disabling legacy ${_legacy_svc} service..."
            systemctl disable "${_legacy_svc}" || true
        fi
        if [ -f "/etc/systemd/system/${_legacy_svc}" ]; then
            rm -f "/etc/systemd/system/${_legacy_svc}"
        fi
    done

    # Stop the current service (if running) before replacing the unit file.
    systemctl stop dashboard.service 2>/dev/null || true

    # Copy dashboard.service template with actual paths substituted in.
    sed -e "s|__USER__|${BOT_USER}|g" \
        -e "s|__WORKDIR__|${WORKDIR}|g" \
        "${WORKDIR}/dashboard.service" > "${DASHBOARD_SERVICE_DEST}"

    chmod 644 "${DASHBOARD_SERVICE_DEST}"
    systemctl daemon-reload
    systemctl enable dashboard.service
    systemctl start dashboard.service
    echo "dashboard.service installed and enabled."

    # --- Post-install status for both services ---
    echo ""
    echo "======================================================================="
    echo " Post-install service status"
    echo "======================================================================="
    systemctl status mcbot.service --no-pager --lines=5 || true
    echo ""
    systemctl status dashboard.service --no-pager --lines=5 || true
    echo ""
    printf "Both services are enabled and will start automatically on every reboot.\n"
    echo ""
    echo "  Bot:              sudo systemctl status mcbot"
    echo "  Bot logs:         sudo journalctl -u mcbot -f"
    echo "  Restart bot:      sudo systemctl restart mcbot"
    echo "  Dashboard:        sudo systemctl status dashboard"
    echo "  Dashboard logs:   sudo journalctl -u dashboard -f"
    echo "  Restart dashboard: sudo systemctl restart dashboard"
    echo "  Stop bot:         sudo systemctl stop mcbot"
    echo "  Stop dashboard:   sudo systemctl stop dashboard"
else
    # Print manual next steps when skipping service installation
    printf "\nNext steps:\n1. Activate the virtual environment: source .venv/bin/activate\n2. Run: python cyoa_bot.py\n   (or without activating: .venv/bin/python cyoa_bot.py)\n"
fi

# ---------------------------------------------------------------------------
# Create dashboard.sh helper (idempotent)
# ---------------------------------------------------------------------------

DASHBOARD_SH="$(cd "$(dirname "$0")" && pwd)/dashboard.sh"
cat > "${DASHBOARD_SH}" << 'EOF'
#!/bin/bash
# Convenience wrapper – activates the venv and starts the web dashboard.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/.venv/bin/activate"
exec python -m dashboard.app
EOF
chmod +x "${DASHBOARD_SH}"
echo "dashboard.sh helper written to ${DASHBOARD_SH}"

# ---------------------------------------------------------------------------
# Offer to start the mcbot monitor
# ---------------------------------------------------------------------------

printf "\n"
read -rp "Would you like to start the mcbot monitor now? (y/N): " start_monitor
if [ "$start_monitor" = "y" ] || [ "$start_monitor" = "Y" ]; then
    echo ""
    echo "Starting mcbot monitor ..."
    "$VENV_DIR/bin/python" mcbot_monitor.py --info || true
else
    echo ""
    echo "You can run the monitor at any time with:"
    echo "  ${VENV_DIR}/bin/python mcbot_monitor.py --info"
    echo "  ${VENV_DIR}/bin/python mcbot_monitor.py --list-serial"
fi

# ---------------------------------------------------------------------------
# Final instructions
# ---------------------------------------------------------------------------

printf "\n"
echo "======================================================================="
echo " Setup complete!  Here is how to start everything:"
echo "======================================================================="
echo ""
echo "  Start the bot (manual):"
echo "    source .venv/bin/activate && python cyoa_bot.py"
echo "    (or: sudo systemctl start mcbot  — if service was installed)"
echo ""
echo "  Start the web dashboard (manual):"
echo "    ./dashboard.sh"
echo "    (or: sudo systemctl start dashboard  — if service was installed)"
echo ""
echo "  Then open your browser at:  http://localhost:5000"
echo ""
echo "  If both services were installed, they will start automatically"
echo "  on every reboot — no manual action needed."
echo ""
echo "======================================================================="