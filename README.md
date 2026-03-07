# MCBOT – MeshCore CYOA Story Bot

A lightweight **Create Your Own Adventure (CYOA)** story bot for
[MeshCore](https://github.com/meshcore-dev/MeshCore) LoRa mesh networks.

Runs on a **Raspberry Pi Zero 2W** connected to a MeshCore LoRa radio via USB
serial.  Story text is generated in real time by the free tier of the
[Groq](https://console.groq.com) cloud LLM API (Llama 3, Mixtral, etc.).

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
├── setup.sh                       # Interactive setup wizard (.env generator + systemd installer)
├── mcbot.service                  # Systemd unit file template (installed by setup.sh)
├── pytest.ini                     # Test configuration
└── tests/
    ├── test_cyoa_bot.py
    ├── test_story_engine.py
    ├── test_utils.py
    ├── test_mcbot_monitor.py
    └── test_meshcore_radio_config.py
```

---

## Prerequisites (Debian / Raspberry Pi OS)

Before you begin, make sure `python3-venv` and `python3-pip` are installed:

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip
```

### Python dependencies

| Package | Required by | Notes |
|---|---|---|
| `meshcore>=2.0.0` | `cyoa_bot.py`, `mcbot_monitor.py` | MeshCore serial protocol library |
| `groq>=1.0.0` | `story_engine.py` | Groq cloud LLM API client |
| `python-dotenv>=1.0.0` | `cyoa_bot.py`, `mcbot_monitor.py` | `.env` file loading |
| `pyserial>=3.5` | `meshcore_radio_config.py` | Direct USB serial access for radio config |

`pyserial` is used only by `meshcore_radio_config.py`.  All other scripts use
the `meshcore` library for serial communication.  `./setup.sh` installs all
dependencies automatically.

---

## Quick Start

### 1. Run the setup wizard

```bash
chmod +x setup.sh
./setup.sh
```

The wizard will:

- Create a Python virtual environment at `.venv/` (reused on re-runs).
- Install all dependencies from `requirements.txt` and `requirements-dev.txt`
  (includes `psutil` for the monitor) into the venv using `.venv/bin/pip`.
- Prompt for all configuration values and write `.env`.
- Optionally install and enable the `mcbot.service` systemd service so the
  bot **starts automatically on every reboot** (Raspberry Pi / Linux only).
  When prompted *"Would you like to install and enable the mcbot systemd
  service?"* answer `y` and the script will:
  - Write `/etc/systemd/system/mcbot.service` with the correct paths and user
  - Configure `ExecStart` to use `.venv/bin/python` so all dependencies are
    available when the service starts
  - Run `systemctl daemon-reload` and `systemctl enable --now mcbot.service`
- Prompt *"Would you like to start the mcbot monitor now?"*:
  - Answer `y` to launch `mcbot_monitor.py --info` immediately using the venv
    interpreter (no activation required).
  - Answer `n` (or press Enter) and the wizard will print example commands so
    you can run the monitor later.

> **Note:** Systemd installation requires root. Either run
> `sudo bash "$(pwd)/setup.sh"` from the repo directory from the start, or
> simply answer `y` when the script prompts *"Re-run now with sudo?"* and it
> will re-exec itself automatically.
>
> **Why not `sudo ./setup.sh`?** `sudo` resets `PATH` and does not look in the
> current directory, so `sudo ./setup.sh` returns *"command not found"* on many
> systems. Using the full path (`sudo bash /abs/path/to/setup.sh`) avoids this.

**Alternative – manual setup:**

```bash
# Create and activate the virtual environment
python3 -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate

# Install dependencies
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -r requirements-dev.txt

# Verify that python-dotenv installed correctly
.venv/bin/python -c "from dotenv import load_dotenv; print('python-dotenv OK')"

# Copy and edit the configuration
cp .env.example .env
```

Edit `.env` and fill in at minimum:

| Variable | Description |
|---|---|
| `GROQ_API_KEY` | Free API key from [console.groq.com](https://console.groq.com) |
| `SERIAL_PORT` | USB serial port, e.g. `/dev/ttyUSB0` |

### 2. Add yourself to the `dialout` group (Linux)

```bash
sudo usermod -a -G dialout $USER
newgrp dialout          # apply without logging out
```

### 3. Run the bot

```bash
# Via the venv Python directly (no activation needed)
.venv/bin/python cyoa_bot.py

# Or activate the venv first
source .venv/bin/activate
python cyoa_bot.py
```

---

## Gameplay (via MeshCore radio)

Send a direct message to the bot node from any MeshCore client:

| Message | Action |
|---|---|
| `start` / `new` / `begin` | Start a new adventure |
| `1`, `2`, or `3` | Choose the numbered story option |
| `restart` / `reset` | Reset your current story and start fresh |
| `help` / `?` | Show command reference |

Any other text while a story is in progress is treated as free-text input to
the story engine.

---

## Configuration Reference

All settings are loaded from environment variables (`.env` file):

| Variable | Default | Description |
|---|---|---|
| `GROQ_API_KEY` | *(required)* | Groq API key |
| `GROQ_MODEL` | `llama3-8b-8192` | Model name (free-tier: llama3-8b-8192, llama3-70b-8192, mixtral-8x7b-32768) |
| `SERIAL_PORT` | `/dev/ttyUSB0` | Serial device path |
| `BAUD_RATE` | `115200` | Serial baud rate |
| `MAX_CHUNK_SIZE` | `200` | Max characters per LoRa message |
| `CHUNK_DELAY` | `2.0` | Seconds between consecutive message chunks |
| `MAX_HISTORY` | `10` | Max conversation turns kept per user (RAM limit) |

---

## Running Tests

```bash
# Dev dependencies are already installed by ./setup.sh; just run tests:
.venv/bin/python -m pytest
```

Or, if you set up manually:

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

---

## Diagnostics and Monitoring (`mcbot_monitor.py`)

`mcbot_monitor.py` is a standalone helper for debugging bot connectivity
issues – especially useful when the LoRa radio receives messages but the bot
does not respond.

### Prerequisites

Install the dev dependencies (includes `psutil` for system metrics):

```bash
.venv/bin/pip install -r requirements-dev.txt
```

> **Note:** `mcbot_monitor.py` can run without `python-dotenv` installed —
> it will skip loading `.env` and print a warning, but all other functionality
> (system info, serial listing, etc.) will still work.  For full functionality,
> run it via `.venv/bin/python mcbot_monitor.py` or install all dependencies
> with `.venv/bin/pip install -r requirements.txt`.
>
> If you used `./setup.sh`, all dependencies (including `psutil`) are already
> installed in `.venv` — no extra step needed.

### Available modes

| Flag | Description |
|---|---|
| `--info` | Print system info (CPU, RAM, disk, uptime, Python/platform) and environment summary (GROQ_API_KEY masked). Also tests Groq API reachability. |
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

# 2. See which serial devices are present and if current user can access them
.venv/bin/python mcbot_monitor.py --list-serial

# 3. Connect and watch for all incoming events for 60 seconds
#    (stop cyoa_bot.py first – only one process can hold the serial port)
.venv/bin/python mcbot_monitor.py --listen --duration 60

# 4. Watch specifically for start commands with a clear banner
.venv/bin/python mcbot_monitor.py --listen --duration 120 --watch-start

# 5. Show raw JSON payloads for every event (useful for firmware debugging)
.venv/bin/python mcbot_monitor.py --listen --duration 60 --debug

# 6. Send a one-off test message to confirm outbound path works
.venv/bin/python mcbot_monitor.py --send-test <PUBKEY_PREFIX> --text "hello from monitor"

# 7. Run without flags to get both help text and --info output
.venv/bin/python mcbot_monitor.py
```

### Verifying that a `start` command is received

Use this workflow to confirm the full path from radio to bot command handler
**without** modifying any code and without needing to restart the bot:

1. **Stop the bot** so the monitor can open the serial port:

   ```bash
   # If running as a systemd service:
   sudo systemctl stop mcbot

   # If running directly:
   # Press Ctrl+C in the terminal where cyoa_bot.py is running.
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
   check the bot's log output (`sudo journalctl -u mcbot -f`) for lines
   containing `"Start command from"` — this INFO-level log line is emitted
   immediately when the bot receives a recognised start command.

6. If **no** `CONTACT_MSG_RECV` event appears, the problem is at the radio or
   serial layer.  Re-check `SERIAL_PORT`, the `dialout` group, and whether the
   MeshCore firmware is running on the device.

### What to look for on a Raspberry Pi

**Bot not responding at all?**

1. Run `--info` – check that `GROQ_API_KEY` is set and the Groq API is
   reachable.  If not, the bot will refuse to start.
2. Run `--list-serial` – confirm the device listed under `SERIAL_PORT` exists
   and your user has read/write permission (must be in the `dialout` group).
3. Stop `cyoa_bot.py` (or the `mcbot` systemd service), then run
   `--listen --watch-start --duration 60` while sending a `start` message
   from a LoRa node.
   If the `★★★ START COMMAND DETECTED ★★★` banner appears, the hardware path
   is working; restart the bot and check its logs.  If **no** events appear,
   the problem is at the serial/radio layer.

**Bot responds sometimes but not others?**

- Watch the event summary printed after `--listen` finishes.  Look for
  `DISCONNECTED` events or a high rate of `ERROR` events that might indicate
  an unstable serial connection.

**Permission denied when opening the serial port?**

```bash
sudo usermod -a -G dialout $USER
newgrp dialout   # apply without logging out
```

---

## Radio Configuration Tool (`meshcore_radio_config.py`)

`meshcore_radio_config.py` is a standalone serial configuration tool for
setting up a MeshCore LoRa radio.  It uses `pyserial` for direct USB serial
access and is designed for **UK/EU 868 MHz band** operation by default.

### Prerequisites

`pyserial` is installed automatically by `./setup.sh` or manually:

```bash
.venv/bin/pip install -r requirements.txt
```

Your user must also be in the `dialout` group to access `/dev/ttyUSB*` and
`/dev/ttyACM*` devices:

```bash
sudo usermod -a -G dialout $USER
newgrp dialout   # apply without logging out
```

### Usage

```bash
# Interactive menu (recommended) – select port from list, then configure
.venv/bin/python meshcore_radio_config.py

# Specify the serial port up front (skip the port-selection prompt)
.venv/bin/python meshcore_radio_config.py --port /dev/ttyUSB0

# Non-interactive: apply settings and reboot in one command
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
- **In the menu** – via option 5 "Show radio public key".
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

### Automated installation (recommended)

Re-run the setup wizard and answer `y` to the service prompt.  The simplest
way is to let the script re-exec itself – when the wizard asks
*"Re-run now with sudo?"* just answer `y`.  Alternatively, pass the full path
to avoid the `sudo: ./setup.sh: command not found` pitfall:

```bash
sudo bash "$(pwd)/setup.sh"
```

The script writes `/etc/systemd/system/mcbot.service` (using the paths and
user detected at install time), reloads the systemd daemon, and enables the
service so it starts on every boot.

### Manual installation

1. Create the virtual environment and install dependencies (if not already done
   by `./setup.sh`):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

2. Copy the template and substitute the placeholder values:

```bash
sudo cp mcbot.service /etc/systemd/system/mcbot.service
sudo nano /etc/systemd/system/mcbot.service   # fill in User, WorkingDirectory, ExecStart
```

The `mcbot.service` file in this repository contains the full template with
inline comments explaining each placeholder. Key fields to update:

| Field | Example value |
|---|---|
| `User` | `pi` (the OS user that owns the repository) |
| `WorkingDirectory` | `/home/pi/MCBOT` |
| `EnvironmentFile` | `/home/pi/MCBOT/.env` |
| `ExecStart` | `/home/pi/MCBOT/.venv/bin/python /home/pi/MCBOT/cyoa_bot.py` |

3. Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mcbot
```

### Managing the service

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

### Verifying auto-start after reboot

```bash
sudo reboot
# after the Pi comes back up:
sudo systemctl status mcbot
```

The service status should show `active (running)` and `enabled`.

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
  ├── each Session holds bounded conversation history
  └── calls Groq AsyncGroq client for story generation

utils.py
  └── chunk_message() – word-aware text splitter
```