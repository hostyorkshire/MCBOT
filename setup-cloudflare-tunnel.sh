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

warn_box() {
    echo ""
    echo -e "${YELLOW}  ┌─────────────────────────────────────────────────────────────────┐${RESET}"
    while IFS= read -r line; do
        printf "${YELLOW}  │  %-63s│${RESET}\n" "$line"
    done <<< "$1"
    echo -e "${YELLOW}  └─────────────────────────────────────────────────────────────────┘${RESET}"
    echo ""
}

pause_and_confirm() {
    echo -e "${YELLOW}  Press Enter to continue, or Ctrl+C to abort...${RESET}"
    read -r _PAUSE_INPUT
}

_STEP_TOTAL=8

step() {
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    case "$1" in
        Step\ [0-9]*)
            _STEPN="${1#Step }"
            _STEPN="${_STEPN%% –*}"
            echo -e "${YELLOW}  [ Step ${_STEPN} of ${_STEP_TOTAL} ] $*${RESET}"
            ;;
        *)
            echo -e "${YELLOW}  $*${RESET}"
            ;;
    esac
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
# Short label used in manual DNS instructions (e.g. "apistorybot" from "apistorybot.example.com")
BOT_API_NAME="${BOT_API_SUBDOMAIN%%.*}"

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

CERT_FILE="${HOME}/.cloudflared/cert.pem"

if [[ -f "${CERT_FILE}" ]]; then
    echo ""
    echo -e "${YELLOW}  ┌─────────────────────────────────────────────────────────────────┐${RESET}"
    echo -e "${YELLOW}  │  ⚠  EXISTING CERTIFICATE DETECTED                              │${RESET}"
    echo -e "${YELLOW}  │                                                                 │${RESET}"
    echo -e "${YELLOW}  │  A certificate already exists at:                              │${RESET}"
    echo -e "${YELLOW}  │    ~/.cloudflared/cert.pem                                     │${RESET}"
    echo -e "${YELLOW}  │                                                                 │${RESET}"
    echo -e "${YELLOW}  │  If this is a re-run, the old cert may belong to a stale       │${RESET}"
    echo -e "${YELLOW}  │  tunnel.  Before deleting it locally, clean up Cloudflare:     │${RESET}"
    echo -e "${YELLOW}  │                                                                 │${RESET}"
    echo -e "${YELLOW}  │    1. Go to https://dash.cloudflare.com/                       │${RESET}"
    echo -e "${YELLOW}  │    2. Open Zero Trust → Networks → Tunnels                     │${RESET}"
    echo -e "${YELLOW}  │    3. Delete any old or stale tunnel entries there first.      │${RESET}"
    echo -e "${YELLOW}  │    4. Then return here and answer 'y' below to delete the      │${RESET}"
    echo -e "${YELLOW}  │       local cert.pem and re-authenticate.                      │${RESET}"
    echo -e "${YELLOW}  │                                                                 │${RESET}"
    echo -e "${YELLOW}  │  If you answer 'n', the existing certificate will be used      │${RESET}"
    echo -e "${YELLOW}  │  and the login step will be skipped.                           │${RESET}"
    echo -e "${YELLOW}  └─────────────────────────────────────────────────────────────────┘${RESET}"
    echo ""

    read -r -p "  Do you want to delete the existing cert.pem and re-authenticate? [y/N]: " REAUTH
    REAUTH="${REAUTH:-N}"
    if [[ "${REAUTH}" =~ ^[Yy]$ ]]; then
        rm -f "${CERT_FILE}"
        info "Deleted ${CERT_FILE}. Proceeding with authentication..."
        cloudflared tunnel login
        success "Authentication complete. Credentials saved to ~/.cloudflared/cert.pem"
    else
        info "Keeping existing certificate. Skipping login step."
        success "Using existing credentials at ${CERT_FILE}."
    fi
else
    cloudflared tunnel login
    success "Authentication complete. Credentials saved to ~/.cloudflared/cert.pem"
fi

# ---------------------------------------------------------------------------
# Step 3 – Create a Named Tunnel
# ---------------------------------------------------------------------------
step "Step 3 – Create a Named Tunnel"
pause_and_confirm

