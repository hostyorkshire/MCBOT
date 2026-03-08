#!/usr/bin/env python3
"""MeshCore Radio Configurator – interactive serial configuration tool.

Configures a MeshCore-compatible LoRa radio over a USB serial connection
on Linux/Ubuntu.  Designed for **UK/EU 868 MHz band** operation by default.

⚠  FREQUENCY WARNING ⚠
  The default frequency is 869.525 MHz (UK/EU ISM band, sub-band g1).
  Do NOT set the frequency to 915 MHz unless you are in a region where
  915 MHz LoRa is permitted (e.g. USA, Canada, Australia).
  Using 915 MHz in the UK/EU is ILLEGAL and may cause interference.

Usage::

    # Interactive menu (recommended)
    .venv/bin/python meshcore_radio_config.py

    # Specify the serial port up front (skip port-selection prompt)
    .venv/bin/python meshcore_radio_config.py --port /dev/ttyUSB0

    # Open a raw serial shell (send arbitrary CLI commands)
    .venv/bin/python meshcore_radio_config.py --shell --port /dev/ttyUSB0

    # Non-interactive: apply settings and reboot in one shot
    .venv/bin/python meshcore_radio_config.py --port /dev/ttyUSB0 \\
        --freq 869.525 --name "MyNode" --lat 53.8 --lon -1.5 --reboot
"""

from __future__ import annotations

import argparse
import glob as _glob
import logging
import os
import stat
import sys
import time
from dataclasses import dataclass

try:
    import serial  # type: ignore[import-untyped]

    _SERIAL_AVAILABLE = True
except ImportError:
    _SERIAL_AVAILABLE = False

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_BAUD_RATE: int = 115200
DEFAULT_FREQ_MHZ: float = 869.525  # UK/EU ISM sub-band g1 (up to 500 mW, 10% DC)
READ_TIMEOUT: float = 2.0  # seconds to wait for a response line

# Approximate frequency bounds for the UK/EU 868-band channels.
# Anything below this is almost certainly a 433 MHz or wrong-band value;
# anything above is likely a 915-band value.
FREQ_EU868_MIN: float = 863.0
FREQ_EU868_MAX: float = 870.0

# Candidate serial port glob patterns (Linux/Ubuntu).
SERIAL_PORT_PATTERNS: list[str] = [
    "/dev/ttyUSB*",
    "/dev/ttyACM*",
]

