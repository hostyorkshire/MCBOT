#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# MCBOT – Cloudflare Tunnel Setup Script
# ---------------------------------------------------------------------------
# Automates the complete Cloudflare Tunnel setup for a Raspberry Pi Zero 2W
# running MCBOT's Flask API backend.
#
# Usage:
#   cd MCBOT
#   bash setup-cloudflare-tunnel.sh
#
# This script does NOT require Python or venv activation — it only installs
# and configures the cloudflared binary and systemd services.  It is safe to
# run whether or not a virtual environment is active.
#
# Steps match the numbered steps in docs/cloudflare-tunnel-setup.md:
#   Step 1 – Install cloudflared
#   Step 2 – Authenticate with Cloudflare
#   Step 3 – Create a Named Tunnel
#   Step 4 – Create the DNS CNAME Record
#   Step 5 – Create the Config File
#   Step 6 – Enable CORS on the Flask Backend
#   Step 7 – Test the Tunnel and CORS (summary / curl command printed)
#   Step 8 – Run as a systemd Service (Autostart)
# ---------------------------------------------------------------------------
set -euo pipefail

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
RESET='\033[0m'

info()    { echo -e "${YELLOW}[INFO]  $*${RESET}"; }
success() { echo -e "${GREEN}[✓]    $*${RESET}"; }
error()   { echo -e "${RED}[ERROR] $*${RESET}" >&2; }

step() {
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo -e "${YELLOW}  $*${RESET}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
}

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
echo ""
echo -e "${GREEN}  __  __  ____  ____   ___ _____ ${RESET}"
echo -e "${GREEN} |  \/  |/ ___|| __ ) / _ \\_   _|${RESET}"
echo -e "${GREEN} | |\\/| | |    |  _ \\| | | || |  ${RESET}"
echo -e "${GREEN} | |  | | |___ | |_) | |_| || |  ${RESET}"
echo -e "${GREEN} |_|  |_|\____||____/ \\___/ |_|  ${RESET}"
echo ""
echo -e "${GREEN}  Cloudflare Tunnel Setup Wizard${RESET}"
echo ""

# ---------------------------------------------------------------------------
# Interactive prompts
# ---------------------------------------------------------------------------
step "Configuration prompts"
info "Press Enter to accept the default shown in [brackets]."
echo ""

read -r -p "  Tunnel name                    [mcbot-tunnel]: " TUNNEL_NAME
TUNNEL_NAME="${TUNNEL_NAME:-mcbot-tunnel}"

read -r -p "  Bot API subdomain              [apistorybot.intergalactic.it.com]: " BOT_API_SUBDOMAIN
BOT_API_SUBDOMAIN="${BOT_API_SUBDOMAIN:-apistorybot.intergalactic.it.com}"

read -r -p "  CORS origin (website domain)   [https://storybot.intergalactic.it.com]: " CORS_ORIGIN
CORS_ORIGIN="${CORS_ORIGIN:-https://storybot.intergalactic.it.com}"

read -r -p "  Flask app port                 [5000]: " FLASK_PORT
FLASK_PORT="${FLASK_PORT:-5000}"

read -r -p "  Linux username                 [${USER}]: " LINUX_USER
LINUX_USER="${LINUX_USER:-${USER}}"

echo ""
echo -e "${GREEN}  Using:${RESET}"
echo "    Tunnel name    : ${TUNNEL_NAME}"
echo "    Bot API        : https://${BOT_API_SUBDOMAIN}"
echo "    CORS origin    : ${CORS_ORIGIN}"
echo "    Flask port     : ${FLASK_PORT}"
echo "    Linux user     : ${LINUX_USER}"
echo ""

read -r -p "  Proceed with these settings? [Y/n]: " CONFIRM
CONFIRM="${CONFIRM:-Y}"
if [[ ! "${CONFIRM}" =~ ^[Yy]$ ]]; then
    info "Aborted by user."
    exit 0
fi

# ---------------------------------------------------------------------------
# Step 1 – Install cloudflared
# ---------------------------------------------------------------------------
step "Step 1 – Install cloudflared"

if command -v cloudflared > /dev/null 2>&1; then
    EXISTING_VER="$(cloudflared --version 2>&1 | head -n1)"
    success "cloudflared is already installed: ${EXISTING_VER}"
    success "Skipping download/install."
else
    # Detect architecture
    RAW_ARCH="$(uname -m)"
    case "${RAW_ARCH}" in
        aarch64) CF_ARCH="arm64" ;;
        x86_64)  CF_ARCH="amd64" ;;
        *)
            error "Unsupported architecture: ${RAW_ARCH}"
            error "This script supports aarch64 (arm64) and x86_64 (amd64) only."
            exit 1
            ;;
    esac
    info "Detected architecture: ${RAW_ARCH} → ${CF_ARCH}"

    DEB_FILE="cloudflared-linux-${CF_ARCH}.deb"
    DL_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/${DEB_FILE}"

    info "Downloading ${DEB_FILE} from GitHub Releases..."
    wget -q --show-progress -O "/tmp/${DEB_FILE}" "${DL_URL}"

    info "Installing ${DEB_FILE}..."
    sudo dpkg -i "/tmp/${DEB_FILE}"

    rm -f "/tmp/${DEB_FILE}"
    success "cloudflared installed successfully."
