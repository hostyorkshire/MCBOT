# Cloudflare Tunnel Setup Guide

This guide explains how to expose the MCBOT dashboard and `/chat` API endpoint to the public internet using a **Cloudflare Tunnel** (`cloudflared`).  The tunnel connects your local bot server to a subdomain on your Cloudflare-managed domain without opening any inbound firewall ports.

This guide is written for the reference setup used by this project:

| Component | Location / URL |
|---|---|
| Bot backend (Pi Zero 2W) | tunnelled to `https://bot.intergalactic.it.com` |
| Website (cPanel) | served at `https://adv.intergalactic.it.com` |

Substitute your own domain names wherever these appear.

---

## Architecture Overview

```
[ Browser visiting https://adv.intergalactic.it.com/chat.html ]
               │  HTTPS POST to https://bot.intergalactic.it.com/chat
               ▼
[ Cloudflare Edge Network ]
               │  Encrypted outbound tunnel
               ▼
[ cloudflared daemon on Raspberry Pi Zero 2W ]
               │  HTTP (localhost:5000)
               ▼
[ MCBOT Flask app (python -m dashboard.app) ]
```

The `cloudflared` daemon runs on the same machine as the bot.  It dials **out** to Cloudflare—no inbound ports need to be opened in your router or firewall.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| MCBOT dashboard running | `python -m dashboard.app` on the Pi |
| Cloudflare account | Free plan is sufficient |
| Domain managed by Cloudflare | DNS nameservers must point to Cloudflare |
| Chosen bot subdomain | e.g. `bot.intergalactic.it.com` |

---

## Part A – Pi Zero 2W (Bot Backend)

### Step 1 – Install cloudflared

