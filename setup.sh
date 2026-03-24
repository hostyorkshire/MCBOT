#!/bin/bash
set -euo pipefail

# ---------------------------------------------------------------------------
# MCBOT – MeshCore CYOA Adventure Bot  |  Setup Wizard
# ---------------------------------------------------------------------------
# MCBOT always runs inside a Python virtual environment (.venv).
# This script is idempotent – re-run it safely at any time to repair or
# upgrade an existing installation without losing your configuration.
# ---------------------------------------------------------------------------

cat << 'BANNER'

  __  __  ____  ____   ___ _____
 |  \/  |/ ___|| __ ) / _ \_   _|
 | |\/| | |    |  _ \| | | || |
 | |  | | |___ | |_) | |_| || |
 |_|  |_|\____|____/ \___/ |_|

      .---------.
      |  o   o  |   ~ Choose Your Own Adventure ~
      |    ^    |   MeshCore AI Story Bot
      |  \___/  |   Powered by Groq AI
      '---------'
      /|       |\
     d '-------' b   Adventure awaits, brave hero!

  >>>  Setup Wizard  –  stand by...  <<<

BANNER

# ---------------------------------------------------------------------------
# Progress bar helpers
# ---------------------------------------------------------------------------

GREEN='\033[0;32m'
RESET='\033[0m'
PROGRESS_INCREMENT=5
PROGRESS_MAX=95

_bar() {
    local pct=$1
    local filled=$(( pct * 40 / 100 ))
    local empty=$(( 40 - filled ))
    local bar=""
    local i
    for (( i = 0; i < filled; i++ )); do bar+="█"; done
    for (( i = 0; i < empty; i++ )); do bar+="░"; done
    printf "\r${GREEN}[%s]${RESET} %3d%%" "${bar}" "${pct}"
}

run_with_progress() {
    local label="$1"
    shift
    printf "%s\n" "${label}"

    # Capture stdout+stderr to a temp log so failures are debuggable.
    local log
    log="$(mktemp -t mcbot-setup.XXXXXX.log)"
    chmod 600 "${log}"

    "$@" >"${log}" 2>&1 &
    local pid=$!
    local pct=0
    while kill -0 "${pid}" 2>/dev/null; do
        _bar "${pct}"
        sleep 0.4
        if [ "${pct}" -lt "${PROGRESS_MAX}" ]; then
            pct=$(( pct + PROGRESS_INCREMENT ))
        fi
    done

    if wait "${pid}"; then
        _bar 100
        printf "\n"
        rm -f "${log}"
        return 0
    else
        local exit_code=$?
        printf "\n"
        echo "ERROR: command failed (exit ${exit_code}): $*" >&2
        echo "---- begin output ----" >&2
        tail -n 200 "${log}" >&2
        echo "---- end output ----" >&2
        rm -f "${log}"
        return "${exit_code}"
    fi
}

# ---------------------------------------------------------------------------
# Resolve the absolute path of this script so that a reliable sudo command
# can be printed (or used for auto re-exec) regardless of how the script was
# invoked (./setup.sh, bash setup.sh, from another directory, etc.).
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Root/sudo guard – venv and pip must NOT be set up as root.
# ---------------------------------------------------------------------------
# Running setup as root creates a root-owned .venv that causes PermissionError
# when pip or the bot later runs as a normal user.
#
# If root is detected:
#   - .venv already exists: skip venv/pip steps (assume done as normal user)
#     and continue to the .env / systemd sections that do need root.
#   - .venv does not exist: abort with clear instructions.
# ---------------------------------------------------------------------------
_DO_VENV=true
if [ "$EUID" -eq 0 ]; then
    if [ -d "$VENV_DIR" ]; then
        echo ""
        echo "Detected sudo/root invocation – skipping venv and pip install steps."
        echo "(These must be performed as your normal user, not root.)"
        echo ""
        _DO_VENV=false
    else
        echo "" >&2
        echo "ERROR: Do not run setup.sh as root (or with sudo) for environment setup." >&2
        echo "" >&2
        echo "  Creating the virtual environment and installing pip packages as root" >&2
        echo "  produces a root-owned .venv that will cause PermissionError when" >&2
        echo "  the bot or pip later runs as a normal user." >&2
        echo "" >&2
        echo "  Run setup WITHOUT sudo first (creates the venv as your normal user):" >&2
        echo "    bash \"${SCRIPT_ABS}\"" >&2
        echo "" >&2
        echo "  Then re-run with sudo ONLY for the systemd service installation step:" >&2
        echo "    sudo bash \"${SCRIPT_ABS}\"" >&2
        echo "" >&2
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# Ownership / writability check – catch a previously root-owned .venv early.
# ---------------------------------------------------------------------------
if [ "$_DO_VENV" = true ] && [ -d "$VENV_DIR" ] && [ ! -w "$VENV_DIR" ]; then
    _VENV_OWNER="$(stat -c '%U' "$VENV_DIR" 2>/dev/null || echo 'unknown')"
    echo "" >&2
    echo "ERROR: The existing .venv at ${VENV_DIR} is not writable by you." >&2
    echo "       It appears to be owned by: ${_VENV_OWNER}" >&2
    echo "" >&2
    echo "  This typically happens when setup.sh was previously run with sudo." >&2
    echo "" >&2
    echo "  Fix options:" >&2
    echo "    1. Remove the root-owned venv and re-run setup (recommended):" >&2
    echo "         sudo rm -rf \"${VENV_DIR}\" && bash \"${SCRIPT_ABS}\"" >&2
    echo "    2. Take ownership of the existing venv and re-run setup:" >&2
    echo "         sudo chown -R $(id -un):$(id -gn) \"${VENV_DIR}\"" >&2
    echo "         bash \"${SCRIPT_ABS}\"" >&2
    echo "" >&2
    exit 1