# Check if tunnel already exists
TUNNEL_ID=""
if cloudflared tunnel list 2>/dev/null | grep -q "${TUNNEL_NAME}"; then
    warn_box "⚠  TUNNEL ALREADY EXISTS

A tunnel named '${TUNNEL_NAME}' already exists in your
Cloudflare account.

This usually means you have run this script before.

Options:
  Y  – Use the existing tunnel (recommended for re-runs)
  N  – You will then be asked if you want to delete it"

    read -r -p "  Use the existing tunnel? [Y/n]: " USE_EXISTING
    USE_EXISTING="${USE_EXISTING:-Y}"
    if [[ "${USE_EXISTING}" =~ ^[Yy]$ ]]; then
        # Try JSON output first; fall back to text grep if python3 unavailable
        TUNNEL_ID="$(cloudflared tunnel list --output json 2>/dev/null \
            | python3 -c "
import sys, json
tunnels = json.load(sys.stdin)
match = next((t['id'] for t in tunnels if t['name'] == '${TUNNEL_NAME}'), '')
print(match)
" 2>/dev/null)" || TUNNEL_ID=""
        if [[ -z "${TUNNEL_ID}" ]]; then
            TUNNEL_ID="$(cloudflared tunnel list 2>/dev/null \
                | grep "${TUNNEL_NAME}" \
                | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' \
                | head -n1)" || TUNNEL_ID=""
        fi
        if [[ -z "${TUNNEL_ID}" ]]; then
            error "Could not determine tunnel ID."
            error "Run 'cloudflared tunnel list' to see your tunnels, then re-run this script."
            exit 1
        fi
        success "Using existing tunnel ID: ${TUNNEL_ID}"
    else
        read -r -p "  Delete it and recreate? [y/N]: " DELETE_AND_RECREATE
        DELETE_AND_RECREATE="${DELETE_AND_RECREATE:-N}"
        if [[ "${DELETE_AND_RECREATE}" =~ ^[Yy]$ ]]; then
            # Capture old tunnel ID before deletion so we can clean up its credential file
            OLD_TUNNEL_ID="$(cloudflared tunnel list --output json 2>/dev/null \
                | python3 -c "
import sys, json
tunnels = json.load(sys.stdin)
for t in tunnels:
    if t.get('name') == '${TUNNEL_NAME}':
        print(t.get('id', ''))
        break
" 2>/dev/null)" || OLD_TUNNEL_ID=""
            if [[ -z "${OLD_TUNNEL_ID}" ]]; then
                OLD_TUNNEL_ID="$(cloudflared tunnel list 2>/dev/null \
                    | grep "${TUNNEL_NAME}" \
                    | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' \
                    | head -n1)" || OLD_TUNNEL_ID=""
            fi
            info "Deleting existing tunnel '${TUNNEL_NAME}'..."
            cloudflared tunnel delete -f "${TUNNEL_NAME}"
            # Remove stale credential file left behind by the deleted tunnel
            if [[ -n "${OLD_TUNNEL_ID}" && -f "/home/${LINUX_USER}/.cloudflared/${OLD_TUNNEL_ID}.json" ]]; then
                info "Removing stale credentials file for old tunnel ${OLD_TUNNEL_ID}..."
                rm -f "/home/${LINUX_USER}/.cloudflared/${OLD_TUNNEL_ID}.json"
            fi
            info "Creating tunnel '${TUNNEL_NAME}'..."
            TUNNEL_OUTPUT="$(cloudflared tunnel create "${TUNNEL_NAME}" 2>&1)"
            echo "${TUNNEL_OUTPUT}"
            TUNNEL_ID="$(echo "${TUNNEL_OUTPUT}" | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -n1)"
            if [[ -z "${TUNNEL_ID}" ]]; then
                error "Could not extract tunnel ID from the output above."
                error "Run 'cloudflared tunnel list' to see your tunnels, then re-run this script."
                exit 1
            fi
            success "Tunnel created. Tunnel ID: ${TUNNEL_ID}"
            TUNNEL_WAS_RECREATED=true
        else
            error "Cannot continue without a tunnel. Aborting."
            error "Run 'cloudflared tunnel list' to review your tunnels and re-run this script."
            exit 1
        fi
    fi
