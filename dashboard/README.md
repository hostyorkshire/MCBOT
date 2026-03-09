# MCBOT Dashboard

A local Flask web dashboard that shows live bot status and active story sessions.

---

## Features

| Feature | Details |
|---|---|
| Bot status | Running / idle / offline, uptime, error count |
| Last 20 sessions | User name, genre, chapter, scene, state (active, finished, or restarted) |
| **Real-time live updates** | Instant push via Socket.IO whenever `bot_state.json` changes |
| Fallback polling | Updates every 10 seconds if WebSockets are unavailable |
| Styled UI | Matches the public-facing website's 70s lava-lamp theme |

---

## Requirements

- Python 3.10+
- Flask 3.x, Flask-SocketIO 5.x, and **eventlet** (see `dashboard/requirements.txt`)

> **eventlet** is the production-grade WSGI server used by the dashboard.  It
> replaces Werkzeug's development server and is required for reliable Socket.IO
> support under systemd / production environments.  It is installed
> automatically when you run `pip install -r dashboard/requirements.txt`.

---

## Setup

### 1 – Install dependencies

```bash
# From the repository root:
pip install -r dashboard/requirements.txt
```

This installs `flask`, `flask-socketio`, `eventlet`, and their transport libraries.

> **eventlet** is required for production/systemd use.  It is the WSGI server
> that Flask-SocketIO uses when `async_mode="eventlet"` is set (which is the
> default in this project).

> The main bot dependencies (`requirements.txt`) are separate; install both if
> you are running the bot and the dashboard on the same machine.

### 2 – Run the dashboard

**Option A – helper script (recommended, works out-of-the-box after cloning):**

```bash
# From the repository root or the dashboard/ directory:
bash dashboard/start-dashboard.sh
```

The script installs requirements automatically and launches the dashboard
with the correct runner.

**Option B – run directly:**

```bash
# From the repository root (important – the package path must resolve):
python -m dashboard.app
```

> ⛔ **Do NOT use `flask run`.**  Flask's Werkzeug development server does not
> support the Socket.IO transport, so real-time live updates will **not** work.
> Always start the dashboard with `python -m dashboard.app` or
> `bash dashboard/start-dashboard.sh`.

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

The `dashboard/` directory ships a systemd unit file template and an installer
script so the dashboard can start automatically on boot and restart itself if
it ever crashes.

> **Important:** Never copy the `dashboard.service` template directly to
> `/etc/systemd/system/` – it contains placeholder values that will not work.
> Always use the installer (see below) so that the correct user name and
> repository path are substituted automatically.

### Quick install (recommended)

**The easiest way** is simply to run `setup.sh` from the repository root – it
automatically (re)installs and enables the dashboard systemd service (with the
correct Python venv and paths for the current user/environment) whenever you
answer `y` to the service installation prompt:

```bash
sudo bash "$(pwd)/setup.sh"
```

Alternatively, run the included helper script directly (requires `sudo`):

```bash
sudo bash dashboard/install-dashboard-service.sh
```

Both approaches:
- Auto-detect the correct non-root user (`$SUDO_USER` or directory owner) and
  the repository's absolute path.
- Verify that the Python venv exists, is Python 3.10+, and has
  `flask-socketio` installed; exit with a clear error and remedy if not.
- Write a fully-populated unit file to
  `/etc/systemd/system/dashboard.service`.
- Run `systemctl daemon-reload`, `enable`, and `--now` to activate the service.
- The operation is **idempotent** – running it multiple times is safe.

### Verifying autostart

After installation, confirm the service is enabled and running:

```bash
sudo systemctl status dashboard
```

You should see `active (running)` and `enabled`.

To test autostart after reboot:

```bash
sudo reboot
# Once the system is back up:
sudo systemctl status dashboard
```

### Useful commands

| Action | Command |
|---|---|
| Check status | `sudo systemctl status dashboard` |
| View live logs | `sudo journalctl -u dashboard -f` |
| View recent logs | `sudo journalctl -u dashboard -n 50` |
| Stop the service | `sudo systemctl stop dashboard` |
| Restart the service | `sudo systemctl restart dashboard` |
| Disable autostart | `sudo systemctl disable dashboard` |
| Re-enable autostart | `sudo systemctl enable dashboard` |

### Notes

- The installed unit file (`/etc/systemd/system/dashboard.service`) is
  generated by the installer with the correct `User`, `WorkingDirectory`, and
  `ExecStart` values for your installation.  To regenerate it after moving the
  repository, run `sudo bash dashboard/install-dashboard-service.sh` again.

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

# Tab 2 – start the dashboard (installs requirements automatically)
bash dashboard/start-dashboard.sh
# or equivalently:
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
| `/dashboard/api/stories` | GET | Last 20 story sessions JSON (active, finished, or restarted) |

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

Returns the last 20 story sessions (active, finished, or restarted), newest first.

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
├── active_stories.py              # Persistent story log (last 20 sessions)
├── bot_state.json                 # Runtime state file (written by the bot; gitignored)
├── active_stories.json            # Persistent story log file (written on finish/restart; gitignored)
├── requirements.txt               # Flask + Flask-SocketIO dependencies
├── start-dashboard.sh             # Helper: installs requirements and launches the dashboard
├── README.md                      # This file
├── install-dashboard-service.sh   # Installer: auto-detects paths and writes /etc/systemd/system/dashboard.service
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

---

## How finished story logging works

When a story ends – whether the player chooses **End** at a chapter boundary,
the **doom counter** maxes out, the story reaches the **maximum chapter limit**,
or the player **starts a new adventure** mid-session (a restart) – the engine
calls `upsert_story()` in `dashboard/active_stories.py`.  This writes the full
session snapshot to `dashboard/active_stories.json` so it survives a dashboard
reload or server restart.