fi

# ---------------------------------------------------------------------------
# Verify install
# ---------------------------------------------------------------------------
if ! CF_VERSION="$(cloudflared --version 2>&1 | head -n1)"; then
    error "cloudflared is not working after installation."
    error "Run 'cloudflared --version' manually to investigate."
    exit 1
fi
success "Verified: ${CF_VERSION}"

# ---------------------------------------------------------------------------
# Step 2 – Authenticate with Cloudflare
# ---------------------------------------------------------------------------
step "Step 2 – Authenticate with Cloudflare"

echo ""
echo -e "${YELLOW}  ┌─────────────────────────────────────────────────────────────────┐${RESET}"
echo -e "${YELLOW}  │  ⚠  HEADLESS / SSH NOTICE – READ BEFORE PRESSING ENTER         │${RESET}"
echo -e "${YELLOW}  │                                                                 │${RESET}"
echo -e "${YELLOW}  │  This Pi has NO desktop and NO browser.                        │${RESET}"
echo -e "${YELLOW}  │                                                                 │${RESET}"
echo -e "${YELLOW}  │  When the command runs, cloudflared will print a message that  │${RESET}"
echo -e "${YELLOW}  │  says \"A browser window will open\" — IGNORE THAT LINE.         │${RESET}"
echo -e "${YELLOW}  │                                                                 │${RESET}"
echo -e "${YELLOW}  │  What to do instead:                                           │${RESET}"
echo -e "${YELLOW}  │    1. Look for a long https://dash.cloudflare.com/... URL      │${RESET}"
echo -e "${YELLOW}  │       printed in this terminal.                                │${RESET}"
echo -e "${YELLOW}  │    2. Copy that URL.                                           │${RESET}"
echo -e "${YELLOW}  │    3. Paste it into a browser on your LAPTOP or other device. │${RESET}"
echo -e "${YELLOW}  │    4. Log in to Cloudflare and select the domain you want      │${RESET}"
echo -e "${YELLOW}  │       to use (e.g. intergalactic.it.com).                      │${RESET}"
echo -e "${YELLOW}  │    5. Return to this terminal — it will continue automatically.│${RESET}"
echo -e "${YELLOW}  │                                                                 │${RESET}"
echo -e "${YELLOW}  │  A certificate file will be saved to ~/.cloudflared/cert.pem  │${RESET}"
echo -e "${YELLOW}  │  on the Pi once you have authorised the domain.               │${RESET}"
echo -e "${YELLOW}  └─────────────────────────────────────────────────────────────────┘${RESET}"
echo ""

cloudflared tunnel login
success "Authentication complete. Credentials saved to ~/.cloudflared/cert.pem"

# ---------------------------------------------------------------------------
# Step 3 – Create a Named Tunnel
# ---------------------------------------------------------------------------
step "Step 3 – Create a Named Tunnel"

info "Creating tunnel '${TUNNEL_NAME}'..."
TUNNEL_OUTPUT="$(cloudflared tunnel create "${TUNNEL_NAME}" 2>&1)"
echo "${TUNNEL_OUTPUT}"

TUNNEL_ID="$(echo "${TUNNEL_OUTPUT}" | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -n1)"

if [[ -z "${TUNNEL_ID}" ]]; then
    error "Could not extract tunnel ID from the output above."
    error "Check that the tunnel was created with: cloudflared tunnel list"
    exit 1
fi

success "Tunnel created. Tunnel ID: ${TUNNEL_ID}"

CREDS_FILE="/home/${LINUX_USER}/.cloudflared/${TUNNEL_ID}.json"

# ---------------------------------------------------------------------------
# Step 4 – Create the DNS CNAME Record
# ---------------------------------------------------------------------------
step "Step 4 – Create the DNS CNAME Record"

info "Creating CNAME: ${BOT_API_SUBDOMAIN} → ${TUNNEL_ID}.cfargotunnel.com"
cloudflared tunnel route dns "${TUNNEL_NAME}" "${BOT_API_SUBDOMAIN}"
success "DNS CNAME record created (or already exists)."
info "Verify in Cloudflare dashboard: DNS → Records → look for '${BOT_API_SUBDOMAIN}'."

# ---------------------------------------------------------------------------
# Step 5 – Create the Config File
# ---------------------------------------------------------------------------
step "Step 5 – Create ~/.cloudflared/config.yml"