else
    info "Creating tunnel '${TUNNEL_NAME}'..."
    TUNNEL_OUTPUT="$(cloudflared tunnel create "${TUNNEL_NAME}" 2>&1)"
    echo "${TUNNEL_OUTPUT}"
    TUNNEL_ID="$(echo "${TUNNEL_OUTPUT}" | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -n1)"
    if [[ -z "${TUNNEL_ID}" ]]; then
        error "Could not extract tunnel ID from the output above."
        error "Run 'cloudflared tunnel list' to see your tunnels, then re-run this script."
        exit 1
    fi
    success "Tunnel created. Tunnel ID: ${TUNNEL_ID}"
fi

CREDS_FILE="/home/${LINUX_USER}/.cloudflared/${TUNNEL_ID}.json"

# ---------------------------------------------------------------------------
# Step 4 – Create the DNS CNAME Record
# ---------------------------------------------------------------------------
step "Step 4 – Create the DNS CNAME Record"
pause_and_confirm

info "Creating CNAME: ${BOT_API_SUBDOMAIN} → ${TUNNEL_ID}.cfargotunnel.com"
DNS_OUTPUT="$(cloudflared tunnel route dns "${TUNNEL_NAME}" "${BOT_API_SUBDOMAIN}" 2>&1)" && DNS_EXIT=0 || DNS_EXIT=$?
echo "${DNS_OUTPUT}"
if [[ ${DNS_EXIT} -eq 0 ]]; then
    success "DNS CNAME record created automatically."
elif echo "${DNS_OUTPUT}" | grep -qiE 'already exist|record already'; then
    if [[ "${TUNNEL_WAS_RECREATED:-false}" == "true" ]]; then
        # Stale CNAME from the deleted tunnel — try to overwrite it atomically
        info "Stale CNAME detected (tunnel was recreated). Attempting to overwrite..."
        OVERWRITE_OUTPUT="$(cloudflared tunnel route dns --overwrite-dns "${TUNNEL_NAME}" "${BOT_API_SUBDOMAIN}" 2>&1)" && OVERWRITE_EXIT=0 || OVERWRITE_EXIT=$?
        echo "${OVERWRITE_OUTPUT}"
        if [[ ${OVERWRITE_EXIT} -eq 0 ]]; then
            success "Stale CNAME replaced successfully."
        else
            warn_box "⚠  STALE CNAME DETECTED — ACTION REQUIRED

The tunnel was recreated with a new ID, but an old CNAME record
still exists in Cloudflare DNS pointing to the previous tunnel.

Automatic overwrite also failed (your version of cloudflared may
not support --overwrite-dns).

You must delete the old record manually before continuing:
  1. Go to https://dash.cloudflare.com/
  2. Select your domain → DNS → Records
  3. Delete the CNAME named '${BOT_API_NAME}'
     (it points to the OLD tunnel ID, not ${TUNNEL_ID})
  4. Then add a new CNAME record:
       Type   : CNAME
       Name   : ${BOT_API_NAME}
       Target : ${TUNNEL_ID}.cfargotunnel.com
       Proxy  : Enabled (orange cloud ☁  — IMPORTANT)
  5. Click Save"

            read -r -p "  Have you deleted the old CNAME and added the new one? [y/N]: " DNS_STALE_FIXED
            DNS_STALE_FIXED="${DNS_STALE_FIXED:-N}"
            if [[ ! "${DNS_STALE_FIXED}" =~ ^[Yy]$ ]]; then
                error "DNS record must point to the new tunnel ID to continue."
                error "Fix it in the Cloudflare dashboard, then re-run this script."
                exit 1
            fi
            success "Noted — continuing with manually updated DNS."
        fi
    else
        success "DNS CNAME already exists — skipping."
    fi
else
    warn_box "⚠  AUTOMATIC DNS SETUP FAILED

cloudflared could not create the DNS record automatically.
This can happen when the domain's DNS zone is not accessible
via the certificate you authenticated with, or if the zone
is managed by a different Cloudflare account.