fi

if [ "$_DO_VENV" = true ]; then

if [ -d "$VENV_DIR" ]; then
    echo "Reusing existing virtual environment at ${VENV_DIR}"
    # Verify the existing venv is functional; a corrupted or outdated venv
    # (e.g. after a Python version upgrade) will fail here.
    if ! "$VENV_DIR/bin/pip" --version > /dev/null 2>&1; then
        echo "" >&2
        echo "ERROR: Existing virtual environment at ${VENV_DIR} appears broken." >&2
        echo "       (pip is not functional inside the venv)" >&2
        echo "" >&2
        echo "  Recovery options:" >&2
        echo "    1. Remove the venv and re-run setup:" >&2
        echo "         rm -rf ${VENV_DIR} && bash \"${SCRIPT_ABS}\"" >&2
        echo "    2. If you recently upgraded Python, a fresh venv is required." >&2
        echo "" >&2
        exit 1
    fi
else
    if ! run_with_progress "Creating virtual environment at ${VENV_DIR} ..." \
            python3 -m venv "$VENV_DIR"; then
        echo "" >&2
        echo "ERROR: Failed to create the virtual environment." >&2
        echo "       Ensure python3-venv is installed:" >&2
        echo "         sudo apt install python3-venv" >&2
        exit 1
    fi
fi

run_with_progress "Upgrading pip inside the virtual environment ..." \
    "$VENV_DIR/bin/pip" install --upgrade --retries 10 --timeout 60 pip

# Verify requirements.txt is present before attempting to install from it.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ ! -f "${SCRIPT_DIR}/requirements.txt" ]; then
    echo "" >&2
    echo "ERROR: requirements.txt not found in ${SCRIPT_DIR}." >&2
    echo "       Cannot install Python dependencies without it." >&2
    echo "       Please ensure requirements.txt exists in the repository root." >&2
    exit 1
fi

if ! run_with_progress "Installing Python requirements into the virtual environment ..." \
        "$VENV_DIR/bin/pip" install --retries 10 --timeout 60 -r requirements.txt; then
    echo "" >&2
    echo "ERROR: Failed to install dependencies from requirements.txt." >&2
    echo "       Check the output above for details, then try:" >&2
    echo "         ${VENV_DIR}/bin/pip install --retries 10 --timeout 60 -r requirements.txt" >&2
    exit 1
fi

if ! run_with_progress "Installing dev requirements into the virtual environment ..." \
        "$VENV_DIR/bin/pip" install --retries 10 --timeout 60 -r requirements-dev.txt; then
    echo "" >&2
    echo "ERROR: Failed to install dev dependencies from requirements-dev.txt." >&2
    echo "       This may be a transient network error.  Try re-running setup, or:" >&2
    echo "         ${VENV_DIR}/bin/pip install --retries 10 --timeout 60 -r requirements-dev.txt" >&2
    exit 1
fi