CONFIG_FILE="${HOME}/.cloudflared/config.yml"

if [[ -f "${CONFIG_FILE}" ]]; then
    info "${HOME}/.cloudflared/config.yml already exists."
    read -r -p "  Overwrite it? [y/N]: " OVERWRITE
    OVERWRITE="${OVERWRITE:-N}"
    if [[ ! "${OVERWRITE}" =~ ^[Yy]$ ]]; then
        info "Skipping config.yml — using existing file."
    else
        _WRITE_CONFIG=true
    fi
else
    _WRITE_CONFIG=true
fi

if [[ "${_WRITE_CONFIG:-false}" == "true" ]]; then
    mkdir -p "${HOME}/.cloudflared"
    cat > "${CONFIG_FILE}" <<EOF
tunnel: ${TUNNEL_ID}
credentials-file: ${CREDS_FILE}

ingress:
  - hostname: ${BOT_API_SUBDOMAIN}
    service: http://localhost:${FLASK_PORT}
  - service: http_status:404
EOF
    success "Config written to ${CONFIG_FILE}"
fi

# Secure the credentials file
if ls "${HOME}/.cloudflared/"*.json > /dev/null 2>&1; then
    chmod 600 "${HOME}/.cloudflared/"*.json
    success "Credentials file permissions set to 600."
fi

# ---------------------------------------------------------------------------
# Step 6 – Update systemd dashboard CORS drop-in
# ---------------------------------------------------------------------------
step "Step 6 – Enable CORS on the Flask Backend (systemd drop-in)"

DROPIN_DIR="/etc/systemd/system/dashboard.service.d"
DROPIN_FILE="${DROPIN_DIR}/cors.conf"

info "Writing CORS drop-in override to ${DROPIN_FILE}..."
sudo mkdir -p "${DROPIN_DIR}"

sudo tee "${DROPIN_FILE}" > /dev/null <<EOF
[Service]
Environment="CHAT_CORS_ORIGIN=${CORS_ORIGIN}"
EOF

info "Reloading systemd and restarting dashboard service..."
sudo systemctl daemon-reload
sudo systemctl restart dashboard || info "Note: 'dashboard' service not found or not running — skipping restart."

success "CORS drop-in written: CHAT_CORS_ORIGIN=${CORS_ORIGIN}"

# ---------------------------------------------------------------------------
# Step 7 (test) is printed in the summary below
# Step 8 – Install cloudflared as a systemd service (Autostart)
# ---------------------------------------------------------------------------
step "Step 8 – Install cloudflared as a systemd Service (Autostart)"

info "Installing cloudflared system service..."
sudo cloudflared --config "${CONFIG_FILE}" service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
success "cloudflared service installed, enabled, and started."

# ---------------------------------------------------------------------------
# Final Summary
# ---------------------------------------------------------------------------
step "Setup Complete – Summary"

echo ""
echo -e "${GREEN}  Configured values:${RESET}"
echo "    Tunnel name         : ${TUNNEL_NAME}"
echo "    Tunnel ID           : ${TUNNEL_ID}"
echo "    Credentials file    : ${CREDS_FILE}"
echo "    Config file         : ${CONFIG_FILE}"
echo "    Bot API subdomain   : https://${BOT_API_SUBDOMAIN}"
echo "    CORS origin         : ${CORS_ORIGIN}"
echo "    Flask port          : ${FLASK_PORT}"
echo ""
echo -e "${GREEN}  DNS records now in effect:${RESET}"
echo "    CNAME  ${BOT_API_SUBDOMAIN}  →  ${TUNNEL_ID}.cfargotunnel.com"
echo ""
echo -e "${GREEN}  Useful commands:${RESET}"
echo "    Check tunnel status  : sudo systemctl status cloudflared"
echo "    View tunnel logs     : journalctl -u cloudflared -f"
echo "    Check dashboard CORS : sudo systemctl show dashboard | grep CHAT_CORS"
echo "    List all tunnels     : cloudflared tunnel list"
echo ""
echo -e "${GREEN}  Step 7 – Verify the tunnel with curl:${RESET}"
echo ""
echo "    curl -i -X OPTIONS https://${BOT_API_SUBDOMAIN}/chat \\"
echo "      -H \"Origin: ${CORS_ORIGIN}\" \\"
echo "      -H \"Access-Control-Request-Method: POST\""
echo ""
echo "    # or send a test message:"
echo "    curl -X POST https://${BOT_API_SUBDOMAIN}/chat \\"
echo "      -H \"Content-Type: application/json\" \\"
echo "      -d '{\"message\":\"hello\",\"user_id\":\"00000000-0000-0000-0000-000000000001\"}'"
echo ""
success "All done! The Cloudflare Tunnel for '${TUNNEL_NAME}' is up and running."
echo ""