You need to add the record manually:

  1. Go to https://dash.cloudflare.com/
  2. Select your domain (the part after the first dot in:
       ${BOT_API_SUBDOMAIN})
  3. Click DNS → Records → + Add record
  4. Fill in exactly:
       Type   : CNAME
       Name   : ${BOT_API_NAME}
       Target : ${TUNNEL_ID}.cfargotunnel.com
       Proxy  : Enabled (orange cloud ☁  — IMPORTANT)
  5. Click Save

The orange cloud (proxied) setting is required for the
tunnel to work. Do NOT set it to DNS-only (grey cloud)."

    read -r -p "  Have you added the DNS record manually in Cloudflare? [y/N]: " DNS_MANUAL
    DNS_MANUAL="${DNS_MANUAL:-N}"
    if [[ ! "${DNS_MANUAL}" =~ ^[Yy]$ ]]; then
        error "DNS record is required to continue."
        error "Add it in the Cloudflare dashboard, then re-run this script."
        exit 1
    fi
    success "Noted — continuing with manually configured DNS."
fi

# Verify the DNS route is visible to cloudflared
info "Verifying DNS route registration with cloudflared..."
CF_TUNNEL_INFO="$(cloudflared tunnel info "${TUNNEL_NAME}" 2>&1)" || CF_TUNNEL_INFO=""
if echo "${CF_TUNNEL_INFO}" | grep -qi "${BOT_API_SUBDOMAIN}"; then
    success "DNS route confirmed via 'cloudflared tunnel info'."
else
    echo ""
    info "Route not yet visible via 'cloudflared tunnel info' — this is normal."
    info "DNS changes can take a few minutes to propagate."
    echo ""
    echo -e "${YELLOW}  To verify manually in the Cloudflare dashboard:${RESET}"
    echo "    1. Go to https://dash.cloudflare.com/"
    echo "    2. Select your domain → DNS → Records"
    echo "    3. Look for a CNAME named '${BOT_API_NAME}'"
    echo "       pointing to '${TUNNEL_ID}.cfargotunnel.com'"
    echo "    4. The Proxy column should show an orange cloud ☁"
    echo ""
fi

# ---------------------------------------------------------------------------
# Step 5 – Create the Config File
# ---------------------------------------------------------------------------
step "Step 5 – Create ~/.cloudflared/config.yml"
pause_and_confirm

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
pause_and_confirm

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
pause_and_confirm

if systemctl list-unit-files 2>/dev/null | grep -q cloudflared \
   || ls /etc/systemd/system/cloudflared* >/dev/null 2>&1; then
    warn_box "⚠  CLOUDFLARED SERVICE ALREADY INSTALLED

The cloudflared systemd service already exists.

Options:
  Y  – Re-install / overwrite (will restart service)
  N  – Skip install, just restart the existing service"

    read -r -p "  Re-install the service? [y/N]: " REINSTALL_SERVICE
    REINSTALL_SERVICE="${REINSTALL_SERVICE:-N}"
    if [[ "${REINSTALL_SERVICE}" =~ ^[Yy]$ ]]; then
        info "Uninstalling existing cloudflared system service..."
        sudo cloudflared service uninstall || true
        sudo systemctl daemon-reload
        info "Re-installing cloudflared system service..."
        sudo mkdir -p /etc/cloudflared
        sudo cp "${CONFIG_FILE}" /etc/cloudflared/config.yml
        sudo cloudflared --config /etc/cloudflared/config.yml service install
        sudo systemctl enable cloudflared
        sudo systemctl restart cloudflared
        success "cloudflared service re-installed, enabled, and restarted."
    else
        info "Skipping service install — restarting existing service..."
        sudo systemctl restart cloudflared
        sudo systemctl enable cloudflared
        success "cloudflared service restarted and enabled."
    fi
else
    info "Installing cloudflared system service..."
    # Clean up any leftover cloudflared service files that systemctl missed
    sudo cloudflared service uninstall 2>/dev/null || true
    sudo systemctl daemon-reload
    sudo mkdir -p /etc/cloudflared
    sudo cp "${CONFIG_FILE}" /etc/cloudflared/config.yml
    sudo cloudflared --config /etc/cloudflared/config.yml service install
    sudo systemctl enable cloudflared
    sudo systemctl start cloudflared
    success "cloudflared service installed, enabled, and started."
fi

