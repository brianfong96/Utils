"""Opt-in real UIA integration against an isolated local Chromium fixture."""

import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import psutil
from pywinauto import Desktop

from automation import (
    AutomationError, Target, composer, document_url, read_text,
    send_to_window, trusted_window,
)


@unittest.skipUnless(os.environ.get("CONTINUE_TIMER_NATIVE_TEST") == "1",
                     "Set CONTINUE_TIMER_NATIVE_TEST=1 to open an isolated Chromium fixture.")
class NativeIntegrationTests(unittest.TestCase):
    def test_real_accessible_composer_and_enter(self):
        browsers = [
            Path(os.environ["ProgramFiles"]) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ["ProgramFiles(x86)"]) / "Microsoft/Edge/Application/msedge.exe",
        ]
        browser = next((candidate for candidate in browsers if candidate.exists()), None)
        if browser is None:
            self.skipTest("Chrome or Edge not installed")
        content = Path(__file__).with_name("fixture.html").read_bytes()

        class FixtureHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        fixture = f"http://127.0.0.1:{server.server_port}/"
        with tempfile.TemporaryDirectory(prefix="continue-timer-fixture-") as profile:
            arguments = [
                str(browser), f"--user-data-dir={profile}", "--no-first-run",
                "--no-default-browser-check", "--force-renderer-accessibility",
                "--disable-sync", "--disable-features=msImplicitSignin",
                "--enable-features=UiaProvider", "--disable-gpu",
                "--new-window", fixture,
            ]
            if os.environ.get("CONTINUE_TIMER_TEST_NO_SANDBOX") == "1":
                arguments.append("--no-sandbox")
            process = subprocess.Popen(arguments)
            window = None
            field = None
            try:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    matches = Desktop(backend="uia").windows(
                        title_re=".*Continue Timer integration fixture.*", process=process.pid,
                    )
                    if len(matches) == 1:
                        window = matches[0]
                        window.set_focus()
                        try:
                            field = composer(window)
                        except AutomationError:
                            pass
                        else:
                            break
                    time.sleep(0.2)
                self.assertIsNotNone(window, "The isolated fixture window did not appear.")
                self.assertIsNotNone(field, "Fixture composer was not exposed to UIA.")
                target = Target(window.handle, window.process_id(), window.window_text(),
                                tuple(field.element_info.runtime_id), fixture)
                self.assertEqual(read_text(field), "")
                self.assertEqual(document_url(window), fixture)
                self.assertFalse(trusted_window(window), "Local fixture must not pass production URL checks.")
                # Only the known, isolated local fixture bypasses ChatGPT URL validation.
                with patch("automation.trusted_window", return_value=True):
                    try:
                        send_to_window(window, target)
                    except AutomationError as error:
                        self.fail(f"{error}; fixture value={read_text(field)!r}, "
                                  f"focused={field.has_keyboard_focus()}")
                self.assertEqual(read_text(field), "")
                self.assertTrue(window.descendants(title="Received: continue"))
            finally:
                try:
                    parent = psutil.Process(process.pid)
                    owned_processes = parent.children(recursive=True) + [parent]
                except psutil.NoSuchProcess:
                    owned_processes = []
                if window is not None:
                    window.close()
                _, alive = psutil.wait_procs(owned_processes, timeout=3)
                for owned in alive:
                    try:
                        owned.terminate()
                    except psutil.NoSuchProcess:
                        pass
                psutil.wait_procs(alive, timeout=5)
                process.wait(timeout=10)