### Write path (story_engine.py → active_stories.json)

```
story_engine.py
  ├── start_story()          # existing session → _log_story() [restart]
  └── advance_story()
        ├── choice 3 at chapter boundary → _log_story() [player-ended]
        ├── doom >= DOOM_MAX  → _log_story() [doom finale]
        └── chapter >= MAX_CHAPTERS → _log_story() [forced finale]
```

`_log_story` is the module-level alias for `dashboard.active_stories.upsert_story`.
It is imported lazily so `story_engine` starts cleanly even if the `dashboard`
package is not installed.

### Read path (active_stories.json → /dashboard/api/stories)

`/dashboard/api/stories` merges two sources:

1. **Persisted log** (`active_stories.json`) – finished/restarted sessions that
   survived a server restart.
2. **In-memory sessions** (`get_sessions()`) – currently active sessions from
   `bot_state.json`.

In-memory data takes precedence over persisted data for the same `user_key`,
ensuring live sessions show their most up-to-date state.

### Troubleshooting: finished stories missing from the dashboard

**Step 1 – Check the JSON log**

After finishing a story, inspect the log directly:

```bash
cat dashboard/active_stories.json
```

It should contain an entry with `"finished": true` for the completed session.
If the file is empty or missing, the write path is broken (see Step 3).

**Step 2 – Check the API response**

```bash
curl http://localhost:5000/dashboard/api/stories
```

Finished sessions should appear with `"finished": true`.  If they appear here
but not in the browser table, the issue is in the front-end JavaScript.

**Step 3 – Check the application logs**

Enable `DEBUG` logging (set `FLASK_DEBUG=1` or configure Python logging) and
look for lines from `dashboard.active_stories` and `story_engine`:

```
DEBUG dashboard.active_stories  upsert_story: user_key=… finished=True …
DEBUG dashboard.active_stories  upsert_story: wrote N stories to …
DEBUG story_engine               _log_story called (player-ended): user_key=… …
```

If these lines are absent after a story ends, the finish code path is not
reaching `_log_story`.

**Step 4 – Look for ERROR log lines**

Any `ERROR` from `dashboard.active_stories` means file I/O failed:

```
ERROR dashboard.active_stories  active_stories: failed to write …: [Errno 13] Permission denied
```

Fix the underlying cause (permissions, disk full, etc.) and restart the service.

---

## Troubleshooting

### Socket.IO / Engine.IO version mismatch

**Symptoms:**

- The browser console shows an error like:
  ```
  WebSocket connection to 'ws://…/socket.io/?EIO=4&…' failed
  ```
  or
  ```
  The client is using an unsupported version of the Socket.IO or Engine.IO protocols
  ```
- The "Live updates active" status never appears; the dashboard falls back to 10-second polling.

**Cause:**

The Python backend (`flask-socketio` 5.x / `python-socketio` 5.x) uses
**Socket.IO protocol v5 / Engine.IO v4 (EIO4)**.  The JavaScript client
bundled with older server versions and cached by your browser may be EIO3
(socket.io 2.x or 3.x), which is incompatible.

The HTML templates now load the JavaScript client from a pinned CDN URL
(`socket.io 4.7.5`) so that the URL itself acts as a cache key – a version
change automatically fetches a fresh copy.

**Remedy:**

1. **Clear your browser cache** and hard-reload the page:
   - **Chrome / Edge / Firefox:** `Ctrl+Shift+R` (Windows/Linux) or `Cmd+Shift+R` (Mac)
   - Or open DevTools → Network tab → tick *Disable cache* → reload.

2. Confirm the Python packages are the correct versions:
   ```bash
   .venv/bin/pip show flask-socketio python-socketio
   ```
   Both should be in the **5.x** series (the 5.x series uses EIO4, which
   is what the `socket.io 4.x` JavaScript client expects).

3. If you manually upgraded `python-socketio` to 6.x or higher, also update
   the `<script src="…">` CDN URL in `templates/index.html` and
   `templates/story_live.html` to the matching JavaScript client version.
   See the [python-socketio compatibility table](https://python-socketio.readthedocs.io/en/stable/intro.html#version-compatibility)
   and the [Flask-SocketIO changelog](https://flask-socketio.readthedocs.io/en/latest/changelog.html)
   for the correct pairing.

### Dashboard service fails to start

If `sudo systemctl status dashboard` shows a failure, check the logs:

```bash
sudo journalctl -u dashboard -n 50
```

Common causes and remedies:

| Error in logs | Cause | Remedy |
|---|---|---|
| `python: No such file or directory` | `.venv/bin/python` is missing | Re-run `setup.sh` to recreate the venv |
| `ModuleNotFoundError: flask_socketio` | Dashboard requirements not installed | Run `.venv/bin/pip install -r dashboard/requirements.txt` |
| `ModuleNotFoundError: eventlet` | eventlet not installed | Run `.venv/bin/pip install -r dashboard/requirements.txt` |
| `Python 3.x … required` | Python version too old | Install Python 3.10+, recreate the venv, re-run `setup.sh` |
| `No module named dashboard` | Service started from wrong directory | Ensure `WorkingDirectory` in the unit file is the repo root; re-run the installer |

### After upgrading the repository

After pulling new code that changes dashboard dependencies (e.g. adding
eventlet), reinstall requirements and restart the service:

```bash
.venv/bin/pip install -r dashboard/requirements.txt
sudo systemctl restart dashboard
```

To reinstall the service with correct paths:

```bash
sudo bash dashboard/install-dashboard-service.sh
```

Or re-run the full setup wizard:

```bash
sudo bash "$(pwd)/setup.sh"
```
