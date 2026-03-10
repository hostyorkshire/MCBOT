# MCBOT – MeshCore CYOA Story Bot

A lightweight **Create Your Own Adventure (CYOA)** story bot for
[MeshCore](https://github.com/meshcore-dev/MeshCore) LoRa mesh networks.

Runs on a **Raspberry Pi Zero 2W** connected to a MeshCore LoRa radio via USB
serial.  Story text is generated in real time by the free tier of the
[Groq](https://console.groq.com) cloud LLM API (Llama 3, Mixtral, etc.).

> **MCBOT always runs inside a Python virtual environment (`.venv/`).**  
> The setup wizard (`setup.sh`) creates and manages the venv automatically.  
> Both manual use (`source .venv/bin/activate`) and the systemd service use  
> the venv's Python interpreter so all dependencies are always available —  
> even after a reboot.

---

## Table of Contents

- [Features](#features)
- [Hardware Requirements](#hardware-requirements)
- [Project Layout](#project-layout)
- [Complete Setup Guide](#complete-setup-guide)
- [Gameplay (via MeshCore radio)](#gameplay-via-meshcore-radio)
- [Configuration Reference](#configuration-reference)
- [Python Dependencies](#python-dependencies)
- [Web Dashboard](#web-dashboard)
- [Cloudflare Tunnel (Public HTTPS Access)](#cloudflare-tunnel-public-https-access)
- [Running Tests](#running-tests)
- [Diagnostics and Monitoring (`mcbot_monitor.py`)](#diagnostics-and-monitoring-mcbot_monitorpy)
- [Radio Configuration Tool (`meshcore_radio_config.py`)](#radio-configuration-tool-meshcore_radio_configpy)
- [Running as a systemd Service (Linux / Auto-start on Reboot)](#running-as-a-systemd-service-linux-auto-start-on-reboot)
- [Architecture](#architecture)
- [Story Pacing / Doom System](#story-pacing-doom-system)

---

## Features

- Real-time CYOA storytelling delivered over LoRa mesh radio
- Per-user story sessions with full conversation context
- Automatic message chunking to fit within LoRa packet size limits
- Lightweight asyncio design optimised for Raspberry Pi Zero 2W
- Multi-user support (each radio node gets its own story)
- Graceful API-error handling with user-friendly fallback messages

---

## Hardware Requirements

- Raspberry Pi Zero 2W (or any Linux SBC with Python 3.10+)
- A MeshCore-compatible LoRa radio connected over USB serial
  (e.g. Heltec, LilyGO T-Beam, RAK, or Seeed boards running
  [MeshCore companion firmware](https://github.com/meshcore-dev/MeshCore))

---

## Project Layout

```
MCBOT/
├── cyoa_bot.py                    # Main entry point – MeshCore event loop
├── mcbot_monitor.py               # Diagnostic / monitoring helper (see below)
├── meshcore_radio_config.py       # Radio configuration tool (see below)
├── story_engine.py                # Groq LLM session management
├── utils.py                       # Message chunking helpers
├── requirements.txt               # Python dependencies (includes pyserial)
├── requirements-dev.txt           # Dev/test dependencies (includes psutil)
├── .env.example                   # Configuration template
├── setup.sh                       # Interactive setup wizard (.env, venv, and systemd services)
├── setup-cloudflare-tunnel.sh     # Cloudflare Tunnel setup wizard (see below)
├── dashboard.sh                   # Convenience wrapper – start the web dashboard (created by setup.sh)
├── mcbot.service                  # Systemd unit file template (bot, installed by setup.sh)
├── dashboard.service              # Systemd unit file template (dashboard, installed by setup.sh)
├── pytest.ini                     # Test configuration
├── dashboard/                     # Web dashboard (Flask)
│   ├── app.py
│   ├── requirements.txt           # dashboard-specific deps (flask, flask-socketio)
│   └── …
└── tests/
    ├── test_cyoa_bot.py
    ├── test_story_engine.py
    ├── test_utils.py
    ├── test_mcbot_monitor.py
    └── test_meshcore_radio_config.py
```

---

## Complete Setup Guide

Follow these steps **in order** to get the bot running from scratch.

### Step 1 – Install system packages

On Raspberry Pi OS / Debian, install the required system packages first:

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip git
```

### Step 2 – Clone the repository

```bash
git clone https://github.com/hostyorkshire/MCBOT.git
cd MCBOT
```

> All subsequent commands in this guide assume you are inside the `MCBOT`
> directory. Replace `/home/cyoa/MCBOT` in any examples with the full path
> shown by `pwd` if your username or install location is different.

### Step 3 – Add your user to the `dialout` group

This grants your user read/write access to USB serial devices
(`/dev/ttyUSB0`, `/dev/ttyACM0`, etc.) without needing `sudo` each time.

```bash
sudo usermod -a -G dialout $USER
newgrp dialout      # apply immediately without logging out
```

> Verify it worked: `groups` should list `dialout`.
> A full logout/login is needed for the change to persist across reboots.

### Step 4 – Run the setup wizard

The wizard creates the Python virtual environment, installs all dependencies,
prompts for your configuration values, and optionally installs the systemd
service.

```bash
chmod +x setup.sh
bash setup.sh
```

> **To also install the systemd services in the same run**, the script needs
> root for that step.  When it asks *"Re-run now with sudo?"* answer `y` and
> it will re-exec itself automatically.  Alternatively, start the whole wizard
> with `sudo` from the beginning (use the full path to avoid the
> `sudo: ./setup.sh: command not found` pitfall):
>
> ```bash
> sudo bash "$(pwd)/setup.sh"
> ```

The wizard will:

- Create a Python virtual environment at `.venv/` inside the repo (reused on
  re-runs).
- Install all Python dependencies into that venv:
  - `requirements.txt` – `meshcore`, `groq`, `python-dotenv`, `pyserial`
  - `requirements-dev.txt` – `pytest`, `pytest-asyncio`, `psutil`
  - `dashboard/requirements.txt` – `flask`, `flask-socketio`
- Create `dashboard.sh` in the project root for easy dashboard startup.
- Prompt for each configuration value and write `.env`.
- Optionally install and enable **both** systemd services together so that
  both the bot and the web dashboard start automatically on every reboot:
  - `/etc/systemd/system/mcbot.service` – the CYOA bot
  - `/etc/systemd/system/dashboard.service` – the web dashboard

  Both services are installed automatically with the correct user and paths
  substituted.  `mcbot.service` uses `.venv/bin/python` directly; `dashboard.service`
  runs `bash dashboard/start-dashboard.sh` which auto-detects the venv and
  starts the Flask-SocketIO server.  The wizard runs `systemctl daemon-reload`,
  `systemctl enable`, and `systemctl start` for each service, then prints the
  live `systemctl status` output so you can confirm both are active before you
  leave the terminal.
- Optionally launch `mcbot_monitor.py --info` to verify the setup.

### Step 5 – (Manual alternative to the wizard)

Skip this step if you ran `setup.sh`. Use this path if you prefer to set
everything up by hand:

```bash
# 1. Create the virtual environment
python3 -m venv .venv

# 2. Install all dependencies into the venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -r requirements-dev.txt

# 3. Verify python-dotenv installed correctly
.venv/bin/python -c "from dotenv import load_dotenv; print('python-dotenv OK')"

# 4. Create your .env configuration file
cp .env.example .env
nano .env        # or any text editor – fill in GROQ_API_KEY and SERIAL_PORT at minimum
```

Minimum required values in `.env`:

| Variable | Description |
|---|---|
| `GROQ_API_KEY` | Free API key from [console.groq.com](https://console.groq.com) |
| `SERIAL_PORT` | USB serial device path, e.g. `/dev/ttyUSB0` |

### Step 6 – Run the bot

```bash
# Run directly using the venv Python (no activation needed)
.venv/bin/python cyoa_bot.py

# Override the serial port or baud rate on the command line
.venv/bin/python cyoa_bot.py --port /dev/ttyACM0
.venv/bin/python cyoa_bot.py --port /dev/ttyUSB1 --baud 9600
```

CLI flags (`--port`, `--baud`) take precedence over environment variables.
Run `.venv/bin/python cyoa_bot.py --help` for the full option list.

Press **Ctrl+C** to stop. To run in the background with automatic restart on
reboot, see the [systemd service section](#running-as-a-systemd-service-linux--auto-start-on-reboot) below.

---

## Gameplay (via MeshCore radio)

Send a direct message to the bot node from any MeshCore client. On first contact
(any message with no active session), the bot replies with an introductory greeting.
Type `start` (or another command) to begin:

| Message | Action |
|---|---|
| `start` / `new` / `begin` | Start a new adventure (defaults to post‑apoc sci‑fi survival) |
| `start <genre\|#>` | Start with a specific genre (e.g. `start horror` or `start 3`) |
| `genres` | List available genres with their numbers |
| `1`, `2`, or `3` | Choose the numbered story option |
| `restart` / `reset` | Reset your current story and start fresh |
| `help` / `?` | Show command reference |

**Available genres:** `wasteland` (default) · `cozy` · `horror` · `mil` · `comedy`

Commands are also accepted with a leading `/`, `!`, or `\` prefix (e.g. `/start horror`,
`!genres`). Any other text while a story is active is treated as free-text input
to the story engine.

---

## Configuration Reference

All settings are loaded from the `.env` file in the project directory:

| Variable | Default | Description |
|---|---|---|
| `GROQ_API_KEY` | *(required)* | Groq API key |
| `GROQ_MODEL` | `llama-3.1-8b-instant` | Model name (see [Groq models](https://console.groq.com/docs/models)) |
| `SERIAL_PORT` | `/dev/ttyUSB0` | Serial device path |
| `BAUD_RATE` | `115200` | Serial baud rate |
| `MAX_CHUNK_SIZE` | `200` | Max characters per LoRa message chunk |
| `CHUNK_DELAY` | `2.0` | Seconds between consecutive message chunks |
| `MAX_HISTORY` | `10` | Max conversation turns kept per user (RAM limit) |

> **Note:** The old `llama3-8b-8192` model has been decommissioned by Groq.
> The default is now `llama-3.1-8b-instant`.  If you have `GROQ_MODEL=llama3-8b-8192`
> in your `.env`, update it and restart the service.  See
> [Groq deprecation docs](https://console.groq.com/docs/deprecations) for the
> current model list.

`SERIAL_PORT` and `BAUD_RATE` can also be overridden at runtime with the
`--port` and `--baud` CLI flags (the flags take precedence over env vars):

```bash
.venv/bin/python cyoa_bot.py --port /dev/ttyACM0 --baud 9600
```

Full `.env` example (also in `.env.example`):

```ini
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
GROQ_MODEL=llama-3.1-8b-instant
SERIAL_PORT=/dev/ttyUSB0
BAUD_RATE=115200
MAX_CHUNK_SIZE=200
CHUNK_DELAY=2.0
MAX_HISTORY=10
```

### Checking your configuration (without exposing secrets)

Run the built-in env check to verify all required variables are set.  API
keys are **never** printed – only their presence and length are shown:

```bash
.venv/bin/python cyoa_bot.py --check-env
```

Example output:

```
Environment variable check:
  GROQ_API_KEY: SET (length 56)
  GROQ_MODEL: SET (llama-3.1-8b-instant)
  SERIAL_PORT: SET (/dev/ttyUSB0)
  BAUD_RATE: SET (115200)
  MAX_CHUNK_SIZE: SET (200)
  CHUNK_DELAY: SET (2.0)
  MAX_HISTORY: SET (10)

✓ All required variables are set.
```

This is safe to run in logs or paste into a support request.

### Rotating the Groq API key safely

When you need to replace your Groq API key (e.g. it was accidentally exposed):

1. **Generate a new key** at [console.groq.com](https://console.groq.com) –
   do not paste the old or new key into chat logs, emails, or issue comments.

2. **Update `.env`** on the Raspberry Pi (edit the file directly, never paste
   into a terminal where it might appear in shell history):

   ```bash
   nano /home/cyoa/MCBOT/.env
   # Replace the GROQ_API_KEY value, save and exit (Ctrl+X → Y → Enter)
   ```

3. **Verify the key is set** (this does not print the key value):

   ```bash
   .venv/bin/python cyoa_bot.py --check-env
   ```

4. **Restart the service** to apply the new key:

   ```bash
   sudo systemctl restart mcbot
   sudo systemctl status mcbot   # confirm it came back up
   ```

5. **Revoke the old key** in the Groq console once the service is running
   successfully with the new one.

---

## Python Dependencies

| Package | Version | Required by |
|---|---|---|
| `meshcore` | `>=2.0.0` | `cyoa_bot.py`, `mcbot_monitor.py` |
| `groq` | `>=1.0.0` | `story_engine.py` |
| `python-dotenv` | `>=1.0.0` | `cyoa_bot.py`, `mcbot_monitor.py` |
| `pyserial` | `>=3.5` | `meshcore_radio_config.py` |
| `pytest` | `>=8.0.0` | tests |
| `pytest-asyncio` | `>=0.24.0` | tests |
| `psutil` | `>=5.9.0` | `mcbot_monitor.py` |
| `flask` | `>=3.0.0` | `dashboard/app.py` |
| `flask-socketio` | `>=5.3.0,<6` | `dashboard/app.py` (real-time updates) |
| `python-socketio` | `>=5.3.0,<6` | `dashboard/app.py` (EIO4 protocol) |
| `simple-websocket` | `>=0.10.0` | WebSocket transport for python-socketio |

`setup.sh` installs all of the above automatically into `.venv/`.

---

## Web Dashboard

A lightweight Flask web dashboard lives in the `dashboard/` directory.  After
running `setup.sh`, its dependencies (`flask`) are already installed in the
shared `.venv` and a convenience wrapper script `dashboard.sh` is created at
the project root.

### Starting the dashboard

**Option A – zero-setup helper (works right after cloning, no `setup.sh` needed):**

```bash
bash dashboard/start-dashboard.sh
```

This script installs the required packages automatically and starts the
dashboard using the correct runner (`python -m dashboard.app`).

**Option B – after running `setup.sh` (`.venv` already set up):**

```bash
./dashboard.sh
```

Then open **http://localhost:5000/dashboard/** in your browser, or use the
host machine's IP address (e.g. **http://192.168.1.10:5000/dashboard/**) to
access it from another device on the same network.

> ⛔ **Do NOT use `flask run`.**  The Werkzeug server does not support
> Socket.IO, so real-time live updates will not work.  Always use
> `bash dashboard/start-dashboard.sh`, `./dashboard.sh`, or
> `python -m dashboard.app`.

> 💡 **Socket.IO version compatibility:** The dashboard JavaScript client is
> loaded from a pinned CDN URL (`socket.io 4.x`) to match the Python backend
> (`flask-socketio 5.x` / `python-socketio 5.x`, which use EIO4).  If live
> updates stop working after an upgrade or if you see WebSocket errors in the
> browser console, **clear your browser cache** (`Ctrl+Shift+R` /
> `Cmd+Shift+R`) and reload.  See
> [dashboard/README.md – Troubleshooting](dashboard/README.md#troubleshooting)
> for details.

> ⚠️ **Security warning:** the dashboard binds to `0.0.0.0`, making it
> reachable by *any* device on the same network.  Do not expose this port to
> the internet without adding authentication or firewall restrictions.

> 🔄 **Autostart on reboot:** when you run `sudo bash "$(pwd)/setup.sh"` and
> answer `y` to the service prompt, `setup.sh` copies `dashboard.service` to
> `/etc/systemd/system/dashboard.service` (with the correct user and paths
> substituted), then runs `systemctl daemon-reload`, `systemctl enable`, and
> `systemctl start`.  The dashboard will start automatically on every reboot
> with no further action needed.  To disable autostart:
> `sudo systemctl disable dashboard`.

### Starting the bot and dashboard together

Open two terminals (or use `tmux`/`screen`):

```bash
# Terminal 1 – bot
source .venv/bin/activate && python cyoa_bot.py

# Terminal 2 – dashboard
bash dashboard/start-dashboard.sh
# or, if .venv is already set up by setup.sh:
./dashboard.sh
```

---

## Cloudflare Tunnel (Public HTTPS Access)

A Cloudflare Tunnel lets you expose the MCBOT dashboard and `/chat` API to the
public internet **without opening any inbound firewall ports**.  The automated
setup script handles every step — installing `cloudflared`, authenticating with
Cloudflare, creating the tunnel, configuring DNS, enabling CORS, and setting up
a systemd service for autostart.

> **Full walkthrough:** see [`docs/cloudflare-tunnel-setup.md`](docs/cloudflare-tunnel-setup.md)
> for a detailed step-by-step explanation of what the script does.

### Running the setup script

```bash
# 1. cd into the MCBOT project directory
cd MCBOT

# 2. Run the Cloudflare Tunnel setup wizard
bash setup-cloudflare-tunnel.sh
```

The script is interactive and will prompt you for your tunnel name, subdomain,
CORS origin, Flask port, and Linux username (sensible defaults are provided).

> **Virtual environment note:** This script does *not* use Python — it only
> installs and configures the `cloudflared` binary and systemd services.
> It is safe to run whether or not a virtual environment is active.
> The MCBOT dashboard (Flask) should already be set up via `setup.sh` before
> running this script.

---

## Running Tests

```bash
# Dev dependencies are already installed by setup.sh; just run:
.venv/bin/python -m pytest

# If you set up manually and haven't installed dev dependencies yet:
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

---

## Diagnostics and Monitoring (`mcbot_monitor.py`)

`mcbot_monitor.py` is a standalone helper for debugging bot connectivity
issues – especially useful when the LoRa radio receives messages but the bot
does not respond.

> **Note:** If you used `setup.sh`, all dependencies (including `psutil`) are
> already installed in `.venv` — no extra step needed.  If you set up
> manually, install dev dependencies first:
>
> ```bash
> .venv/bin/pip install -r requirements-dev.txt
> ```

### Available modes

| Flag | Description |
|---|---|
| `--info` | Print system info (CPU, RAM, disk, uptime, Python/platform) and environment summary (`GROQ_API_KEY` masked). Also tests Groq API reachability. |
| `--list-serial` | List all `/dev/ttyUSB*`, `/dev/ttyACM*`, `/dev/ttyS*` devices with permissions. Highlights whether the configured `SERIAL_PORT` is present and accessible. |
| `--listen` | Connect to MeshCore and print **all** incoming events with timestamps and payloads. Press Ctrl+C or let `--duration` expire to stop. |
| `--send-test PUBKEY_PREFIX` | Connect to MeshCore and send a single test message to the specified pubkey prefix. |
| `--text "…"` | Message text for `--send-test` (default: `mcbot monitor test`). |
| `--duration SECONDS` | How long to listen with `--listen` (default: `30`). |
| `--debug` | With `--listen`: print the full raw JSON payload for every event. |
| `--watch-start` | With `--listen`: print a highlighted banner whenever an inbound message is a `start`/`new`/`begin` command (including with a leading `/` or `!` prefix). |

### Example commands

```bash
# 1. Check system health and environment (safe, no hardware required)
.venv/bin/python mcbot_monitor.py --info

# 2. List serial devices and check if your user can access them
.venv/bin/python mcbot_monitor.py --list-serial

# 3. Watch all incoming events for 60 seconds
#    (stop cyoa_bot.py first – only one process can hold the serial port)
.venv/bin/python mcbot_monitor.py --listen --duration 60

# 4. Watch specifically for start commands with a highlighted banner
.venv/bin/python mcbot_monitor.py --listen --duration 120 --watch-start

# 5. Show raw JSON payloads for every event (useful for firmware debugging)
.venv/bin/python mcbot_monitor.py --listen --duration 60 --debug

# 6. Send a one-off test message to confirm the outbound path works
.venv/bin/python mcbot_monitor.py --send-test <PUBKEY_PREFIX> --text "hello from monitor"

# 7. Run without flags to get both help text and --info output
.venv/bin/python mcbot_monitor.py
```

### Verifying that a `start` command is received

Use this workflow to confirm the full path from radio to bot command handler
**without** modifying any code:

1. **Stop the bot** so the monitor can open the serial port:

   ```bash
   # If running as a systemd service:
   sudo systemctl stop mcbot

   # If running directly: press Ctrl+C in the terminal running cyoa_bot.py.
   ```

2. **Start the monitor** in watch-start mode:

   ```bash
   .venv/bin/python mcbot_monitor.py --listen --duration 120 --watch-start
   ```

3. **Send `start`** from a MeshCore client to the bot node.

4. **Expected output** – you should see something like:

   ```
   [2024-07-01T12:00:05.123] EVENT: CONTACT_MSG_RECV
     ├─ from       : ab12cd34
     ├─ message    : 'start'
     ★★★ START COMMAND DETECTED ★★★  (normalised: 'start')
   ```

   The `★★★` banner confirms the message arrived at the serial layer **and**
   that the bot's command normaliser would recognise it (even with a leading
   `/` or `!` prefix such as `/start`).

5. If the `CONTACT_MSG_RECV` event appears but the bot does **not** respond,
   check the bot logs:

   ```bash
   sudo journalctl -u mcbot -f
   ```

   Look for lines containing `"Start command from"` — this is logged
   immediately when the bot receives a recognised start command.

6. If **no** `CONTACT_MSG_RECV` event appears, the problem is at the radio or
   serial layer. Re-check `SERIAL_PORT`, the `dialout` group membership, and
   whether the MeshCore firmware is running on the device.

### What to look for on a Raspberry Pi

**Bot not responding at all?**

1. Run `--info` and confirm `GROQ_API_KEY` is set and the Groq API is
   reachable. If not, the bot will refuse to start.
2. Run `--list-serial` and confirm the device matches `SERIAL_PORT` and your
   user has read/write permission (must be in the `dialout` group – see
   [Step 3](#step-3--add-your-user-to-the-dialout-group)).
3. Stop the bot or service, then run
   `--listen --watch-start --duration 60` while sending a `start` message
   from a LoRa node. If the `★★★` banner appears, the hardware path is
   working — restart the bot and check its logs. If **no** events appear at
   all, the problem is at the serial/radio layer.

**Bot responds sometimes but not others?**

Watch the event summary printed after `--listen` finishes. Look for
`DISCONNECTED` events or a high rate of `ERROR` events, which can indicate
an unstable serial connection.

**Permission denied when opening the serial port?**

```bash
sudo usermod -a -G dialout $USER
newgrp dialout      # apply immediately; log out and back in to make it permanent
```

**`ConnectionError` on startup (no device found)?**

When `cyoa_bot.py` cannot connect it automatically scans for candidate serial
devices and prints a diagnostic:

```
Could not connect to MeshCore device on /dev/ttyUSB0 (baud 115200).

Candidate serial devices found on this system:
  /dev/ttyACM0

Troubleshooting hints:
  • Ensure your user is in the 'dialout' group:
      sudo usermod -a -G dialout $USER && newgrp dialout
  • Check device permissions:
      ls -l /dev/ttyUSB0
      ls -l /dev/ttyACM0
  • Try an alternate port, e.g.:  --port /dev/ttyACM0
```

Use the suggested `--port` flag to try an alternate device without editing
`.env`:

```bash
.venv/bin/python cyoa_bot.py --port /dev/ttyACM0
```

If no candidate devices appear at all, check USB cable / device power and run
`dmesg | tail -20` to see whether the kernel detected the device.

---

## Radio Configuration Tool (`meshcore_radio_config.py`)

`meshcore_radio_config.py` is a standalone serial configuration tool for
setting up a MeshCore LoRa radio. It uses `pyserial` for direct USB serial
access and targets the **UK/EU 868 MHz band** by default.

### Prerequisites

`pyserial` is installed automatically by `setup.sh`. To install manually:

```bash
.venv/bin/pip install -r requirements.txt
```

Your user must also be in the `dialout` group (see
[Step 3](#step-3--add-your-user-to-the-dialout-group) above).

### Usage

```bash
# Interactive menu (recommended) – prompts to select port, then configure
.venv/bin/python meshcore_radio_config.py

# Specify the serial port up front (skip the port-selection prompt)
.venv/bin/python meshcore_radio_config.py --port /dev/ttyUSB0

# Non-interactive: set frequency, name, location, and reboot in one command
.venv/bin/python meshcore_radio_config.py --port /dev/ttyUSB0 \
    --freq 869.525 --name "MyNode" --lat 53.8 --lon -1.5 --reboot

# Open a raw serial shell for manual MeshCore CLI commands
.venv/bin/python meshcore_radio_config.py --shell --port /dev/ttyUSB0
```

### Interactive menu options

| Option | Description |
|---|---|
| `1` Set frequency | Set radio frequency (863–870 MHz for UK/EU) |
| `2` Set node name | Set the node name (no spaces, max 32 chars) |
| `3` Set latitude | Set GPS latitude (decimal degrees) |
| `4` Set longitude | Set GPS longitude (decimal degrees) |
| `5` Show radio public key | Fetch and display the radio's public key |
| `6` Show pending settings | Preview all staged (unapplied) settings |
| `7` Apply settings | Send all pending settings to the radio |
| `8` Apply settings + reboot | Send settings and reboot the radio |
| `9` Manual serial shell | Drop into a raw CLI shell |
| `0` Quit | Close the serial port and exit |

### Public key display

The radio's **public key** (used to address it in the MeshCore mesh) is
automatically shown:

- **On connect** – immediately after opening the serial port.
- **In the menu** – via option `5` "Show radio public key".
- **After non-interactive apply** – printed before the port is closed.

### CLI flags

| Flag | Description |
|---|---|
| `--port DEVICE` | Serial device, e.g. `/dev/ttyUSB0` |
| `--baud RATE` | Baud rate (default: `115200`) |
| `--freq MHZ` | Frequency in MHz (UK/EU default: `869.525`) |
| `--name NAME` | Node name (no spaces, max 32 chars) |
| `--lat DEGREES` | Latitude in decimal degrees |
| `--lon DEGREES` | Longitude in decimal degrees |
| `--reboot` | Send a reboot command after applying settings |
| `--shell` | Open a raw serial shell |

---

## Running as a systemd Service (Linux / Auto-start on Reboot)

Running MCBOT as a systemd service means it starts automatically when the
Raspberry Pi boots and restarts itself if it crashes.

### Automated installation (recommended)

The setup wizard handles everything when run with `sudo`. Use the full path to
avoid the `sudo: ./setup.sh: command not found` pitfall:

```bash
sudo bash "$(pwd)/setup.sh"
```

When prompted *"Would you like to install and enable both systemd services
(mcbot + dashboard)?"*, answer `y`.  The script will:

1. Detect your username (`$SUDO_USER`) and the repo's absolute path.
2. Write `/etc/systemd/system/mcbot.service` (bot) and
   `/etc/systemd/system/dashboard.service` (web dashboard) with the
   exact paths filled in.
3. Run `systemctl daemon-reload`, `systemctl enable`, and `systemctl start` for each service.
4. Print the live `systemctl status` output for both services so you can
   confirm they are active.

The service files written will look like this (using `/home/cyoa/MCBOT` as the
example path — your actual paths are substituted automatically):

**`/etc/systemd/system/mcbot.service`** (bot):

```ini
[Unit]
Description=MeshCore CYOA Story Bot
After=network.target

[Service]
Type=simple
User=cyoa
WorkingDirectory=/home/cyoa/MCBOT
EnvironmentFile=/home/cyoa/MCBOT/.env
ExecStart=/home/cyoa/MCBOT/.venv/bin/python /home/cyoa/MCBOT/cyoa_bot.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**`/etc/systemd/system/dashboard.service`** (web dashboard):

```ini
[Unit]
Description=MCBOT Web Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=cyoa
WorkingDirectory=/home/cyoa/MCBOT/dashboard
ExecStart=/home/cyoa/MCBOT/dashboard/start-dashboard.sh
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### Manual installation

Use this if you prefer not to use the wizard.

1. **Create the venv and install dependencies** (skip if already done):

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

2. **Copy the service template** and edit it with your actual paths:

   ```bash
   sudo cp mcbot.service /etc/systemd/system/mcbot.service
   sudo nano /etc/systemd/system/mcbot.service
   ```

   Update the following four lines (replace `cyoa` and `/home/cyoa/MCBOT` with
   your actual username and repo path):

   ```ini
   User=cyoa
   WorkingDirectory=/home/cyoa/MCBOT
   EnvironmentFile=/home/cyoa/MCBOT/.env
   ExecStart=/home/cyoa/MCBOT/.venv/bin/python /home/cyoa/MCBOT/cyoa_bot.py
   ```

   To find the correct absolute path, run `pwd` from inside the `MCBOT`
   directory.

3. **Enable and start the service:**

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now mcbot.service
   ```

4. **Verify it is running:**

   ```bash
   sudo systemctl status mcbot
   ```

   You should see `active (running)` and `enabled`.

### Managing the services

**Bot (`mcbot`):**

| Task | Command |
|---|---|
| Check status | `sudo systemctl status mcbot` |
| View live logs | `sudo journalctl -u mcbot -f` |
| View recent logs | `sudo journalctl -u mcbot -n 50` |
| Stop the bot | `sudo systemctl stop mcbot` |
| Start the bot | `sudo systemctl start mcbot` |
| Restart the bot | `sudo systemctl restart mcbot` |
| Disable auto-start | `sudo systemctl disable mcbot` |
| Re-enable auto-start | `sudo systemctl enable mcbot` |

**Dashboard (`dashboard`):**

| Task | Command |
|---|---|
| Check status | `sudo systemctl status dashboard` |
| View live logs | `sudo journalctl -u dashboard -f` |
| View recent logs | `sudo journalctl -u dashboard -n 50` |
| Stop the dashboard | `sudo systemctl stop dashboard` |
| Start the dashboard | `sudo systemctl start dashboard` |
| Restart the dashboard | `sudo systemctl restart dashboard` |
| Disable auto-start | `sudo systemctl disable dashboard` |
| Re-enable auto-start | `sudo systemctl enable dashboard` |

### Verifying auto-start after reboot

```bash
sudo reboot
# After the Pi comes back up:
sudo systemctl status mcbot
sudo systemctl status dashboard
```

Both services should show `active (running)` and `enabled`.

If a service fails to start, check its logs for a clear error message:

```bash
sudo journalctl -u dashboard -n 30
```

Common causes: missing `.venv` (re-run `setup.sh`), Python version too old
(install Python 3.10+), or dashboard requirements not installed (run
`.venv/bin/pip install -r dashboard/requirements.txt`).  See
[dashboard/README.md – Troubleshooting](dashboard/README.md#troubleshooting)
for a full reference table.

### Troubleshooting the dashboard systemd service

If `sudo systemctl status dashboard` shows `failed` or `status=1`, follow
these steps:

**1. Check the journal for the exact error:**

```bash
sudo journalctl -u dashboard -n 50 --no-pager
```

**2. Check the startup error log written by the launch script:**

```bash
cat /home/cyoa/MCBOT/dashboard/dashboard-error.log
```

(Replace `/home/cyoa/MCBOT` with your actual repo path.)

**3. Common causes and remedies:**

| Symptom in logs | Cause | Remedy |
|---|---|---|
| `No such file or directory` on `start-dashboard.sh` | Script not executable or wrong path | Re-run `sudo bash "$(pwd)/setup.sh"` to reinstall the service |
| `No Python interpreter found` | `.venv` missing | Re-run `setup.sh` (without `sudo`) to recreate the venv |
| `Python 3.x … required` | Python version too old | Install Python 3.10+, then re-run `setup.sh` |
| `ModuleNotFoundError: flask_socketio` | Dashboard requirements missing | Run `.venv/bin/pip install -r dashboard/requirements.txt` |
| `No module named dashboard` | Wrong working directory | Re-run `sudo bash "$(pwd)/setup.sh"` to reinstall the service with correct paths |

**4. Manually test the launch script** (as the service user) to reproduce the
error interactively:

```bash
sudo -u cyoa bash /home/cyoa/MCBOT/dashboard/start-dashboard.sh
```

**5. Reinstall the service** to apply any path or config corrections:

```bash
sudo bash "$(pwd)/setup.sh"
```

Answer `y` when prompted to install the systemd services.

**6. After any fix, reload and restart the service:**

```bash
sudo systemctl daemon-reload
sudo systemctl restart dashboard
sudo systemctl status dashboard
```

---

## Architecture

```
MeshCore Radio (USB Serial)
        │
        ▼
cyoa_bot.py  (asyncio event loop)
  ├── subscribes to CONTACT_MSG_RECV events
  ├── resolves sender name via mc.get_contact_by_key_prefix()
  ├── parses commands: start / 1-3 / restart / help / free text
  ├── calls StoryEngine for LLM responses
  └── chunks & sends reply via mc.commands.send_msg()

story_engine.py  (StoryEngine)
  ├── one Session object per user (keyed by pubkey_prefix)
  ├── each Session holds bounded conversation history + invisible pacing state
  └── calls Groq AsyncGroq client for story generation

utils.py
  └── chunk_message() – word-aware text splitter
```

---

## Story Pacing / Doom System

Stories are guaranteed to end in peril.  All pacing is **invisible to the
user** — no counters or labels are shown.

### How it works

Each call to `advance_story` increments a per-session *doom* counter:

```
doom += chapter_number + risk_gain
```

`risk_gain` is 0 (safe choice), 1 (neutral), or 2 (risky), determined
automatically from keywords in the player's message.

When doom reaches `DOOM_MAX` the LLM is instructed to write a dramatic
peril finale.

Every `SCENES_PER_CHAPTER` scenes (without doom triggering) the chapter ends
with an in-world cliffhanger followed by an explicit prompt:

```
1. Continue
2. Pause
3. End
```

* **Continue (1)** – the story resumes immediately with no cooldown.
* **Pause (2)** – the session stays open; the player can send any choice at
  any time to continue.
* **End (3)** – the story is closed and an end screen is shown.

If the story reaches `MAX_CHAPTERS` without doom triggering, the engine
forces a peril finale instead of another cliffhanger.

### Configurable knobs (in `story_engine.py`)

| Constant | Default | Purpose |
|---|---|---|
| `DOOM_MAX` | `500` | Doom threshold that triggers a peril finale |
| `SCENES_PER_CHAPTER` | `150` | Scenes before a chapter cliffhanger + choice prompt |
| `MAX_CHAPTERS` | `10` | Hard cap on chapters before forced finale |

### Risk-gain keyword heuristic

High-risk actions (`attack`, `fight`, `run`, `jump`, `steal`, `open`, …)
add **+2** doom on top of the baseline.  Low-risk actions (`wait`, `hide`,
`retreat`, `listen`, `observe`, …) add **+0**.  Everything else adds **+1**.

The keyword sets (`_HIGH_RISK_KEYWORDS`, `_LOW_RISK_KEYWORDS`) are defined
at the top of `story_engine.py` and are easy to extend.