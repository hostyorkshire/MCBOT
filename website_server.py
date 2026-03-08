#!/usr/bin/env python3
"""Minimal HTTP server that serves the ``website/`` folder.

This is intentionally simple – it wraps Python's built-in
``http.server`` so the static site can be hosted on a Raspberry Pi
Zero without installing nginx or any third-party package.

Usage::

    # Serve on the default port (8080):
    python website_server.py

    # Serve on a custom port:
    python website_server.py --port 8080

    # Bind to all interfaces (required so cloudflared can reach it):
    python website_server.py --host 0.0.0.0 --port 8080

Environment variables (override CLI defaults)::

    WEBSITE_HOST   bind address (default: 127.0.0.1)
    WEBSITE_PORT   TCP port      (default: 8080)
"""

from __future__ import annotations

import argparse
import http.server
import os
import pathlib
import socketserver


# ---------------------------------------------------------------------------
# Resolve the website directory relative to this script so the server can be
# started from any working directory.
# ---------------------------------------------------------------------------
WEBSITE_DIR = pathlib.Path(__file__).parent / "website"


class _Handler(http.server.SimpleHTTPRequestHandler):
    """Serve files from WEBSITE_DIR and suppress noisy access logs."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEBSITE_DIR), **kwargs)

    def log_message(self, fmt: str, *args) -> None:  # noqa: D102
        # Keep systemd journal tidy – only log errors (non-2xx responses).
        if args and len(args) >= 2:
            try:
                status_code = int(str(args[1]).split()[0])
                if 200 <= status_code < 300:
                    return
            except (ValueError, IndexError):
                pass
        super().log_message(fmt, *args)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve the MCBOT static website from the Raspberry Pi."
    )
    parser.add_argument(
        "--host",
        default=os.getenv("WEBSITE_HOST", "127.0.0.1"),
        help="Bind address (default: 127.0.0.1).  Use 0.0.0.0 to expose on all interfaces.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("WEBSITE_PORT", "8080")),
        help="TCP port (default: 8080).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if not WEBSITE_DIR.is_dir():
        raise SystemExit(f"ERROR: website directory not found: {WEBSITE_DIR}")

    # Allow the OS to reuse the port immediately after the server stops.
    socketserver.TCPServer.allow_reuse_address = True

    with socketserver.TCPServer((args.host, args.port), _Handler) as httpd:
        print(f"Serving {WEBSITE_DIR} at http://{args.host}:{args.port}/")
        print("Press Ctrl-C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")


if __name__ == "__main__":
    main()
