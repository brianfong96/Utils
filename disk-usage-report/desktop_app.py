#!/usr/bin/env python3
"""Native desktop entry point for Disk Usage Report."""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
import threading
import urllib.request
from pathlib import Path

from web_server import create_server


APP_NAME = "Disk Usage Report"
HOST = "127.0.0.1"
PORT = 5336
SMOKE_ASSETS = {
    "/": b"<!doctype html>",
    "/assets/app.css": b":root",
    "/assets/app.js": b'"use strict"',
    "/assets/app-icon.png?v=2": b"\x89PNG\r\n\x1a\n",
    "/apple-touch-icon.png?v=2": b"\x89PNG\r\n\x1a\n",
    "/favicon.ico?v=2": b"\x00\x00\x01\x00",
}


def application_history_directory() -> Path:
    """Return durable per-user storage outside a one-file bundle."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "DiskUsageReport" / "history"
    return Path.home() / ".disk-usage-report" / "history"


def migrate_source_history(destination: Path) -> None:
    """Copy existing source-mode history on the first desktop run."""
    if (destination / "index.json").exists():
        return
    candidates = [Path(__file__).with_name(".history")]
    if getattr(sys, "frozen", False):
        executable_directory = Path(sys.executable).resolve().parent
        candidates.extend(
            [
                executable_directory / ".history",
                executable_directory.parent / ".history",
            ]
        )
    for source in candidates:
        if source != destination and (source / "index.json").is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination, dirs_exist_ok=True)
            return


def show_error(message: str) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
    else:
        print(f"{APP_NAME}: {message}", file=sys.stderr)


def smoke_test_assets(base_url: str) -> bool:
    """Verify every packaged UI asset needed to draw the branded window."""
    for path, signature in SMOKE_ASSETS.items():
        with urllib.request.urlopen(base_url + path, timeout=10) as response:
            content_matches = response.read(len(signature)).startswith(signature)
            if response.status != 200 or not content_matches:
                return False
    return True


def run() -> int:
    history_directory = application_history_directory()
    migrate_source_history(history_directory)
    smoke_test = "--smoke-test" in sys.argv
    requested_port = 0 if smoke_test else PORT
    try:
        server = create_server(HOST, requested_port, history_directory)
    except OSError as error:
        show_error(
            f"The app could not start on port {PORT}. It may already be running.\n\n"
            f"{error}"
        )
        return 1

    server_thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.25},
        name="disk-usage-report-server",
        daemon=True,
    )
    server_thread.start()
    actual_port = server.server_address[1]
    url = f"http://{HOST}:{actual_port}/"

    try:
        if smoke_test:
            return 0 if smoke_test_assets(url.rstrip("/")) else 1

        try:
            import webview
        except ImportError:
            show_error(
                "The desktop window dependency is missing. Run the build script "
                "or install the packages in requirements.txt."
            )
            return 1

        webview.create_window(
            APP_NAME,
            url,
            width=1280,
            height=840,
            min_size=(760, 560),
            background_color="#090d16",
        )
        webview.start(debug=False)
        return 0
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(run())
