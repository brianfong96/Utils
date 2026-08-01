"""Local HTTP API and static-file server for the disk usage application."""

from __future__ import annotations

import gzip
import json
import os
import re
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import scanner


STATIC_DIRECTORY = Path(__file__).with_name("web")
MAX_REQUEST_BYTES = 64 * 1024
MAX_EVENTS = 20_000
HISTORY_LIMIT = 10
DEFAULT_HISTORY_DIRECTORY = Path(__file__).with_name(".history")
SCAN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class HistoryStore:
    """Persist a bounded set of compressed reports for later viewing."""

    def __init__(self, directory: Path = DEFAULT_HISTORY_DIRECTORY) -> None:
        self.directory = Path(directory)
        self.index_path = self.directory / "index.json"
        self._lock = threading.Lock()

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(entry) for entry in self._read_index()]

    def get(self, scan_id: str) -> dict[str, Any] | None:
        if not SCAN_ID_PATTERN.fullmatch(scan_id):
            return None
        with self._lock:
            entry = next(
                (item for item in self._read_index() if item.get("id") == scan_id),
                None,
            )
            if entry is None:
                return None
            try:
                with gzip.open(
                    self.directory / f"{scan_id}.json.gz", "rt", encoding="utf-8"
                ) as report_file:
                    report = json.load(report_file)
            except (OSError, json.JSONDecodeError):
                return None
            return report if isinstance(report, dict) else None

    def save(
        self,
        scan_id: str,
        report: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        if not SCAN_ID_PATTERN.fullmatch(scan_id):
            raise ValueError("Invalid scan identifier.")

        totals = {"bytes": 0, "files": 0, "directories": 0, "skipped": 0}
        for drive in report.get("drives", []):
            scan = drive.get("scan", {})
            for key in totals:
                totals[key] += int(scan.get(key, 0) or 0)
        entry = {
            "id": scan_id,
            "created_at": report.get("created_at"),
            "duration_seconds": report.get("duration_seconds", 0),
            "roots": list(config["roots"]),
            "workers": config["workers"],
            "depth": config["depth"],
            "drive_count": len(report.get("drives", [])),
            **totals,
        }

        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            report_path = self.directory / f"{scan_id}.json.gz"
            report_temporary = self.directory / f"{scan_id}.json.gz.tmp"
            with gzip.open(report_temporary, "wt", encoding="utf-8") as report_file:
                json.dump(report, report_file, separators=(",", ":"))
            os.replace(report_temporary, report_path)

            entries = [
                item for item in self._read_index() if item.get("id") != scan_id
            ]
            entries.insert(0, entry)
            removed = entries[HISTORY_LIMIT:]
            entries = entries[:HISTORY_LIMIT]

            index_temporary = self.directory / "index.json.tmp"
            index_temporary.write_text(
                json.dumps(entries, indent=2), encoding="utf-8"
            )
            os.replace(index_temporary, self.index_path)
            for old_entry in removed:
                old_id = old_entry.get("id")
                if isinstance(old_id, str) and SCAN_ID_PATTERN.fullmatch(old_id):
                    (self.directory / f"{old_id}.json.gz").unlink(missing_ok=True)
        return dict(entry)

    def _read_index(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        return [
            item
            for item in payload
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and SCAN_ID_PATTERN.fullmatch(item["id"])
        ][:HISTORY_LIMIT]


@dataclass
class ScanSession:
    scan_id: str
    config: dict[str, Any]
    status: str = "starting"
    error: str | None = None
    report: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    _events: list[dict[str, Any]] = field(default_factory=list)
    _sequence: int = 0
    _condition: threading.Condition = field(default_factory=threading.Condition)

    def emit(self, event_type: str, data: dict[str, Any]) -> None:
        with self._condition:
            self._sequence += 1
            self._events.append(
                {"id": self._sequence, "type": event_type, "data": data}
            )
            if len(self._events) > MAX_EVENTS:
                del self._events[: len(self._events) - MAX_EVENTS]
            self._condition.notify_all()

    def wait_for_events(
        self, after: int, timeout: float = 15.0
    ) -> list[dict[str, Any]]:
        with self._condition:
            available = [event for event in self._events if event["id"] > after]
            if not available and self.status not in {"complete", "error", "cancelled"}:
                self._condition.wait(timeout)
                available = [
                    event for event in self._events if event["id"] > after
                ]
            return available

    def public_state(self) -> dict[str, Any]:
        return {
            "id": self.scan_id,
            "status": self.status,
            "error": self.error,
            "config": self.config,
            "report": self.report if self.status == "complete" else None,
        }

    def latest_sequence(self) -> int:
        with self._condition:
            return self._sequence


class ScanManager:
    def __init__(self, history_store: HistoryStore) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, ScanSession] = {}
        self._active_id: str | None = None
        self._history_store = history_store

    def start(self, config: dict[str, Any]) -> ScanSession:
        with self._lock:
            if self._active_id:
                active = self._sessions.get(self._active_id)
                if active and active.status in {"starting", "running"}:
                    raise RuntimeError("A scan is already running.")
            self._sessions.clear()
            session = ScanSession(uuid.uuid4().hex, config)
            self._sessions[session.scan_id] = session
            self._active_id = session.scan_id

        thread = threading.Thread(
            target=self._run,
            args=(session,),
            name=f"scan-session-{session.scan_id[:8]}",
            daemon=True,
        )
        thread.start()
        return session

    def get(self, scan_id: str) -> ScanSession | None:
        with self._lock:
            return self._sessions.get(scan_id)

    def cancel(self, scan_id: str) -> bool:
        session = self.get(scan_id)
        if session is None or session.status not in {"starting", "running"}:
            return False
        session.cancel_event.set()
        session.emit("stage", {"stage": "cancelling", "message": "Stopping workers"})
        return True

    def _run(self, session: ScanSession) -> None:
        config = session.config
        session.status = "running"
        session.emit(
            "started",
            {
                "scan_id": session.scan_id,
                "workers": config["workers"],
                "depth": config["depth"],
                "roots": config["roots"],
            },
        )
        try:
            drives = scanner.discover_drives(config["roots"])
            if len(drives) != len(config["roots"]):
                raise ValueError("One or more scan paths became unavailable.")
            report = scanner.scan_drives(
                drives,
                config["depth"],
                config["workers"],
                config["split_depth"],
                False,
                progress_callback=session.emit,
                cancel_event=session.cancel_event,
            )
            if session.cancel_event.is_set():
                raise scanner.ScanCancelled
            session.report = report
            history_saved = True
            try:
                self._history_store.save(session.scan_id, report, config)
            except (OSError, ValueError):
                history_saved = False
            session.status = "complete"
            session.emit(
                "complete",
                {
                    "scan_id": session.scan_id,
                    "duration_seconds": report["duration_seconds"],
                    "history_saved": history_saved,
                },
            )
        except scanner.ScanCancelled:
            session.status = "cancelled"
            session.emit("cancelled", {"scan_id": session.scan_id})
        except Exception as error:  # Keep failures visible to the local UI.
            session.error = str(error) or error.__class__.__name__
            session.status = "error"
            session.emit("error", {"message": session.error})


def normalize_config(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        workers = int(payload.get("workers", scanner.DEFAULT_WORKERS))
        depth = int(payload.get("depth", 3))
    except (TypeError, ValueError) as error:
        raise ValueError("Workers and depth must be whole numbers.") from error
    if not 1 <= workers <= 64:
        raise ValueError("Workers must be between 1 and 64.")
    if not 1 <= depth <= 8:
        raise ValueError("Depth must be between 1 and 8.")

    supplied_roots = payload.get("roots")
    if not isinstance(supplied_roots, list) or not supplied_roots:
        raise ValueError("Select at least one drive or path.")
    if len(supplied_roots) > 32:
        raise ValueError("A scan can include at most 32 roots.")

    roots: list[str] = []
    seen: set[str] = set()
    for value in supplied_roots:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Every scan root must be a non-empty path.")
        path = os.path.abspath(os.path.expanduser(value.strip()))
        key = os.path.normcase(path)
        if key in seen:
            continue
        if not os.path.isdir(path):
            raise ValueError(f"Directory does not exist or is inaccessible: {path}")
        seen.add(key)
        roots.append(path)
    if not roots:
        raise ValueError("Select at least one unique drive or path.")

    return {
        "workers": workers,
        "depth": depth,
        "split_depth": 2,
        "roots": roots,
    }


class ApplicationServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        history_directory: Path = DEFAULT_HISTORY_DIRECTORY,
    ) -> None:
        super().__init__(address, RequestHandler)
        self.history_store = HistoryStore(history_directory)
        self.scan_manager = ScanManager(self.history_store)


class RequestHandler(BaseHTTPRequestHandler):
    server: ApplicationServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._serve_file("index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/assets/app.css":
            self._serve_file("app.css", "text/css; charset=utf-8")
            return
        if parsed.path == "/assets/app.js":
            self._serve_file("app.js", "text/javascript; charset=utf-8")
            return
        if parsed.path == "/api/config":
            drives = scanner.discover_drives()
            self._json(
                HTTPStatus.OK,
                {
                    "defaults": {
                        "workers": scanner.DEFAULT_WORKERS,
                        "depth": 3,
                    },
                    "drives": [
                        {
                            "path": str(drive.path),
                            "total": drive.total,
                            "used": drive.used,
                            "free": drive.free,
                        }
                        for drive in drives
                    ],
                },
            )
            return
        if parsed.path == "/api/history":
            self._json(HTTPStatus.OK, {"history": self.server.history_store.list()})
            return

        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 3 and parts[:2] == ["api", "history"]:
            report = self.server.history_store.get(parts[2])
            if report is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Saved scan not found."})
            else:
                self._json(HTTPStatus.OK, {"report": report})
            return
        if len(parts) == 3 and parts[:2] == ["api", "scans"]:
            session = self.server.scan_manager.get(parts[2])
            if session is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Scan not found."})
            else:
                self._json(HTTPStatus.OK, session.public_state())
            return
        if len(parts) == 4 and parts[:2] == ["api", "scans"] and parts[3] == "events":
            session = self.server.scan_manager.get(parts[2])
            if session is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Scan not found."})
            else:
                query = parse_qs(parsed.query)
                try:
                    after = int(
                        self.headers.get("Last-Event-ID")
                        or query.get("after", ["0"])[0]
                    )
                except ValueError:
                    after = 0
                self._event_stream(session, after)
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

    def do_POST(self) -> None:
        if not self._origin_allowed():
            self._json(HTTPStatus.FORBIDDEN, {"error": "Origin not allowed."})
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/scans":
            try:
                payload = self._read_json()
                config = normalize_config(payload)
                session = self.server.scan_manager.start(config)
            except ValueError as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            except RuntimeError as error:
                self._json(HTTPStatus.CONFLICT, {"error": str(error)})
                return
            self._json(
                HTTPStatus.ACCEPTED,
                {"id": session.scan_id, "status": session.status},
            )
            return

        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 4 and parts[:2] == ["api", "scans"] and parts[3] == "cancel":
            if self.server.scan_manager.cancel(parts[2]):
                self._json(HTTPStatus.ACCEPTED, {"status": "cancelling"})
            else:
                self._json(
                    HTTPStatus.CONFLICT,
                    {"error": "Scan is not running or does not exist."},
                )
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        hostname = urlparse(origin).hostname
        return hostname in {"127.0.0.1", "localhost", "::1"}

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Invalid request length.") from error
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("Request body is missing or too large.")
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Request body must be valid JSON.") from error
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def _serve_file(self, name: str, content_type: str) -> None:
        try:
            content = (STATIC_DIRECTORY / name).read_bytes()
        except OSError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Asset not found."})
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
            "frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(content)

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        content = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def _event_stream(self, session: ScanSession, after: int) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        cursor = after
        try:
            while True:
                events = session.wait_for_events(cursor)
                if not events:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                for event in events:
                    cursor = event["id"]
                    payload = json.dumps(event["data"], separators=(",", ":"))
                    message = (
                        f"id: {cursor}\n"
                        f"event: {event['type']}\n"
                        f"data: {payload}\n\n"
                    )
                    self.wfile.write(message.encode("utf-8"))
                    self.wfile.flush()
                if session.status in {"complete", "error", "cancelled"}:
                    latest = session.latest_sequence()
                    if cursor >= latest:
                        break
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            self.close_connection = True


def create_server(
    host: str = "127.0.0.1",
    port: int = 5336,
    history_directory: Path = DEFAULT_HISTORY_DIRECTORY,
) -> ApplicationServer:
    return ApplicationServer((host, port), history_directory)


def serve(
    host: str = "127.0.0.1",
    port: int = 5336,
    open_browser: bool = True,
) -> None:
    server = create_server(host, port)
    actual_port = server.server_address[1]
    url = f"http://{host}:{actual_port}/"
    print(f"Disk Usage Report is running at {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.25, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
