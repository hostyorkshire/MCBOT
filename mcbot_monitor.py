#!/usr/bin/env python3
"""MeshCore CYOA Bot – diagnostic and monitoring helper.

Provides observability into process/system health, serial/MeshCore
connectivity, incoming event visibility, outbound send testing, and
Groq API reachability.  Intended for debugging on a Raspberry Pi when
the bot appears to be running but not responding.

Usage examples::

    # System and environment summary
    python mcbot_monitor.py --info

    # List available serial devices
    python mcbot_monitor.py --list-serial

    # Connect and print all incoming MeshCore events for 60 s
    python mcbot_monitor.py --listen --duration 60

    # Same, but show raw JSON payloads for every event
    python mcbot_monitor.py --listen --duration 60 --debug

    # Watch specifically for 'start' commands (print a banner when seen)
    python mcbot_monitor.py --listen --duration 120 --watch-start

    # Send a test message to a node
    python mcbot_monitor.py --send-test abc123 --text "ping"

Configuration is read from the same .env file used by cyoa_bot.py.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import glob as _glob
import logging
import os
import platform
import stat
import sys
import time

try:
    from dotenv import load_dotenv

    _DOTENV_AVAILABLE = True
except ImportError:
    _DOTENV_AVAILABLE = False

    def load_dotenv(*_args, **_kwargs) -> None:  # type: ignore[misc]
        """No-op fallback used when python-dotenv is not installed."""


# ---------------------------------------------------------------------------
# Bootstrap – load .env before anything else reads os.getenv()
# ---------------------------------------------------------------------------
if _DOTENV_AVAILABLE:
    load_dotenv()
else:
    print(
        "WARNING: python-dotenv not installed; skipping .env loading. "
        "Install with: pip install -r requirements.txt  "
        "(or run .venv/bin/python mcbot_monitor.py ...)",
        file=sys.stderr,
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (mirrors cyoa_bot.py so both tools share the same .env)
# ---------------------------------------------------------------------------
SERIAL_PORT: str = os.getenv("SERIAL_PORT", "/dev/ttyUSB0")
BAUD_RATE: int = int(os.getenv("BAUD_RATE", "115200"))
GROQ_API_KEY: str | None = os.getenv("GROQ_API_KEY")
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama3-8b-8192")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REDACTED = "<redacted>"
_MASK_KEEP = 4  # how many suffix characters to leave visible


def _mask_secret(value: str | None) -> str:
    """Return *value* with all but the last few characters replaced by ``*``."""
    if not value:
        return _REDACTED
    keep = min(_MASK_KEEP, len(value) // 2)
    return "*" * (len(value) - keep) + value[-keep:]


def _separator(title: str = "", width: int = 60) -> str:
    if title:
        side = (width - len(title) - 2) // 2
        return f"{'─' * side} {title} {'─' * (width - side - len(title) - 2)}"
    return "─" * width


# ---------------------------------------------------------------------------
# Mode: --info
# ---------------------------------------------------------------------------


def cmd_info() -> None:
    """Print system, Python, and environment summary."""
    print(_separator("System Info"))

    # Python / platform
    print(f"  Python       : {sys.version}")
    print(f"  Platform     : {platform.platform()}")
    print(f"  Architecture : {platform.machine()}")
    print(f"  Node         : {platform.node()}")
    print(f"  Timestamp    : {datetime.datetime.now().isoformat(timespec='seconds')}")

    # psutil is optional – fall back gracefully if not installed
    try:
        import psutil  # noqa: PLC0415

        boot_ts = psutil.boot_time()
        uptime_secs = time.time() - boot_ts
        uptime = str(datetime.timedelta(seconds=int(uptime_secs)))

        vm = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        cpu_pct = psutil.cpu_percent(interval=1)

        print(f"  Uptime       : {uptime}")
        print(f"  CPU usage    : {cpu_pct:.1f}%")
        print(
            f"  RAM          : {vm.used / 1024**2:.0f} MB used"
            f" / {vm.total / 1024**2:.0f} MB total"
            f" ({vm.percent:.1f}%)"
        )
        print(
            f"  Disk (/)     : {disk.used / 1024**3:.1f} GB used"
            f" / {disk.total / 1024**3:.1f} GB total"
            f" ({disk.percent:.1f}%)"
        )
    except ImportError:
        print("  (install psutil for CPU/RAM/disk stats: pip install psutil)")

    print(_separator("Dependencies"))
    dotenv_status = "installed" if _DOTENV_AVAILABLE else (
        "NOT installed – .env not loaded "
        "(fix: pip install -r requirements.txt)"
    )
    print(f"  python-dotenv : {dotenv_status}")

    print(_separator("Environment"))
    env_vars = {
        "SERIAL_PORT": os.getenv("SERIAL_PORT", "(not set)"),
        "BAUD_RATE": os.getenv("BAUD_RATE", "(not set)"),
        "GROQ_MODEL": os.getenv("GROQ_MODEL", "(not set)"),
        "MAX_CHUNK_SIZE": os.getenv("MAX_CHUNK_SIZE", "(not set)"),
        "CHUNK_DELAY": os.getenv("CHUNK_DELAY", "(not set)"),
        "MAX_HISTORY": os.getenv("MAX_HISTORY", "(not set)"),
        "GROQ_API_KEY": _mask_secret(GROQ_API_KEY),
    }
    width = max(len(k) for k in env_vars)
    for key, val in env_vars.items():
        print(f"  {key:<{width}} : {val}")

    print(_separator("Groq API Key"))
    if GROQ_API_KEY:
        print(f"  GROQ_API_KEY is SET ({_mask_secret(GROQ_API_KEY)})")
        _check_groq()
    else:
        print("  GROQ_API_KEY is NOT set – bot will not start.")

    print(_separator())


def _check_groq() -> None:
    """Attempt a lightweight Groq API call and report the result."""
    try:
        from groq import Groq  # noqa: PLC0415

        client = Groq(api_key=GROQ_API_KEY)
        # list_models is a lightweight call that doesn't consume tokens
        models = client.models.list()
        names = [m.id for m in models.data[:3]]
        print(f"  Groq API     : reachable – sample models: {names}")
    except ImportError:
        print("  Groq API     : groq package not installed")
    except Exception as exc:  # noqa: BLE001
        print(f"  Groq API     : ERROR – {exc}")


# ---------------------------------------------------------------------------
# Mode: --list-serial
# ---------------------------------------------------------------------------


def cmd_list_serial() -> None:
    """List available serial devices and their permissions."""
    print(_separator("Serial Devices"))

    patterns = ["/dev/ttyUSB*", "/dev/ttyACM*", "/dev/ttyS*"]
    found: list[str] = []
    for pattern in patterns:
        found.extend(sorted(_glob.glob(pattern)))

    if not found:
        print("  No serial devices found matching ttyUSB*, ttyACM*, ttyS*")
    else:
        for device in found:
            _describe_device(device)

    configured = SERIAL_PORT
    print(f"\n  Configured SERIAL_PORT : {configured}")
    if configured not in found:
        print(f"  WARNING: {configured} not found in the device list above.")
    else:
        print(f"  {configured} is present.")

    print(_separator())


def _describe_device(path: str) -> None:
    """Print one line of info about a serial device."""
    try:
        st = os.stat(path)
        mode = stat.filemode(st.st_mode)
        try:
            import grp  # noqa: PLC0415
            import pwd  # noqa: PLC0415

            owner = pwd.getpwuid(st.st_uid).pw_name
            group = grp.getgrgid(st.st_gid).gr_name
        except (ImportError, KeyError):
            owner = str(st.st_uid)
            group = str(st.st_gid)

        current_user = (
            os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
        )
        readable = os.access(path, os.R_OK | os.W_OK)
        rw_tag = "rw-ok" if readable else "NO rw access"

        print(f"  {path}  {mode}  {owner}:{group}  [{rw_tag}]")
        if not readable:
            print(
                f"    → Add current user ({current_user}) to the '{group}' group:\n"
                f"      sudo usermod -a -G {group} {current_user} && newgrp {group}"
            )
    except OSError as exc:
        print(f"  {path}  (stat failed: {exc})")


# ---------------------------------------------------------------------------
# Mode: --listen
# ---------------------------------------------------------------------------


async def cmd_listen(duration: float, *, debug: bool = False, watch_start: bool = False) -> None:
    """Connect to MeshCore and log all incoming events."""
    from meshcore import EventType, MeshCore  # noqa: PLC0415

    print(_separator("MeshCore Event Listener"))
    print(f"  Serial port  : {SERIAL_PORT}")
    print(f"  Baud rate    : {BAUD_RATE}")
    print(f"  Duration     : {duration:.0f} s")
    if debug:
        print("  Debug mode   : ON  (raw JSON payloads shown)")
    if watch_start:
        print("  Watch-start  : ON  (start commands highlighted)")
    print(_separator())

    log.info("Connecting to MeshCore at %s (baud %d)…", SERIAL_PORT, BAUD_RATE)
    try:
        mc = await MeshCore.create_serial(SERIAL_PORT, BAUD_RATE)
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to connect: %s", exc)
        print(
            "\n  Could not open serial port. Possible causes:\n"
            "  • Wrong SERIAL_PORT – run with --list-serial to see devices\n"
            "  • Permission denied – see dialout group instructions\n"
            "  • Device already in use by cyoa_bot.py\n"
            "  • Hardware not connected / firmware not running"
        )
        return

    if mc is None:
        log.error("MeshCore.create_serial returned None – check port/baud.")
        return

    log.info("Connected. Subscribing to all event types…")

    # Prefixes stripped by the bot's _normalize_command helper.
    _CMD_PREFIXES = ("/", "!", "\\")
    _START_CMDS = {"start", "new", "begin"}

    received: list[tuple[float, str, object]] = []

    def _make_handler(event_name: str):
        def handler(event: object) -> None:
            ts = datetime.datetime.now().isoformat(timespec="milliseconds")
            payload = getattr(event, "payload", event)
            received.append((time.time(), event_name, payload))

            # Always print event type and timestamp.
            print(f"[{ts}] EVENT: {event_name}")

            # For inbound message events, print human-friendly details.
            if event_name == "CONTACT_MSG_RECV" and isinstance(payload, dict):
                pubkey = payload.get("pubkey_prefix", "<unknown>")
                text = payload.get("text", "")
                print(f"  ├─ from       : {pubkey}")
                print(f"  ├─ message    : {text!r}")

                # Detect start commands the same way the bot does.
                if watch_start:
                    cmd = text.strip().lower()
                    if cmd and cmd[0] in _CMD_PREFIXES:
                        cmd = cmd[1:]
                    if cmd in _START_CMDS:
                        print(
                            "  ★★★ START COMMAND DETECTED ★★★  "
                            f"(normalised: {cmd!r})"
                        )
            elif isinstance(payload, dict):
                # For other dict payloads print a one-line summary.
                summary_keys = [k for k in ("text", "name", "id", "type") if k in payload]
                if summary_keys:
                    summary = ", ".join(
                        f"{k}={payload[k]!r}" for k in summary_keys
                    )
                    print(f"  ├─ {summary}")

            # In debug mode always print the full raw payload.
            if debug:
                try:
                    import json  # noqa: PLC0415

                    raw = json.dumps(payload, default=str, indent=4)
                except (TypeError, ValueError):
                    raw = repr(payload)
                for line in raw.splitlines():
                    print(f"  │  {line}")

        return handler

    for event_type in EventType:
        mc.subscribe(event_type, _make_handler(event_type.name))

    log.info("Listening for %.0f seconds. Press Ctrl+C to stop early.", duration)
    try:
        await asyncio.sleep(duration)
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Interrupted.")
    finally:
        await mc.disconnect()
        log.info("Disconnected.")

    print(_separator("Summary"))
    print(f"  Total events received : {len(received)}")
    if received:
        counts: dict[str, int] = {}
        for _, name, _ in received:
            counts[name] = counts.get(name, 0) + 1
        for name, n in sorted(counts.items(), key=lambda x: -x[1]):
            print(f"    {name}: {n}")
    else:
        print(
            "  No events received. Possible causes:\n"
            "  • No messages were sent to the bot node during the listen window\n"
            "  • Wrong SERIAL_PORT or baud rate (check with --list-serial)\n"
            "  • MeshCore firmware not running on the device\n"
            "  • cyoa_bot.py already holds the serial port (stop it first)"
        )
    print(_separator())


# ---------------------------------------------------------------------------
# Mode: --send-test
# ---------------------------------------------------------------------------


async def cmd_send_test(pubkey_prefix: str, text: str) -> None:
    """Connect to MeshCore and send a single test message."""
    from meshcore import MeshCore  # noqa: PLC0415

    print(_separator("Send Test Message"))
    print(f"  Serial port    : {SERIAL_PORT}")
    print(f"  Baud rate      : {BAUD_RATE}")
    print(f"  Destination    : {pubkey_prefix}")
    print(f"  Message text   : {text}")
    print(_separator())

    log.info("Connecting to MeshCore at %s (baud %d)…", SERIAL_PORT, BAUD_RATE)
    try:
        mc = await MeshCore.create_serial(SERIAL_PORT, BAUD_RATE)
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to connect: %s", exc)
        return

    if mc is None:
        log.error("MeshCore.create_serial returned None – check port/baud.")
        return

    log.info("Connected. Sending test message…")
    try:
        await mc.commands.send_msg(pubkey_prefix, text)
        log.info("Message sent successfully to %s.", pubkey_prefix)
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to send message: %s", exc)
    finally:
        await mc.disconnect()
        log.info("Disconnected.")

    print(_separator())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcbot_monitor",
        description="MeshCore CYOA Bot – diagnostic and monitoring helper.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--info",
        action="store_true",
        help="Print system info, environment summary, and Groq API reachability.",
    )
    parser.add_argument(
        "--list-serial",
        action="store_true",
        help="List available serial devices (/dev/ttyUSB*, /dev/ttyACM*, /dev/ttyS*).",
    )
    parser.add_argument(
        "--listen",
        action="store_true",
        help="Connect to MeshCore and log all incoming events.",
    )
    parser.add_argument(
        "--send-test",
        metavar="PUBKEY_PREFIX",
        help="Send a test message to the specified pubkey prefix.",
    )
    parser.add_argument(
        "--text",
        default="mcbot monitor test",
        help="Message text for --send-test (default: 'mcbot monitor test').",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="Seconds to listen for events with --listen (default: 30).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help=(
            "With --listen: print the full raw JSON payload for every event. "
            "Useful for inspecting exact field names sent by MeshCore firmware."
        ),
    )
    parser.add_argument(
        "--watch-start",
        action="store_true",
        help=(
            "With --listen: print a highlighted banner whenever an inbound "
            "message matches the 'start' / 'new' / 'begin' command (including "
            "with leading / or ! prefix).  Useful for verifying that the bot "
            "will receive and recognise the command before re-enabling it."
        ),
    )
    return parser


def _dispatch(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Dispatch to the appropriate command(s) based on parsed arguments."""
    if not any([args.info, args.list_serial, args.listen, args.send_test]):
        parser.print_help()
        print()
        cmd_info()
        return

    if args.info:
        cmd_info()

    if args.list_serial:
        cmd_list_serial()

    if args.listen:
        asyncio.run(
            cmd_listen(
                args.duration,
                debug=args.debug,
                watch_start=args.watch_start,
            )
        )

    if args.send_test:
        asyncio.run(cmd_send_test(args.send_test, args.text))


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    _dispatch(args, parser)


if __name__ == "__main__":
    main()