# ---------------------------------------------------------------------------
# Health check — give the service a moment to connect
# ---------------------------------------------------------------------------
info "Waiting 4 seconds for cloudflared to initialise..."
sleep 4

if systemctl is-active cloudflared > /dev/null 2>&1; then
    success "cloudflared service is active and running."
    # Show a brief tunnel info snapshot so the user can see connections
    CF_HEALTH="$(cloudflared tunnel info "${TUNNEL_NAME}" 2>&1)" || CF_HEALTH=""
    if [[ -n "${CF_HEALTH}" ]]; then
        echo ""
        echo "${CF_HEALTH}"
        echo ""
    fi
    echo -e "${GREEN}  ┌─────────────────────────────────────────────────────────────────┐${RESET}"
    echo -e "${GREEN}  │  ✓  VERIFY IN CLOUDFLARE DASHBOARD                             │${RESET}"
    echo -e "${GREEN}  │                                                                 │${RESET}"
    echo -e "${GREEN}  │  1. Go to https://dash.cloudflare.com/                         │${RESET}"
    echo -e "${GREEN}  │  2. Open Zero Trust → Networks → Tunnels                       │${RESET}"
    echo -e "${GREEN}  │  3. Find your tunnel — Status should be HEALTHY                │${RESET}"
    echo -e "${GREEN}  │     (a green dot next to the tunnel name)                      │${RESET}"
    echo -e "${GREEN}  │  4. Click the tunnel name → check the Connectors tab           │${RESET}"
    echo -e "${GREEN}  │     — you should see at least one active connection             │${RESET}"
    echo -e "${GREEN}  └─────────────────────────────────────────────────────────────────┘${RESET}"
    echo -e "${GREEN}     Tunnel name: ${TUNNEL_NAME}${RESET}"
    echo ""
else
    echo ""
    echo -e "${RED}  ┌─────────────────────────────────────────────────────────────────┐${RESET}"
    echo -e "${RED}  │  ✗  CLOUDFLARED SERVICE FAILED TO START                        │${RESET}"
    echo -e "${RED}  │                                                                 │${RESET}"
    echo -e "${RED}  │  The service is not running. To diagnose:                      │${RESET}"
    echo -e "${RED}  │                                                                 │${RESET}"
    echo -e "${RED}  │    sudo systemctl status cloudflared                           │${RESET}"
    echo -e "${RED}  │    journalctl -u cloudflared -n 50 --no-pager                  │${RESET}"
    echo -e "${RED}  │                                                                 │${RESET}"
    echo -e "${RED}  │  Common causes:                                                │${RESET}"
    echo -e "${RED}  │    • Config file missing or wrong tunnel ID                    │${RESET}"
    echo -e "${RED}  │    • Credentials (.json) file missing or unreadable            │${RESET}"
    echo -e "${RED}  │    • Tunnel ID mismatch  →  run: cloudflared tunnel list       │${RESET}"
    echo -e "${RED}  │                                                                 │${RESET}"
    echo -e "${RED}  │  In Cloudflare dashboard the tunnel will show as INACTIVE:     │${RESET}"
    echo -e "${RED}  │    https://dash.cloudflare.com/ → Zero Trust →                 │${RESET}"
    echo -e "${RED}  │    Networks → Tunnels → find your tunnel name                  │${RESET}"
    echo -e "${RED}  └─────────────────────────────────────────────────────────────────┘${RESET}"
    echo -e "${RED}     Tunnel name: ${TUNNEL_NAME}${RESET}"
    echo ""
    error "Config file    : ${CONFIG_FILE}"
    error "Credentials    : ${CREDS_FILE}"
    error "Fix the issue above and re-run: sudo systemctl start cloudflared"
fi

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

