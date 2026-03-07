#!/bin/bash
set -euo pipefail

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

echo "Python dependencies installed successfully."
printf "\n"

# Prompt for .env configuration

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

# ---------------------------------------------------------------------------
# Systemd service installation (optional)
# ---------------------------------------------------------------------------

printf "\n"
read -rp "Would you like to install and enable the mcbot systemd service (auto-start on reboot)? (y/N): " install_service
if [ "$install_service" = "y" ] || [ "$install_service" = "Y" ]; then

    # Require root/sudo for systemd installation
    if [ "$EUID" -ne 0 ]; then
        echo ""
        echo "ERROR: Systemd service installation requires root privileges."
        echo "Please re-run the script with sudo:"
        echo "  sudo ./setup.sh"
        exit 1
    fi

    WORKDIR="$(cd "$(dirname "$0")" && pwd)"
    # When called via sudo, use SUDO_USER to get the original user name;
    # fall back to the current user if SUDO_USER is unset or empty.
    BOT_USER="${SUDO_USER:-$(whoami)}"
    PYTHON_BIN="${WORKDIR}/.venv/bin/python"
    SERVICE_DEST="/etc/systemd/system/mcbot.service"

    echo "Installing systemd unit to ${SERVICE_DEST} ..."
    echo "  User:           ${BOT_USER}"
    echo "  WorkingDirectory: ${WORKDIR}"
    echo "  ExecStart:      ${PYTHON_BIN} ${WORKDIR}/cyoa_bot.py"

    # Stop the service if it is already running before replacing the unit file;
    # this avoids leaving a running process tied to the old unit definition
    # while the new file is being written.
    systemctl stop mcbot.service 2>/dev/null || true

    # Write the unit file with actual paths substituted in
    cat > "${SERVICE_DEST}" << UNIT
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

    chmod 644 "${SERVICE_DEST}"
    systemctl daemon-reload
    systemctl enable --now mcbot.service

    echo ""
    echo "mcbot.service installed and enabled successfully!"
    echo "  Status:   sudo systemctl status mcbot"
    echo "  Logs:     sudo journalctl -u mcbot -f"
    echo "  Stop:     sudo systemctl stop mcbot"
    echo "  Disable:  sudo systemctl disable mcbot"
    printf "\nThe bot will now start automatically on every reboot.\n"
else
    # Print manual next steps when skipping service installation
    printf "\nNext steps:\n1. Activate the virtual environment: source .venv/bin/activate\n2. Run: python cyoa_bot.py\n   (or without activating: .venv/bin/python cyoa_bot.py)\n"
fi

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