#!/usr/bin/env python3
"""Launch the Disk Usage Report application on localhost."""

from __future__ import annotations

import argparse
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Disk Usage Report web application on localhost."
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        choices=("127.0.0.1", "localhost"),
        help="Interface to bind (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port to bind (default: 8765; use 0 to select a free port).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the application in the default browser.",
    )
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    return args


def main() -> int:
    args = parse_args()
    try:
        from web_server import serve

        serve(args.host, args.port, open_browser=not args.no_browser)
    except KeyboardInterrupt:
        print("\nServer stopped.")
    except OSError as error:
        print(f"Could not start server: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