# ---------------------------------------------------------------------------
# Cloudflare dashboard verification checklist
# ---------------------------------------------------------------------------
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${GREEN}  Cloudflare Dashboard Verification Checklist${RESET}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""
echo -e "${GREEN}  Open https://dash.cloudflare.com/ and check each item below.${RESET}"
echo ""
echo -e "${GREEN}  1. Tunnel health${RESET}"
echo "     Path  : Zero Trust → Networks → Tunnels"
echo "     Look  : '${TUNNEL_NAME}' should show a green HEALTHY status"
echo "     Note  : If it shows INACTIVE, the service is not running on this Pi."
echo "             Run: sudo systemctl status cloudflared"
echo ""
echo -e "${GREEN}  2. DNS record${RESET}"
echo "     Path  : Your domain → DNS → Records"
echo "     Look  : A CNAME named '${BOT_API_NAME}'"
echo "             pointing to '${TUNNEL_ID}.cfargotunnel.com'"
echo "             with an orange cloud (Proxied)"
echo "     Note  : If missing, run on this Pi:"
echo "             cloudflared tunnel route dns ${TUNNEL_NAME} ${BOT_API_SUBDOMAIN}"
echo "             or add it manually as described above."
echo ""
echo -e "${GREEN}  3. Tunnel connectors${RESET}"
echo "     Path  : Zero Trust → Networks → Tunnels → click '${TUNNEL_NAME}'"
echo "     Look  : Under the Connectors tab — at least one connector"
echo "             should appear with a green dot"
echo "     Note  : If no connectors, check logs:"
echo "             journalctl -u cloudflared -n 50 --no-pager"
echo ""
echo -e "${GREEN}  4. Public hostname (optional check)${RESET}"
echo "     Path  : Zero Trust → Networks → Tunnels → '${TUNNEL_NAME}' → Public Hostnames"
echo "     Look  : '${BOT_API_SUBDOMAIN}' → http://localhost:${FLASK_PORT}"
echo "     Note  : The script uses a config file, so this tab may be empty —"
echo "             that is normal. The curl test above is the definitive check."
echo ""

# ---------------------------------------------------------------------------
# How it all connects – Pi dependency explained
# ---------------------------------------------------------------------------
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${GREEN}  Will the chat work on the website now?${RESET}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""
echo -e "${GREEN}  YES — once the Pi is on and the service is running, the full path is:${RESET}"
echo ""
echo "    Browser (${CORS_ORIGIN})"
echo "       ↓  sends chat messages to https://${BOT_API_SUBDOMAIN}"
echo "    Cloudflare Edge (DNS / CNAME)"
echo "       ↓  routes traffic through the encrypted tunnel"
echo "    cloudflared on this Pi  (systemd service, auto-starts on boot)"
echo "       ↓  forwards requests to localhost:${FLASK_PORT}"
echo "    Flask bot app on this Pi"
echo "       ↓  generates a response and sends it back up the same path"
echo "    Browser receives the reply"
echo ""
echo -e "${YELLOW}  ⚠  The Pi Zero MUST be powered on and connected to the internet.${RESET}"
echo -e "${YELLOW}     If the Pi is off or the service stops, the website chat will return${RESET}"
echo -e "${YELLOW}     a '502 Bad Gateway' or 'Failed to fetch' error — because there is${RESET}"
echo -e "${YELLOW}     nothing on the other end of the tunnel to answer requests.${RESET}"
echo ""
echo -e "${GREEN}  Keeping it running reliably:${RESET}"
echo "    • The cloudflared service is set to start automatically on Pi boot"
echo "    • To check it is running at any time:"
echo "        sudo systemctl status cloudflared"
echo "    • To check the bot Flask app is running:"
echo "        sudo systemctl status dashboard"
echo "    • To restart both after a crash or config change:"
echo "        sudo systemctl restart cloudflared dashboard"
echo ""
echo -e "${GREEN}  Quick remote health check (run from any computer):${RESET}"
echo ""
echo "    curl -s -o /dev/null -w '%{http_code}' https://${BOT_API_SUBDOMAIN}/chat \\"
echo "      -X POST -H 'Content-Type: application/json' \\"
echo "      -d '{\"message\":\"ping\",\"user_id\":\"00000000-0000-0000-0000-000000000001\"}'"
echo ""
echo "    200 or 400  →  Pi is up and the tunnel is working"
echo "    502 / 503   →  Tunnel is up but Flask app is not running on the Pi"
echo "    000 / curl error  →  Tunnel is down (Pi is off or cloudflared crashed)"
echo ""
success "All done! The Cloudflare Tunnel for '${TUNNEL_NAME}' is up and running."
echo ""
