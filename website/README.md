# MCBOT Website – Deployment Guide

This folder contains a fully **static, dependency-free** one-page website that lets
visitors add the MCBOT node to their MeshCore contacts by scanning a QR code.

---

## Files

| File | Purpose |
|---|---|
| `index.html` | The styled landing page (sticker-blob / 70s lava-lamp theme) |
| `qr.png` | QR code image linking to `http://meshtastic.local/#/connect?add=true` (now included by default) |
| `README.md` | This file |

---

## Serving from a Raspberry Pi with Cloudflare Tunnel

Because the Pi does not have a static public IP address, we use a
**Cloudflare Tunnel** (`cloudflared`) to expose the local web server under a
real domain name – no port-forwarding or static IP required.

### Overview

```
 Internet user
      │
      ▼
 Cloudflare edge  (your domain, e.g. mcbot.example.com)
      │  encrypted tunnel
      ▼
 cloudflared (running on the Pi)
      │  localhost
      ▼
 website_server.py  →  website/index.html
```

### Step 1 – Start the local website server

The repo includes a zero-dependency Python server (`website_server.py`) that
serves the `website/` folder.

```bash
# Quick test (Ctrl-C to stop):
python website_server.py --host 127.0.0.1 --port 8080

# Or run it as a permanent systemd service (see Step 2).
```

Visit `http://localhost:8080/` to confirm the page loads.

### Step 2 – Run the website server as a systemd service (auto-start)

```bash
# The setup.sh script can install this service automatically.
# To install manually:

WORKDIR="$(pwd)"
BOT_USER="$(whoami)"

sudo tee /etc/systemd/system/website.service > /dev/null << UNIT
[Unit]
Description=MCBOT Static Website Server
After=network.target

[Service]
Type=simple
User=${BOT_USER}
WorkingDirectory=${WORKDIR}
Environment=WEBSITE_HOST=127.0.0.1
Environment=WEBSITE_PORT=8080
ExecStart=${WORKDIR}/.venv/bin/python ${WORKDIR}/website_server.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now website.service
sudo systemctl status website.service
```

### Step 3 – Install cloudflared on the Pi

```bash
# Pi Zero / Pi Zero 2W use the 32-bit ARM build:
curl -L \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm \
  -o /tmp/cloudflared
sudo install -m 755 /tmp/cloudflared /usr/local/bin/cloudflared

# Pi 4 / Pi 5 use the 64-bit ARM build:
# curl -L \
#   https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64 \
#   -o /tmp/cloudflared
# sudo install -m 755 /tmp/cloudflared /usr/local/bin/cloudflared

cloudflared --version   # confirm installation
```

### Step 4 – Authenticate with Cloudflare

```bash
cloudflared tunnel login
```

This opens a browser window (or prints a URL to open manually) where you
authorize `cloudflared` to manage your Cloudflare account.  A certificate is
saved to `~/.cloudflared/cert.pem`.

> **Requirement:** your domain must already be added to Cloudflare (nameservers
> pointing at Cloudflare) before this step.

### Step 5 – Create a named tunnel

```bash
cloudflared tunnel create mcbot-website
```

Note the **tunnel UUID** printed (e.g. `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`).

### Step 6 – Configure the tunnel

```bash
sudo mkdir -p /etc/cloudflared

# Copy the example config and edit it:
sudo cp cloudflare/config.yml.example /etc/cloudflared/config.yml
sudo nano /etc/cloudflared/config.yml
```

Replace `<TUNNEL_ID>` and `<YOUR_DOMAIN>` with the real values from Step 5.

Minimal config (also found in `cloudflare/config.yml.example`):

```yaml
tunnel: <TUNNEL_ID>
credentials-file: /home/pi/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: mcbot.example.com
    service: http://127.0.0.1:8080
  - service: http_status:404
```

### Step 7 – Add a DNS record

```bash
cloudflared tunnel route dns mcbot-website mcbot.example.com
```

This creates a `CNAME` record in Cloudflare DNS automatically.

### Step 8 – Install and start cloudflared as a service

```bash
# cloudflared can install its own systemd unit:
sudo cloudflared service install
sudo systemctl start cloudflared
sudo systemctl enable cloudflared
sudo systemctl status cloudflared
```

A reference service file is also provided at `cloudflare/cloudflared.service`.

### Verify

1. `sudo systemctl status website.service` – should be **active (running)**.
2. `sudo systemctl status cloudflared` – should be **active (running)**.
3. Open `https://mcbot.example.com/` in a browser from any device – the MCBOT
   landing page should load over HTTPS with a valid Cloudflare certificate.

---

## Resolving the domain locally on the Pi's LAN

If you want devices on the same local network to resolve the domain to the Pi's
**private IP** (avoiding the round-trip through Cloudflare), add a local DNS
override.

**Option A – /etc/hosts on each device**

```
192.168.x.y   mcbot.example.com
```

**Option B – Pi-hole or dnsmasq local override**

Add to `/etc/dnsmasq.d/mcbot.conf` (or the Pi-hole custom DNS settings):

```
address=/mcbot.example.com/192.168.x.y
```

Then restart dnsmasq: `sudo systemctl restart dnsmasq`

---

## Deploying to cPanel (Shared Hosting)

### Upload to the website root

1. Log in to your cPanel account and open **File Manager**.
2. Navigate to `public_html/`.
3. Upload **both** `index.html` and `qr.png` into `public_html/`.
4. Visit `https://yourdomain.com/` – the page should appear immediately.

### Upload to a subfolder (e.g. `https://yourdomain.com/mcbot/`)

1. In File Manager, create a new folder inside `public_html/`, for example `mcbot/`.
2. Upload `index.html` (and `qr.png` once you have it) into `public_html/mcbot/`.
3. Visit `https://yourdomain.com/mcbot/`.

> **Tip:** You can also use cPanel's **FTP / SFTP** credentials with any FTP client
> (FileZilla, Cyberduck, etc.) instead of the browser-based File Manager.

---

## Adding the QR Code

A `qr.png` is now included in this folder by default. It encodes the URL
`http://meshtastic.local/#/connect?add=true` so mobile users can also tap a
link directly when they cannot scan the QR code.

To replace it with your own QR code:

1. **Generate your QR code** from the MeshCore app or companion firmware:
   - In the MeshCore app go to your node's profile and choose **Share / Export QR**.
   - Save or export the image.
2. **Rename the image** to exactly `qr.png`.
3. **Upload `qr.png`** to the same directory as `index.html`
   (e.g. `public_html/` or `public_html/mcbot/`).
4. Refresh the page – the real QR code will appear.

> The QR code should be scanned with the **MeshCore mobile app**
> (`Contacts → Add Contact → Scan QR`).  Mobile users who cannot scan the
> screen can tap the **"Tap to add this contact"** link shown below the QR image.

---

## No Build Step Required

Everything is plain HTML/CSS/JS.  There is no build tool, no npm, no bundler.
Just copy the files and they work.

---

## Customisation Tips

- **Colours** – all colours are defined as CSS custom properties at the top of
  `index.html`'s `<style>` block (`:root { --blob-orange: …; … }`).  Change
  them to match your branding.
- **Heading text** – edit the `<h1>` and `<p class="tagline">` elements in
  `index.html`.
- **Steps** – the `<ol class="steps">` list can be edited to reflect any local
  instructions or node name.