if ! run_with_progress "Installing dashboard requirements into the virtual environment ..." \
        "$VENV_DIR/bin/pip" install --retries 10 --timeout 60 -r dashboard/requirements.txt; then
    echo "" >&2
    echo "ERROR: Failed to install dashboard dependencies from dashboard/requirements.txt." >&2
    echo "       This may be a transient network error.  Try re-running setup, or:" >&2
    echo "         ${VENV_DIR}/bin/pip install --retries 10 --timeout 60 -r dashboard/requirements.txt" >&2
    exit 1
fi

# Verify that flask-socketio was installed correctly (quick import check).
if ! "$VENV_DIR/bin/python" -c "import flask_socketio" 2>/dev/null; then
    echo "WARNING: flask_socketio could not be imported after installation." >&2
    echo "         Check for errors in the pip install output above." >&2
    echo "         To retry: ${VENV_DIR}/bin/pip install -r dashboard/requirements.txt" >&2
fi

# Verify that python-dotenv is importable; this module is required by the bot
# to read its .env configuration file (from dotenv import load_dotenv).
if ! "$VENV_DIR/bin/python" -c "from dotenv import load_dotenv" 2>/dev/null; then
    echo "" >&2
    echo "ERROR: python-dotenv could not be imported after installation." >&2
    echo "       This module is required for the bot to read its .env config." >&2
    echo "       Try running:" >&2
    echo "         ${VENV_DIR}/bin/pip install python-dotenv" >&2
    exit 1
fi

echo "Python dependencies installed successfully."

# Verify that the venv Python binary is usable after installation.
if [ ! -f "${VENV_DIR}/bin/python" ]; then
    echo "ERROR: ${VENV_DIR}/bin/python not found after venv setup." >&2
    echo "       Check for errors in the pip install steps above." >&2
    exit 1
fi

fi  # end _DO_VENV

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
        read -rp "Would you like to overwrite it? (Y/n): " overwrite
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
    SEND_RETRIES=$(grep 'SEND_RETRIES' .env.example | cut -d '=' -f2- | xargs)
    SEND_RETRY_BASE_DELAY=$(grep 'SEND_RETRY_BASE_DELAY' .env.example | cut -d '=' -f2- | xargs)
    SEND_RETRY_MAX_DELAY=$(grep 'SEND_RETRY_MAX_DELAY' .env.example | cut -d '=' -f2- | xargs)

    # Prompt for each variable
    read -rp "GROQ_API_KEY (Default: $GROQ_API_KEY): " input
    GROQ_API_KEY=${input:-$GROQ_API_KEY}

    # Validate GROQ_API_KEY
    if [ -z "$GROQ_API_KEY" ]; then
        echo "GROQ_API_KEY cannot be empty!"
        exit 1
    fi

    read -rp "GROQ_MODEL (Default: $GROQ_MODEL): " input
    GROQ_MODEL=${input:-$GROQ_MODEL}
    read -rp "SERIAL_PORT (Default: $SERIAL_PORT): " input
    SERIAL_PORT=${input:-$SERIAL_PORT}
    read -rp "BAUD_RATE (Default: $BAUD_RATE): " input
    BAUD_RATE=${input:-$BAUD_RATE}
    read -rp "MAX_CHUNK_SIZE (Default: $MAX_CHUNK_SIZE): " input
    MAX_CHUNK_SIZE=${input:-$MAX_CHUNK_SIZE}
    read -rp "CHUNK_DELAY (Default: $CHUNK_DELAY): " input
    CHUNK_DELAY=${input:-$CHUNK_DELAY}
    read -rp "MAX_HISTORY (Default: $MAX_HISTORY): " input
    MAX_HISTORY=${input:-$MAX_HISTORY}
    read -rp "SEND_RETRIES (Default: $SEND_RETRIES): " input
    SEND_RETRIES=${input:-$SEND_RETRIES}
    read -rp "SEND_RETRY_BASE_DELAY (Default: $SEND_RETRY_BASE_DELAY): " input
    SEND_RETRY_BASE_DELAY=${input:-$SEND_RETRY_BASE_DELAY}
    read -rp "SEND_RETRY_MAX_DELAY (Default: $SEND_RETRY_MAX_DELAY): " input
    SEND_RETRY_MAX_DELAY=${input:-$SEND_RETRY_MAX_DELAY}

    # Write to .env file

    printf "# .env configuration file\n# Auto-generated script: setup.sh\n# Make sure to set these values correctly\n" > .env

    {
        echo "GROQ_API_KEY=$GROQ_API_KEY"
        echo "GROQ_MODEL=$GROQ_MODEL"
        echo "SERIAL_PORT=$SERIAL_PORT"
        echo "BAUD_RATE=$BAUD_RATE"
        echo "MAX_CHUNK_SIZE=$MAX_CHUNK_SIZE"
        echo "CHUNK_DELAY=$CHUNK_DELAY"
        echo "MAX_HISTORY=$MAX_HISTORY"
        echo "SEND_RETRIES=$SEND_RETRIES"
        echo "SEND_RETRY_BASE_DELAY=$SEND_RETRY_BASE_DELAY"
        echo "SEND_RETRY_MAX_DELAY=$SEND_RETRY_MAX_DELAY"
    } >> .env
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
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
Type=simple
User=${BOT_USER}
WorkingDirectory=${WORKDIR}
EnvironmentFile=${WORKDIR}/.env
Environment=PYTHONUNBUFFERED=1
ExecStart=${PYTHON_BIN} ${WORKDIR}/cyoa_bot.py
Restart=always
RestartSec=15
TimeoutStartSec=60
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
    echo "  WorkingDirectory: ${WORKDIR}"
    echo "  ExecStart:        ${WORKDIR}/.venv/bin/python -m dashboard.app"

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
    printf "\nNext steps:\n"
    printf "  1. Activate the venv:   source .venv/bin/activate\n"
    printf "  2. Run the bot:         python cyoa_bot.py\n"
    printf "     (or without activating: .venv/bin/python cyoa_bot.py)\n"
    printf "  3. Re-run setup:        bash \"%s\"\n" "${SCRIPT_ABS}"
    printf "  4. To install systemd:  sudo bash \"%s\"\n\n" "${SCRIPT_ABS}"
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
# Offer to run the Cloudflare Tunnel setup script
# ---------------------------------------------------------------------------

