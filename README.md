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
├── cyoa_bot.py        # Main entry point – MeshCore event loop
├── story_engine.py    # Groq LLM session management
├── utils.py           # Message chunking helpers
├── requirements.txt   # Python dependencies
├── .env.example       # Configuration template
├── pytest.ini         # Test configuration
└── tests/
    ├── test_story_engine.py
    └── test_utils.py
```

---

## Quick Start

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in at minimum:

| Variable | Description |
|---|---|
| `GROQ_API_KEY` | Free API key from [console.groq.com](https://console.groq.com) |
| `SERIAL_PORT` | USB serial port, e.g. `/dev/ttyUSB0` |

### 3. Add yourself to the `dialout` group (Linux)

```bash
sudo usermod -a -G dialout $USER
newgrp dialout          # apply without logging out
```

### 4. Run the bot

```bash
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
pip install pytest pytest-asyncio
pytest
```

---

## Running as a systemd Service (Raspberry Pi)

Create `/etc/systemd/system/cyoa-bot.service`:

```ini
[Unit]
Description=MeshCore CYOA Story Bot
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/MCBOT
EnvironmentFile=/home/pi/MCBOT/.env
ExecStart=/usr/bin/python3 /home/pi/MCBOT/cyoa_bot.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl enable cyoa-bot
sudo systemctl start cyoa-bot
sudo journalctl -u cyoa-bot -f
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
  ├── each Session holds bounded conversation history
  └── calls Groq AsyncGroq client for story generation

utils.py
  └── chunk_message() – word-aware text splitter
```
