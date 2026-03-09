# MCBOT Dashboard

A local Flask web dashboard that shows live bot status and active story sessions.

---

## Features

| Feature | Details |
|---|---|
| Bot status | Running / idle / offline, uptime, error count |
| Active sessions | User name, genre, chapter, scene, state |
| **Real-time live updates** | Instant push via Socket.IO whenever `bot_state.json` changes |
| Fallback polling | Updates every 10 seconds if WebSockets are unavailable |
| Styled UI | Matches the public-facing website's 70s lava-lamp theme |

---

## Requirements

- Python 3.10+
- Flask 3.x and Flask-SocketIO 5.x (see `dashboard/requirements.txt`)

---

## Setup

### 1 – Install dependencies

```bash
# From the repository root:
pip install -r dashboard/requirements.txt
```

This installs both `flask` and `flask-socketio` (plus its transport libraries).

> The main bot dependencies (`requirements.txt`) are separate; install both if
> you are running the bot and the dashboard on the same machine.

### 2 – Run the dashboard

```bash
# From the repository root (important – the package path must resolve):
python -m dashboard.app
```

Or with Flask's development server (enables auto-reload):

```bash
FLASK_DEBUG=1 flask --app dashboard.app run --debug --host=0.0.0.0
```

> **Note:** When using `flask run` directly (instead of `python -m dashboard.app`),
> the Socket.IO background watcher thread that broadcasts live updates is started
> automatically via the application factory.  Both launch methods work the same way.

Open **http://localhost:5000/dashboard/** in your browser on the host machine, or
use the host machine's IP address to access the dashboard from another device on
the same network (e.g. **http://192.168.1.10:5000/dashboard/**).

> ⚠️ **Security warning:** binding to `0.0.0.0` makes the dashboard reachable
> by *any* device on the same network.  This is intentional for local
> development but **not safe for production** without additional authentication
> or firewall restrictions.  Do not expose this port to the internet.

---

## Real-time updates (Socket.IO)

The dashboard uses [Flask-SocketIO](https://flask-socketio.readthedocs.io/) to
push live data to connected browsers the instant `bot_state.json` changes on
disk.

### How it works

1. A lightweight background thread inside the Flask process polls
   `bot_state.json` every second for file-modification-time changes.
2. When a change is detected the thread reads the new state and emits a
   `story_update` Socket.IO event to **all** connected clients.
3. Each browser receives the event and re-renders the status card and stories
   table immediately – no page reload required.

### Fallback behaviour

If a client's browser does not support WebSockets, or if Socket.IO fails to
connect for any reason, the page automatically falls back to the original
10-second HTTP polling loop.  The REST API endpoints
(`/dashboard/api/status` and `/dashboard/api/stories`) remain fully functional
regardless of WebSocket support.

### Socket.IO event reference

| Event | Direction | Payload |
|---|---|---|
| `story_update` | Server → Client | `{ "status": {...}, "stories": [...] }` |

The `status` object matches the `/dashboard/api/status` response schema and
`stories` matches the `/dashboard/api/stories` response schema.

---

## Automatic startup with systemd

The `dashboard/` directory ships a ready-to-use systemd unit file
(`dashboard-dashboard.service`) so the dashboard can start automatically on
boot and restart itself if it ever crashes.

### Quick install (recommended)

**The easiest way** is simply to run `setup.sh` from the repository root – it
automatically (re)installs and enables the dashboard systemd service every time
it runs, with no manual steps required:

```bash
sudo bash "$(pwd)/setup.sh"
```

Alternatively, run the included helper script directly from the **repository
root** (requires `sudo`):

```bash
bash dashboard/install-dashboard-service.sh
```

Both approaches copy the unit file to `/etc/systemd/system/`, reload the
daemon, and enable + start the service immediately.  The operation is
idempotent – running it multiple times is safe.

### Manual installation

```bash
# 1. Copy the unit file to the system-wide systemd directory
sudo cp dashboard/dashboard-dashboard.service /etc/systemd/system/

# 2. Reload the systemd daemon
sudo systemctl daemon-reload

# 3. Enable and start the service
sudo systemctl enable --now dashboard-dashboard.service
```

### Useful commands

| Action | Command |
|---|---|
| Check status | `sudo systemctl status dashboard-dashboard` |
| View live logs | `sudo journalctl -u dashboard-dashboard -f` |
| Stop the service | `sudo systemctl stop dashboard-dashboard` |
| Disable autostart | `sudo systemctl disable dashboard-dashboard` |

### Notes

- The service file assumes the repository is located at `/home/cyoa/MCBOT` and
  the user is `cyoa`.  Edit `dashboard/dashboard-dashboard.service` (or the
  installed copy in `/etc/systemd/system/`) if your paths differ, then run
  `sudo systemctl daemon-reload`.

---

## Running alongside the bot

The bot (`cyoa_bot.py`) writes a `dashboard/bot_state.json` file every 5 seconds
when the `dashboard` package is importable.  The Flask dashboard reads that file
to serve the API endpoints – they communicate through the filesystem and never
share a process or port.

**Recommended workflow (two terminal tabs):**

```bash
# Tab 1 – start the bot
python cyoa_bot.py

# Tab 2 – start the dashboard
python -m dashboard.app
```

If the bot is not running, the dashboard shows the bot as **offline**.

To access the dashboard from another device on the same network, navigate to
`http://<host-ip>:5000/dashboard/` where `<host-ip>` is the IP address of the
machine running the dashboard (e.g. `http://192.168.1.10:5000/dashboard/`).

---

## API endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/dashboard/` | GET | Dashboard HTML page |
| `/dashboard/api/status` | GET | Bot status JSON |
| `/dashboard/api/stories` | GET | Active story sessions JSON |

### `/dashboard/api/status` response

```json
{
  "status": "running",
  "uptime": 3672.4,
  "uptime_human": "1h 1m 12s",
  "error_count": 0,
  "start_time": 1717000000.0
}
```

### `/dashboard/api/stories` response

```json
[
  {
    "user_key": "abc123",
    "user_name": "Adventurer",
    "genre": "wasteland",
    "genre_name": "Post-Apocalyptic Wasteland",
    "chapter": 1,
    "scene_in_chapter": 3,
    "doom": 4,
    "finished": false,
    "awaiting_chapter_choice": false,
    "started_at": 1717001234.5
  }
]
```

---

## File layout

```
dashboard/
├── __init__.py                    # Package marker
├── app.py                         # Flask application + API endpoints + Socket.IO setup
├── state.py                       # JSON state file read/write helpers
├── bot_state.json                 # Runtime state file (written by the bot; gitignored)
├── requirements.txt               # Flask + Flask-SocketIO dependencies
├── README.md                      # This file
├── dashboard-dashboard.service    # systemd unit file for automatic startup
├── install-dashboard-service.sh   # Convenience script to install the service
├── static/
│   └── style.css                  # Dashboard stylesheet (lava-lamp theme)
└── templates/
    └── index.html                 # Dashboard HTML page (Socket.IO client included)
```

---

## Customisation

- **Fallback polling interval** – change `AUTO_REFRESH_MS` in `templates/index.html`
  (only used when the WebSocket connection is unavailable).
- **Live-update poll frequency** – change `poll_interval` in `_state_watcher` inside
  `app.py` (how often the server checks `bot_state.json` for changes; default 1 s).
- **State file location** – override `STATE_FILE` in `state.py` or set the
  `DASHBOARD_STATE_FILE` environment variable (future extension point).
- **Add more stats** – extend the `write_state` call in `cyoa_bot.py` and add
  corresponding fields to the API / template.