CF_TUNNEL_SCRIPT="$(cd "$(dirname "$0")" && pwd)/setup-cloudflare-tunnel.sh"
if [ -f "${CF_TUNNEL_SCRIPT}" ]; then
    printf "\n"
    read -rp "Would you like to set up the Cloudflare Tunnel now? (y/N): " setup_cf
    if [ "$setup_cf" = "y" ] || [ "$setup_cf" = "Y" ]; then
        echo ""
        echo "Launching Cloudflare Tunnel setup ..."
        bash "${CF_TUNNEL_SCRIPT}"
    else
        echo ""
        echo "You can set up the Cloudflare Tunnel later by running:"
        echo "  bash \"${CF_TUNNEL_SCRIPT}\""
    fi
fi

# ---------------------------------------------------------------------------
# Final instructions
# ---------------------------------------------------------------------------

printf "\n"
echo "======================================================================="
echo " Setup complete!  Here is how to start everything:"
echo "======================================================================="
echo ""
echo "  MCBOT always runs inside the Python virtual environment (.venv/)."
echo ""
echo "  Activate the venv (required for manual use in a new shell):"
echo "    source .venv/bin/activate"
echo "    python cyoa_bot.py"
echo ""
echo "  Or run without activating (single command):"
echo "    .venv/bin/python cyoa_bot.py"
echo ""
echo "  Start the bot via systemd (if service was installed):"
echo "    sudo systemctl start mcbot"
echo "    sudo systemctl status mcbot"
echo "    sudo journalctl -u mcbot -f"
echo ""
echo "  Start the web dashboard (manual):"
echo "    ./dashboard.sh"
echo "    (or: sudo systemctl start dashboard  — if service was installed)"
echo ""
echo "  Then open your browser at:  http://localhost:5000"
echo ""
echo "  Install / restart the systemd services after any update:"
echo "    sudo bash \"${SCRIPT_ABS}\"   # re-run setup with sudo to reinstall services"
echo "    sudo systemctl restart mcbot"
echo "    sudo systemctl restart dashboard"
echo ""
echo "  Re-run setup at any time to repair or upgrade the installation:"
echo "    bash \"${SCRIPT_ABS}\""
echo ""
echo "  If you see a broken venv or missing module, remove and recreate it:"
echo "    rm -rf .venv && bash \"${SCRIPT_ABS}\""
echo ""
echo "  If both services were installed, they will start automatically"
echo "  on every reboot — no manual action needed."
echo ""
echo "======================================================================="