# Cloudflare Tunnel Setup Guide

This guide explains how to expose the MCBOT dashboard and `/chat` API endpoint to the public internet using a **Cloudflare Tunnel** (`cloudflared`).  The tunnel connects your local bot server to a subdomain on your Cloudflare-managed domain without opening any inbound firewall ports.

---

## Architecture Overview

```
[ Browser / cPanel website (chat.html) ]
              │  HTTPS (Cloudflare CDN)
              ▼
[ Cloudflare Edge Network ]
              │  Encrypted tunnel
              ▼
[ cloudflared daemon (local bot server) ]
              │  HTTP (localhost)
              ▼
[ MCBOT dashboard (Flask, port 5000) ]
```

The `cloudflared` daemon runs on the same machine as the bot.  It dials **out** to Cloudflare—no inbound ports need to be opened in your router or firewall.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| MCBOT dashboard running | `python -m dashboard.app` on the bot machine |
| Cloudflare account | Free plan is sufficient |
| Domain managed by Cloudflare | DNS nameservers must point to Cloudflare |
| Chosen subdomain | e.g. `bot.yourdomain.com` |

---

## Step 1 – Install cloudflared

### Debian / Ubuntu / Raspberry Pi OS

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

## Step 2 – Authenticate with Cloudflare

```bash
cloudflared tunnel login
```

A browser window will open.  Log in to your Cloudflare account and select the domain you want to use (e.g. `yourdomain.com`).  A certificate file is saved to `~/.cloudflared/cert.pem`.

---

## Step 3 – Create a Named Tunnel

```bash
cloudflared tunnel create mcbot-tunnel
```

This command outputs a **tunnel ID** (a UUID) and creates a credentials file at:

```
~/.cloudflared/<TUNNEL-ID>.json
```

Keep note of the tunnel ID – you will need it in the config file.

---

## Step 4 – Create the DNS Record

Route your chosen subdomain through the tunnel:

```bash
cloudflared tunnel route dns mcbot-tunnel bot.yourdomain.com
```

This adds a `CNAME` record in Cloudflare DNS pointing `bot.yourdomain.com` to your tunnel.  The record is managed automatically; you do not need to touch it again.

---

## Step 5 – Create the Config File

Create (or edit) the config file at `~/.cloudflared/config.yml`:

```yaml
tunnel: <TUNNEL-ID>
credentials-file: /home/<YOUR-USER>/.cloudflared/<TUNNEL-ID>.json

ingress:
  - hostname: bot.yourdomain.com
    service: http://localhost:5000
  - service: http_status:404
```

Replace:
- `<TUNNEL-ID>` with the UUID from Step 3
- `<YOUR-USER>` with your Linux username
- `bot.yourdomain.com` with your chosen subdomain

The second `ingress` rule (`http_status:404`) is required as a catch-all and must be the last entry.

---

## Step 6 – Test the Tunnel

Start the tunnel in the foreground to verify it works:

```bash
cloudflared tunnel run mcbot-tunnel
```

You should see output like:

```
INF Starting tunnel tunnelID=<TUNNEL-ID>
INF Registered tunnel connection connIndex=0
```

Visit `https://bot.yourdomain.com/chat` from a browser — you should receive a JSON response (or a method-not-allowed error if you open it in a browser, since `/chat` expects a POST).

Visit `https://bot.yourdomain.com/dashboard/` to confirm the MCBOT dashboard loads correctly.

Press **Ctrl+C** to stop the test run.

---

## Step 7 – Run as a systemd Service (Autostart)

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

## Step 8 – Configure the Chat Page

Edit `website/chat.html` and set the `CHAT_API_BASE` constant (near the bottom of the file, inside the `<script>` block) to your tunnel URL:

```js
// Before (default – same-origin):
const CHAT_API_BASE = "";

// After:
const CHAT_API_BASE = "https://bot.yourdomain.com";
```

Upload the updated `chat.html` to your cPanel `public_html` directory.  The chat page will now send requests to your local bot via the Cloudflare Tunnel.

To restrict the `/chat` endpoint to requests from your website only (recommended for production), set the `CHAT_CORS_ORIGIN` environment variable on the bot machine before starting the dashboard:

```bash
export CHAT_CORS_ORIGIN="https://www.yourdomain.com"
python -m dashboard.app
```

Or add it to the systemd service file (see `/etc/systemd/system/dashboard.service`).

---

## Security Considerations

### Rate Limiting

Protect the `/chat` endpoint from abuse by adding a Cloudflare rate-limiting rule:

1. In the Cloudflare dashboard, go to **Security → WAF → Rate Limiting Rules**.
2. Create a rule that limits requests matching `URI Path equals /chat` to a reasonable rate, e.g. **20 requests per minute per IP**.

### Restrict Access (Optional)

If you want to limit access to the chat page to specific users or teams, enable **Cloudflare Access** on the subdomain:

1. In **Zero Trust → Access → Applications**, add an application for `bot.yourdomain.com`.
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
| `502 Bad Gateway` in browser | Dashboard is not running on port 5000 | Start the dashboard: `python -m dashboard.app` |
| CORS error in browser console | `CHAT_API_BASE` not set in `chat.html` | Set it to the full tunnel URL |
| Tunnel stops after logout | Service not installed | Run `sudo cloudflared service install` |
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
