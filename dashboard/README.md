# MCBOT Dashboard

A local Flask web dashboard that shows live bot status and active story sessions.

---

## Features

| Feature | Details |
|---|---|
| Bot status | Running / idle / offline, uptime, error count |
| Active sessions | User name, genre, chapter, scene, state |
| Auto-refresh | Updates every 10 seconds (or manually with the Refresh button) |
| Styled UI | Matches the public-facing website's 70s lava-lamp theme |

---

## Requirements

- Python 3.10+
- Flask 3.x (see `dashboard/requirements.txt`)

---

## Setup

### 1 – Install dependencies

```bash
# From the repository root:
pip install -r dashboard/requirements.txt
```

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

Open **http://localhost:5000/dashboard/** in your browser on the host machine, or
use the host machine's IP address to access the dashboard from another device on
the same network (e.g. **http://192.168.1.10:5000/dashboard/**).

> ⚠️ **Security warning:** binding to `0.0.0.0` makes the dashboard reachable
> by *any* device on the same network.  This is intentional for local
> development but **not safe for production** without additional authentication
> or firewall restrictions.  Do not expose this port to the internet.

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
├── __init__.py          # Package marker
├── app.py               # Flask application + API endpoints
├── state.py             # JSON state file read/write helpers
├── bot_state.json       # Runtime state file (written by the bot; gitignored)
├── requirements.txt     # Flask dependency
├── README.md            # This file
├── static/
│   └── style.css        # Dashboard stylesheet (lava-lamp theme)
└── templates/
    └── index.html       # Dashboard HTML page
```

---

## Customisation

- **Refresh interval** – change `AUTO_REFRESH_MS` in `templates/index.html`.
- **State file location** – override `STATE_FILE` in `state.py` or set the
  `DASHBOARD_STATE_FILE` environment variable (future extension point).
- **Add more stats** – extend the `write_state` call in `cyoa_bot.py` and add
  corresponding fields to the API / template.