Visit the [cloudflared releases page](https://github.com/cloudflare/cloudflared/releases/latest) and download the `.deb` package for your architecture:

- **Raspberry Pi Zero 2W (64-bit OS):** `cloudflared-linux-arm64.deb`
- **Standard 64-bit Linux PC:** `cloudflared-linux-amd64.deb`

Then install it:

```bash
sudo dpkg -i cloudflared-linux-<arch>.deb
```

Replace `<arch>` with your architecture (`arm64` or `amd64`).

### macOS
```bash
brew install cloudflare/cloudflare/cloudflared
```

### Windows
Download the installer from the [Cloudflare Tunnel installation page](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/).

### Verify the installation
```bash
cloudflared --version
```

---

### Step 2 – Authenticate with Cloudflare

```bash
cloudflared tunnel login
```

A browser window will open.  Log in to your Cloudflare account and select the domain you want to use (e.g. `intergalactic.it.com`).  A certificate file is saved to `~/.cloudflared/cert.pem`.

---

### Step 3 – Create a Named Tunnel

```bash
cloudflared tunnel create mcbot-tunnel
```

This command outputs a **tunnel ID** (a UUID) and creates a credentials file at:

```
~/.cloudflared/<TUNNEL-ID>.json
```

Keep note of the tunnel ID – you will need it in the config file.

---

### Step 4 – Create the DNS Record

Route your chosen subdomain through the tunnel:

```bash
cloudflared tunnel route dns mcbot-tunnel bot.intergalactic.it.com
```

This adds a `CNAME` record in Cloudflare DNS pointing `bot.intergalactic.it.com` to your tunnel.  The record is managed automatically; you do not need to touch it again.

---

### Step 5 – Create the Config File

Create (or edit) the config file at `~/.cloudflared/config.yml`:

```yaml
tunnel: <TUNNEL-ID>
credentials-file: /home/<YOUR-USER>/.cloudflared/<TUNNEL-ID>.json

ingress:
  - hostname: bot.intergalactic.it.com
    service: http://localhost:5000
  - service: http_status:404
```

Replace:
- `<TUNNEL-ID>` with the UUID from Step 3
- `<YOUR-USER>` with your Linux username (e.g. `pi`)

The second `ingress` rule (`http_status:404`) is required as a catch-all and must be the last entry.

---

### Step 6 – Enable CORS on the Flask Backend

Because `chat.html` is served from `https://adv.intergalactic.it.com` and posts to `https://bot.intergalactic.it.com`, the browser enforces Cross-Origin Resource Sharing (CORS).  The MCBOT Flask app reads the allowed origin from the `CHAT_CORS_ORIGIN` environment variable.

Set this variable before starting the dashboard:

```bash
export CHAT_CORS_ORIGIN="https://adv.intergalactic.it.com"
python -m dashboard.app
```

Or add it permanently to the systemd service override so it survives reboots.  Edit `/etc/systemd/system/dashboard.service` (or create a drop-in override):

```ini
[Service]
Environment="CHAT_CORS_ORIGIN=https://adv.intergalactic.it.com"
```

Then reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart dashboard
```

---

### Step 7 – Test the Tunnel and CORS

Start the tunnel in the foreground to verify it works:

```bash
cloudflared tunnel run mcbot-tunnel
```

You should see output like:

```
INF Starting tunnel tunnelID=<TUNNEL-ID>
INF Registered tunnel connection connIndex=0
```

Confirm the `/chat` endpoint is reachable and returns CORS headers:

```bash
curl -i -X OPTIONS https://bot.intergalactic.it.com/chat \
  -H "Origin: https://adv.intergalactic.it.com" \
  -H "Access-Control-Request-Method: POST"
```

The response headers should include:

```
Access-Control-Allow-Origin: https://adv.intergalactic.it.com
Access-Control-Allow-Methods: POST, OPTIONS
```

Press **Ctrl+C** to stop the test run.

---

### Step 8 – Run as a systemd Service (Autostart)

Install `cloudflared` as a system service so that it starts automatically on boot:

```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

Check the status:

```bash
sudo systemctl status cloudflared
```

To view logs:

```bash
journalctl -u cloudflared -f
```

---

## Part B – cPanel Website

### Step 9 – Verify chat.html Is Configured for the Production API

The file `website/chat.html` in this repository is already configured for the production bot API:

```js
const CHAT_API_BASE = "https://bot.intergalactic.it.com";
```

If you need to point it at a different host, edit that constant before uploading.

### Step 10 – Upload Files to cPanel

1. Log in to your cPanel account for `adv.intergalactic.it.com`.
2. Open **File Manager** and navigate to `public_html/` (or a subdirectory if preferred).
3. Upload the following files from the `website/` directory of this repository:
   - `index.html`
   - `chat.html`
   - `qr.png`
4. Confirm the files are accessible:
   - `https://adv.intergalactic.it.com/` – landing page with QR code and web-chat button
   - `https://adv.intergalactic.it.com/chat.html` – interactive chat page

No build step is required.  These are plain HTML/CSS/JS files.

---

## End-to-End Test

Once both parts are complete, run a full integration test:

1. Confirm the bot is running on the Pi:
   ```bash
   curl -X POST https://bot.intergalactic.it.com/chat \
     -H "Content-Type: application/json" \
     -d '{"message":"hello","user_id":"00000000-0000-0000-0000-000000000001"}'
   ```
   Expected response: `{"reply": "..."}` with a StoryBoT message.

2. Open `https://adv.intergalactic.it.com/chat.html` in a browser.
3. Enter a name when prompted.
4. Send a message and confirm you receive a reply from the bot.

---

## Security Considerations

### Cloudflare Tunnel

Cloudflare Tunnel establishes an **outbound-only** encrypted connection from the Pi to Cloudflare.  No inbound ports need to be opened on your router or firewall.  All traffic passes through Cloudflare's global network, including DDoS protection and TLS termination.

### CORS

The `CHAT_CORS_ORIGIN` environment variable restricts which website is permitted to call the `/chat` endpoint.  Setting it to `https://adv.intergalactic.it.com` means browsers will reject cross-origin requests from any other origin.  Do not leave it as `*` in production.

### Rate Limiting

Protect the `/chat` endpoint from abuse by adding a Cloudflare rate-limiting rule:

1. In the Cloudflare dashboard, go to **Security → WAF → Rate Limiting Rules**.
2. Create a rule that limits requests matching `URI Path equals /chat` to a reasonable rate, e.g. **20 requests per minute per IP**.

### Restrict Access (Optional)

If you want to limit access to the chat page to specific users or teams, enable **Cloudflare Access** on the subdomain:

1. In **Zero Trust → Access → Applications**, add an application for `bot.intergalactic.it.com`.
2. Define a policy (e.g. email allowlist, or one-time PIN).

### Tunnel Credentials Security

Keep your tunnel credentials file secure:

```bash
chmod 600 ~/.cloudflared/*.json
```

Never commit the credentials file or `cert.pem` to version control.

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| `tunnel not found` error | Wrong tunnel ID in `config.yml` | Run `cloudflared tunnel list` to find the correct ID |
| DNS record missing | Route command not run | Re-run `cloudflared tunnel route dns …` |
| `502 Bad Gateway` in browser | Dashboard not running on port 5000 | Start the dashboard: `python -m dashboard.app` |
| CORS error in browser console | `CHAT_CORS_ORIGIN` not set or wrong domain | Set `CHAT_CORS_ORIGIN=https://adv.intergalactic.it.com` and restart the bot |
| Chat page shows "Could not reach the bot" | `CHAT_API_BASE` wrong or tunnel is down | Verify `CHAT_API_BASE` in `chat.html` and check `sudo systemctl status cloudflared` |
| Tunnel stops after logout | Service not installed as systemd unit | Run `sudo cloudflared service install` |
| `cloudflared` not found after install | PATH issue | Use the full path `/usr/bin/cloudflared` or re-open your terminal |

---

## Quick-Reference Commands

```bash
# List all tunnels
cloudflared tunnel list

# Start the tunnel (foreground)
cloudflared tunnel run mcbot-tunnel

# Check systemd service
sudo systemctl status cloudflared

# View live logs
journalctl -u cloudflared -f

# Delete the tunnel (irreversible)
cloudflared tunnel delete mcbot-tunnel
```

---

## Related Documentation

- [Cloudflare Tunnel official docs](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/)
- [cloudflared GitHub releases](https://github.com/cloudflare/cloudflared/releases)
- [MCBOT Dashboard README](../dashboard/README.md)
- [Website README](../website/README.md)