_EU868_WARNING = """\
╔══════════════════════════════════════════════════════════════╗
║  ⚠  FREQUENCY / REGULATORY WARNING                          ║
║                                                              ║
║  This tool defaults to 869.525 MHz (UK/EU 868-band).         ║
║                                                              ║
║  • Valid EU/UK LoRa range : 863 – 870 MHz                    ║
║  • Do NOT use 915 MHz in the UK or EU – it is ILLEGAL.       ║
║  • 915 MHz is only permitted in USA/Canada/Australia.        ║
╚══════════════════════════════════════════════════════════════╝
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _separator(title: str = "", width: int = 60) -> str:
    """Return a Unicode box-drawing separator line."""
    if title:
        side = (width - len(title) - 2) // 2
        return f"{'─' * side} {title} {'─' * (width - side - len(title) - 2)}"
    return "─" * width


def list_serial_ports() -> list[str]:
    """Return a sorted list of candidate serial device paths."""
    found: list[str] = []
    for pattern in SERIAL_PORT_PATTERNS:
        found.extend(sorted(_glob.glob(pattern)))
    return found


def describe_port(path: str) -> str:
    """Return a one-line description of *path* including permissions."""
    try:
        st = os.stat(path)
        mode = stat.filemode(st.st_mode)
        try:
            import grp
            import pwd

            owner = pwd.getpwuid(st.st_uid).pw_name
            group = grp.getgrgid(st.st_gid).gr_name
        except (ImportError, KeyError):
            owner = str(st.st_uid)
            group = str(st.st_gid)

        rw_ok = os.access(path, os.R_OK | os.W_OK)
        rw_tag = "rw-ok" if rw_ok else "NO rw access – see dialout group instructions"
        return f"{path}  {mode}  {owner}:{group}  [{rw_tag}]"
    except OSError as exc:
        return f"{path}  (stat failed: {exc})"


def validate_frequency(freq_mhz: float) -> tuple[bool, str]:
    """Return ``(True, "")`` if *freq_mhz* is acceptable, else ``(False, reason)``."""
    if freq_mhz <= 0:
        return False, "Frequency must be a positive number."
    if freq_mhz >= 900:
        return (
            False,
            f"{freq_mhz} MHz looks like a 915-band value. "
            "This is NOT legal in the UK/EU. "
            "For UK/EU, use a value between 863 and 870 MHz.",
        )
    if not (FREQ_EU868_MIN <= freq_mhz <= FREQ_EU868_MAX):
        return (
            False,
            f"{freq_mhz} MHz is outside the UK/EU 868-band range "
            f"({FREQ_EU868_MIN}–{FREQ_EU868_MAX} MHz). "
            "Please double-check your intended frequency.",
        )
    return True, ""


def validate_node_name(name: str) -> tuple[bool, str]:
    """Return ``(True, "")`` if *name* is acceptable, else ``(False, reason)``."""
    name = name.strip()
    if not name:
        return False, "Node name must not be empty."
    if len(name) > 32:
        return False, f"Node name is too long ({len(name)} chars); maximum is 32."
    if " " in name:
        return False, "Node name must not contain spaces (use hyphens or underscores)."
    return True, ""


def validate_latitude(lat: float) -> tuple[bool, str]:
    """Return ``(True, "")`` if *lat* is a valid WGS-84 latitude."""
    if not (-90.0 <= lat <= 90.0):
        return False, f"Latitude must be between -90 and 90 (got {lat})."
    return True, ""


def validate_longitude(lon: float) -> tuple[bool, str]:
    """Return ``(True, "")`` if *lon* is a valid WGS-84 longitude."""
    if not (-180.0 <= lon <= 180.0):
        return False, f"Longitude must be between -180 and 180 (got {lon})."
    return True, ""


def build_set_command(key: str, value: str | float) -> str:
    """Build a ``set <key> <value>`` CLI command string."""
    return f"set {key} {value}"


def build_commands(
    freq_mhz: float | None = None,
    name: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> list[str]:
    """Return a list of ``set`` CLI commands for the supplied settings."""
    cmds: list[str] = []
    if freq_mhz is not None:
        cmds.append(build_set_command("freq", freq_mhz))
    if name is not None:
        cmds.append(build_set_command("name", name))
    if lat is not None:
        cmds.append(build_set_command("lat", lat))
    if lon is not None:
        cmds.append(build_set_command("lon", lon))
    return cmds


# ---------------------------------------------------------------------------
# Settings container
# ---------------------------------------------------------------------------


@dataclass
class RadioSettings:
    """Mutable collection of pending radio settings."""

    freq_mhz: float | None = None
    name: str | None = None
    lat: float | None = None
    lon: float | None = None

    def summary(self) -> list[str]:
        """Return a list of human-readable setting lines."""
        lines = []
        lines.append(
            f"  Frequency  : {self.freq_mhz} MHz"
            if self.freq_mhz is not None
            else "  Frequency  : (not set)"
        )
        lines.append(
            f"  Node name  : {self.name}" if self.name is not None else "  Node name  : (not set)"
        )
        lines.append(
            f"  Latitude   : {self.lat}" if self.lat is not None else "  Latitude   : (not set)"
        )
        lines.append(
            f"  Longitude  : {self.lon}" if self.lon is not None else "  Longitude  : (not set)"
        )
        return lines

    def to_commands(self) -> list[str]:
        """Convert current settings to a list of MeshCore CLI commands."""
        return build_commands(
            freq_mhz=self.freq_mhz,
            name=self.name,
            lat=self.lat,
            lon=self.lon,
        )


# ---------------------------------------------------------------------------
# Serial I/O
# ---------------------------------------------------------------------------


def open_serial(port: str, baud: int = DEFAULT_BAUD_RATE) -> serial.Serial:
    """Open *port* at *baud* and return the ``serial.Serial`` object.

    Raises ``serial.SerialException`` on failure (e.g. port busy or not found).
    Raises ``PermissionError`` if the current user cannot access the device.
    """
    if not _SERIAL_AVAILABLE:
        raise ImportError(
            "pyserial is not installed. Run: .venv/bin/pip install -r requirements.txt"
        )

    # Check access before attempting to open (gives a clearer error message).
    if os.path.exists(port) and not os.access(port, os.R_OK | os.W_OK):
        current_user = os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
        raise PermissionError(
            f"Cannot access {port}. "
            f"Add your user to the dialout group:\n"
            f"  sudo usermod -a -G dialout {current_user} && newgrp dialout"
        )

    return serial.Serial(port, baud, timeout=READ_TIMEOUT)


def send_command(ser: serial.Serial, cmd: str) -> str:
    """Send *cmd* over *ser* and return the response line(s)."""
    line = (cmd.strip() + "\n").encode()
    ser.write(line)
    ser.flush()
    # Collect response until a blank line or timeout.
    response_lines: list[str] = []
    deadline = time.monotonic() + READ_TIMEOUT
    while time.monotonic() < deadline:
        raw = ser.readline()
        if not raw:
            break
        decoded = raw.decode(errors="replace").rstrip()
        if decoded:
            response_lines.append(decoded)
        else:
            break
    return "\n".join(response_lines)


def apply_settings(ser: serial.Serial, settings: RadioSettings) -> list[tuple[str, str]]:
    """Send all pending settings to the radio.

    Returns a list of ``(command, response)`` tuples.
    """
    results: list[tuple[str, str]] = []
    for cmd in settings.to_commands():
        resp = send_command(ser, cmd)
        print(f"  → {cmd}")
        if resp:
            print(f"    {resp}")
        results.append((cmd, resp))
    return results


def _is_hex_string(s: str) -> bool:
    """Return ``True`` if *s* is a non-empty string of hexadecimal characters."""
    return bool(s) and all(c in "0123456789abcdefABCDEF" for c in s)


def fetch_pubkey(ser: serial.Serial) -> str:
    """Send ``get pubkey`` to the radio and return the raw response."""
    return send_command(ser, "get pubkey")


def parse_pubkey_from_response(response: str) -> str | None:
    """Extract the hex public key from a ``get pubkey`` response.

    Handles response formats such as:
    - ``pubkey: abcdef1234...``
    - ``OK abcdef1234...``
    - a bare hex string

    Returns the key string if found, or ``None`` if no recognisable key is
    present in the response.
    """
    for line in response.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Handle "key: <hex>" or "pubkey: <hex>" style lines.
        if ":" in stripped:
            key_part = stripped.split(":", 1)[1].strip()
            if _is_hex_string(key_part) and len(key_part) >= 8:
                return key_part
        # Handle "OK <hex>" style lines.
        if stripped.upper().startswith("OK "):
            candidate = stripped[3:].strip()
            if _is_hex_string(candidate) and len(candidate) >= 8:
                return candidate
        # Handle a bare hex string (>= 16 chars to avoid false positives).
        if _is_hex_string(stripped) and len(stripped) >= 16:
            return stripped
    return None


def _print_pubkey(ser: serial.Serial) -> None:
    """Fetch the radio public key and print it to stdout."""
    resp = fetch_pubkey(ser)
    key = parse_pubkey_from_response(resp)
    if key:
        print(f"  Radio public key : {key}")
    else:
        print("  Radio public key : (could not retrieve)")


# ---------------------------------------------------------------------------
# Interactive helpers
# ---------------------------------------------------------------------------


def _prompt(prompt_text: str, default: str = "") -> str:
    """Read a line from stdin, returning *default* if the user presses Enter."""
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{prompt_text}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return value if value else default


def _prompt_float(prompt_text: str, default: float | None = None) -> float | None:
    """Prompt for a float value, returning *default* on empty input."""
    default_str = str(default) if default is not None else ""
    while True:
        raw = _prompt(prompt_text, default_str)
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            print(f"  ✗ Invalid number: {raw!r}")


def _select_port(preselected: str | None = None) -> str | None:
    """Interactively select a serial port.  Returns the chosen path or ``None``."""
    ports = list_serial_ports()

    if preselected:
        # Validate the pre-selected port exists/is accessible.
        if not os.path.exists(preselected):
            print(f"  WARNING: {preselected} does not exist.")
        return preselected

    print(_separator("Select Serial Port"))
    if not ports:
        print(
            "  No serial devices found matching /dev/ttyUSB* or /dev/ttyACM*.\n"
            "  Connect your device and re-run, or specify --port /dev/ttyXXX."
        )
        custom = _prompt("  Enter port path manually (or leave blank to cancel)")
        return custom or None

    for i, p in enumerate(ports, start=1):
        print(f"  {i}. {describe_port(p)}")
    print(f"  {len(ports) + 1}. Enter a custom path")

    while True:
        choice = _prompt("Select", "1")
        if not choice:
            return None
        try:
            idx = int(choice) - 1
        except ValueError:
            print("  ✗ Enter a number.")
            continue
        if 0 <= idx < len(ports):
            return ports[idx]
        if idx == len(ports):
            custom = _prompt("  Custom port path")
            return custom or None
        print(f"  ✗ Choose between 1 and {len(ports) + 1}.")


# ---------------------------------------------------------------------------
# Interactive menu
# ---------------------------------------------------------------------------


def _menu_set_frequency(settings: RadioSettings) -> None:
    """Prompt the user to update the frequency setting."""
    print(_separator("Set Frequency"))
    print(
        "  UK/EU 868-band valid range: 863 – 870 MHz\n"
        "  Suggested: 869.525 MHz (sub-band g1, up to 500 mW)"
    )
    freq = _prompt_float("  Frequency (MHz)", DEFAULT_FREQ_MHZ)
    if freq is None:
        print("  No change.")
        return
    ok, reason = validate_frequency(freq)
    if not ok:
        print(f"  ✗ {reason}")
        return
    settings.freq_mhz = freq
    print(f"  ✓ Frequency set to {freq} MHz (pending apply).")


def _menu_set_name(settings: RadioSettings) -> None:
    """Prompt the user to update the node name."""
    print(_separator("Set Node Name"))
    default = settings.name or ""
    name = _prompt("  Node name (no spaces)", default)
    if not name:
        print("  No change.")
        return
    ok, reason = validate_node_name(name)
    if not ok:
        print(f"  ✗ {reason}")
        return
    settings.name = name
    print(f"  ✓ Name set to {name!r} (pending apply).")


def _menu_set_lat(settings: RadioSettings) -> None:
    """Prompt the user to update the latitude."""
    print(_separator("Set Latitude"))
    lat = _prompt_float("  Latitude (decimal degrees, e.g. 53.8 for Leeds)", settings.lat)
    if lat is None:
        print("  No change.")
        return
    ok, reason = validate_latitude(lat)
    if not ok:
        print(f"  ✗ {reason}")
        return
    settings.lat = lat
    print(f"  ✓ Latitude set to {lat} (pending apply).")


def _menu_set_lon(settings: RadioSettings) -> None:
    """Prompt the user to update the longitude."""
    print(_separator("Set Longitude"))
    lon = _prompt_float("  Longitude (decimal degrees, e.g. -1.5 for Leeds)", settings.lon)
    if lon is None:
        print("  No change.")
        return
    ok, reason = validate_longitude(lon)
    if not ok:
        print(f"  ✗ {reason}")
        return
    settings.lon = lon
    print(f"  ✓ Longitude set to {lon} (pending apply).")


def _menu_apply(ser: serial.Serial, settings: RadioSettings, reboot: bool = False) -> None:
    """Apply pending settings (and optionally reboot)."""
    cmds = settings.to_commands()
    if not cmds and not reboot:
        print("  No settings to apply.  Configure something first.")
        return

    print(_separator("Applying Settings"))
    if cmds:
        apply_settings(ser, settings)
    else:
        print("  (no pending settings to send)")

    if reboot:
        print("  Sending reboot command…")
        resp = send_command(ser, "reboot")
        if resp:
            print(f"  {resp}")
        print("  ✓ Reboot command sent.  The device will restart.")
    else:
        print("  ✓ Settings applied.  Use option 6 to apply AND reboot.")


def _menu_shell(ser: serial.Serial) -> None:
    """Drop into a raw serial shell for manual command entry."""
    print(_separator("Manual Serial Shell"))
    print("  Type MeshCore CLI commands and press Enter.  Type 'exit' to return.")
    print("  Example commands: show config, show nodes, set tx_power 20")
    print(_separator())
    while True:
        try:
            cmd = input("  shell> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if cmd.lower() in ("exit", "quit", "q"):
            break
        if not cmd:
            continue
        resp = send_command(ser, cmd)
        if resp:
            print(f"  {resp}")
        else:
            print("  (no response)")


def run_interactive_menu(port: str, baud: int = DEFAULT_BAUD_RATE) -> None:
    """Open *port* and run the interactive configuration menu."""
    print(_EU868_WARNING)

    print(_separator("Opening Serial Port"))
    print(f"  Port : {port}  |  Baud : {baud}")
    try:
        ser = open_serial(port, baud)
    except PermissionError as exc:
        print(f"\n  ✗ Permission denied: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(
            f"\n  ✗ Could not open {port}: {exc}\n"
            "  Possible causes:\n"
            "  • Port is already in use (stop cyoa_bot.py first)\n"
            "  • Device not connected or firmware not running\n"
            "  • Wrong port – run mcbot_monitor.py --list-serial to see devices",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"  ✓ Opened {port} at {baud} baud.")
    _print_pubkey(ser)

    settings = RadioSettings()

    menu_items = [
        ("Set frequency (MHz)", _menu_set_frequency),
        ("Set node name", _menu_set_name),
        ("Set latitude", _menu_set_lat),
        ("Set longitude", _menu_set_lon),
        ("Show radio public key", None),
        ("Show pending settings", None),
        ("Apply settings (no reboot)", None),
        ("Apply settings + reboot", None),
        ("Manual serial shell", None),
    ]

    try:
        while True:
            print()
            print(_separator("MeshCore Radio Configurator"))
            for i, (label, _) in enumerate(menu_items, start=1):
                print(f"  {i}. {label}")
            print("  0. Quit")
            print(_separator())

            choice = _prompt("Choice", "0")
            if choice == "0":
                break

            try:
                idx = int(choice) - 1
            except ValueError:
                print("  ✗ Enter a number.")
                continue

            if idx < 0 or idx >= len(menu_items):
                print(f"  ✗ Choose between 0 and {len(menu_items)}.")
                continue

            label, handler = menu_items[idx]

            if handler is not None:
                handler(settings)  # type: ignore[call-arg]
            elif idx == 4:  # Show radio public key
                print(_separator("Radio Public Key"))
                _print_pubkey(ser)
            elif idx == 5:  # Show pending settings
                print(_separator("Pending Settings"))
                for line in settings.summary():
                    print(line)
            elif idx == 6:  # Apply (no reboot)
                _menu_apply(ser, settings, reboot=False)
            elif idx == 7:  # Apply + reboot
                _menu_apply(ser, settings, reboot=True)
            elif idx == 8:  # Shell
                _menu_shell(ser)

    finally:
        ser.close()
        print("\n  Serial port closed.")


# ---------------------------------------------------------------------------
# Non-interactive (CLI flags) mode
# ---------------------------------------------------------------------------


def run_non_interactive(args: argparse.Namespace) -> None:
    """Apply settings from CLI flags without an interactive menu."""
    print(_EU868_WARNING)

    settings = RadioSettings()
    errors: list[str] = []

    if args.freq is not None:
        ok, reason = validate_frequency(args.freq)
        if ok:
            settings.freq_mhz = args.freq
        else:
            errors.append(f"--freq: {reason}")

    if args.name is not None:
        ok, reason = validate_node_name(args.name)
        if ok:
            settings.name = args.name
        else:
            errors.append(f"--name: {reason}")

    if args.lat is not None:
        ok, reason = validate_latitude(args.lat)
        if ok:
            settings.lat = args.lat
        else:
            errors.append(f"--lat: {reason}")

    if args.lon is not None:
        ok, reason = validate_longitude(args.lon)
        if ok:
            settings.lon = args.lon
        else:
            errors.append(f"--lon: {reason}")

    if errors:
        for err in errors:
            print(f"  ✗ {err}", file=sys.stderr)
        sys.exit(1)

    cmds = settings.to_commands()
    if not cmds and not args.reboot:
        print("  Nothing to do. Provide at least one of: --freq, --name, --lat, --lon, --reboot")
        sys.exit(0)

    port = args.port
    print(_separator("Opening Serial Port"))
    print(f"  Port : {port}  |  Baud : {args.baud}")
    try:
        ser = open_serial(port, args.baud)
    except PermissionError as exc:
        print(f"\n  ✗ {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"\n  ✗ Could not open {port}: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"  ✓ Opened {port} at {args.baud} baud.")
    try:
        if cmds:
            print(_separator("Applying Settings"))
            apply_settings(ser, settings)
        if args.reboot:
            print("  Sending reboot command…")
            resp = send_command(ser, "reboot")
            if resp:
                print(f"  {resp}")
            print("  ✓ Reboot command sent.")
        _print_pubkey(ser)
    finally:
        ser.close()


# ---------------------------------------------------------------------------
# Shell mode
# ---------------------------------------------------------------------------


def run_shell_mode(args: argparse.Namespace) -> None:
    """Open *args.port* and immediately drop into the manual serial shell."""
    print(_EU868_WARNING)
    print(_separator("Opening Serial Port"))
    print(f"  Port : {args.port}  |  Baud : {args.baud}")
    try:
        ser = open_serial(args.port, args.baud)
    except PermissionError as exc:
        print(f"\n  ✗ {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"\n  ✗ Could not open {args.port}: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"  ✓ Opened {args.port} at {args.baud} baud.")
    try:
        _menu_shell(ser)
    finally:
        ser.close()
        print("\n  Serial port closed.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meshcore_radio_config",
        description=(
            "MeshCore Radio Configurator – configure a MeshCore LoRa radio "
            "over a serial port.  Defaults to UK/EU 868-band frequencies."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--port",
        metavar="DEVICE",
        help="Serial device path, e.g. /dev/ttyUSB0.  "
        "If omitted, an interactive port-selection prompt is shown.",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=DEFAULT_BAUD_RATE,
        metavar="RATE",
        help=f"Baud rate (default: {DEFAULT_BAUD_RATE}).",
    )
    parser.add_argument(
        "--freq",
        type=float,
        metavar="MHZ",
        help=f"Frequency in MHz (UK/EU default: {DEFAULT_FREQ_MHZ}).  "
        "Must be in the 863–870 MHz range for UK/EU operation.",
    )
    parser.add_argument(
        "--name",
        metavar="NAME",
        help="Node name (no spaces, max 32 chars).",
    )
    parser.add_argument(
        "--lat",
        type=float,
        metavar="DEGREES",
        help="Latitude in decimal degrees (e.g. 53.8 for Leeds).",
    )
    parser.add_argument(
        "--lon",
        type=float,
        metavar="DEGREES",
        help="Longitude in decimal degrees (e.g. -1.5 for Leeds).",
    )
    parser.add_argument(
        "--reboot",
        action="store_true",
        help="Send a reboot command after applying settings.",
    )
    parser.add_argument(
        "--shell",
        action="store_true",
        help="Open a raw serial shell for manual command entry.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # --shell mode: open serial shell directly.
    if args.shell:
        if not args.port:
            args.port = _select_port()
            if not args.port:
                print("  No port selected.  Exiting.", file=sys.stderr)
                sys.exit(1)
        run_shell_mode(args)
        return

    # Non-interactive mode: any setting flag was provided.
    has_setting = any(getattr(args, key) is not None for key in ("freq", "name", "lat", "lon"))
    if has_setting or args.reboot:
        if not args.port:
            print(
                "  ✗ --port is required when using non-interactive flags.",
                file=sys.stderr,
            )
            sys.exit(1)
        run_non_interactive(args)
        return

    # Interactive menu mode.
    port = args.port or _select_port()
    if not port:
        print("  No port selected.  Exiting.", file=sys.stderr)
        sys.exit(1)
    run_interactive_menu(port, args.baud)


if __name__ == "__main__":
    main()
