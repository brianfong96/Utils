#!/usr/bin/env python3
"""Parallel disk-scanning engine and optional batch-report CLI.

Examples:
    python scanner.py --snapshot disk-report.json
    python scanner.py --depth 3 --html disk-report.html
    python scanner.py --load-snapshot disk-report.json
    python scanner.py --drive C:\\ --workers 16 --top 25

For best cross-platform mount discovery, optionally install psutil:
    python -m pip install psutil
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import shutil
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SCHEMA_VERSION = 2
DEFAULT_WORKERS = min(16, max(4, (os.cpu_count() or 4) * 2))
ProgressCallback = Callable[[str, dict[str, Any]], None]


class ScanCancelled(Exception):
    """Raised when a running scan is cancelled."""


def emit_progress(
    callback: ProgressCallback | None, event_type: str, **payload: Any
) -> None:
    if callback is not None:
        callback(event_type, payload)


def ensure_not_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ScanCancelled


@dataclass(frozen=True)
class Drive:
    path: Path
    total: int
    used: int
    free: int


@dataclass
class DirectoryNode:
    path: str
    level: int
    parent: int | None
    children: list[int] = field(default_factory=list)
    stats: "ScanStats" = field(default_factory=lambda: ScanStats())


@dataclass
class ScanStats:
    bytes: int = 0
    files: int = 0
    directories: int = 0
    skipped: int = 0

    def add(self, other: "ScanStats") -> None:
        self.bytes += other.bytes
        self.files += other.files
        self.directories += other.directories
        self.skipped += other.skipped


@dataclass
class PreparedTarget:
    base: ScanStats
    jobs: list[str]


def human_size(value: int) -> str:
    """Format bytes in readable binary units."""
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if amount < 1024 or unit == "PiB":
            return f"{int(amount)} B" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    raise AssertionError("unreachable")


def human_count(value: int) -> str:
    return f"{value:,}"


def drive_from_path(path: Path) -> Drive | None:
    try:
        capacity = shutil.disk_usage(path)
    except OSError:
        return None
    return Drive(path, capacity.total, capacity.used, capacity.free)


def discover_drives(selected: list[str] | None = None) -> list[Drive]:
    """Find ready mounted drives, or use explicitly selected roots."""
    if selected:
        mount_points = [Path(os.path.abspath(path)) for path in selected]
    else:
        try:
            import psutil  # type: ignore[import-not-found]

            mount_points = [
                Path(partition.mountpoint)
                for partition in psutil.disk_partitions(all=False)
            ]
        except ImportError:
            if os.name == "nt":
                mask = ctypes.windll.kernel32.GetLogicalDrives()
                mount_points = [
                    Path(f"{chr(letter)}:\\")
                    for letter in range(65, 91)
                    if mask & (1 << (letter - 65))
                ]
            else:
                mount_points = [Path("/")]

    drives: list[Drive] = []
    seen: set[str] = set()
    for mount_point in mount_points:
        key = os.path.normcase(os.path.abspath(mount_point))
        if key in seen:
            continue
        drive = drive_from_path(mount_point)
        if drive is None:
            continue  # Empty removable disks or inaccessible shares.
        seen.add(key)
        drives.append(drive)
    return sorted(drives, key=lambda drive: drive.free)


def root_device(path: Path) -> int | None:
    if os.name == "nt":
        return None
    try:
        return os.stat(path, follow_symlinks=False).st_dev
    except OSError:
        return None


def is_same_filesystem(entry: os.DirEntry[str], device: int | None) -> bool:
    if device is None:
        return True
    return entry.stat(follow_symlinks=False).st_dev == device


def discover_directory_tree(
    drive: Drive,
    depth: int,
    progress_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[list[DirectoryNode], list[int], int | None]:
    """Build directory nodes through ``depth`` and count files above the leaves."""
    device = root_device(drive.path)
    nodes = [DirectoryNode(str(drive.path), 0, None)]
    if depth == 0:
        return nodes, [0], device

    frontier = [0]
    leaf_indexes: list[int] = []
    worker = threading.current_thread().name
    last_update = 0.0
    try:
        for level in range(depth):
            ensure_not_cancelled(cancel_event)
            next_frontier: list[int] = []
            for node_index in frontier:
                ensure_not_cancelled(cancel_event)
                node = nodes[node_index]
                now = time.monotonic()
                if now - last_update >= 0.2:
                    emit_progress(
                        progress_callback,
                        "worker",
                        worker=worker,
                        status="discovering",
                        path=node.path,
                        bytes=node.stats.bytes,
                        files=node.stats.files,
                        directories=len(nodes),
                    )
                    last_update = now
                node.stats.directories = 1
                try:
                    with os.scandir(node.path) as entries:
                        for entry_index, entry in enumerate(entries, 1):
                            if entry_index % 1024 == 0:
                                ensure_not_cancelled(cancel_event)
                                now = time.monotonic()
                                if now - last_update >= 0.2:
                                    emit_progress(
                                        progress_callback,
                                        "worker",
                                        worker=worker,
                                        status="discovering",
                                        path=node.path,
                                        bytes=node.stats.bytes,
                                        files=node.stats.files,
                                        directories=len(nodes),
                                    )
                                    last_update = now
                            try:
                                if entry.is_symlink():
                                    continue
                                if entry.is_dir(follow_symlinks=False):
                                    if not is_same_filesystem(entry, device):
                                        continue
                                    child_index = len(nodes)
                                    nodes.append(
                                        DirectoryNode(
                                            entry.path,
                                            level + 1,
                                            node_index,
                                        )
                                    )
                                    node.children.append(child_index)
                                    if level + 1 == depth:
                                        leaf_indexes.append(child_index)
                                    else:
                                        next_frontier.append(child_index)
                                else:
                                    node.stats.bytes += entry.stat(
                                        follow_symlinks=False
                                    ).st_size
                                    node.stats.files += 1
                            except OSError:
                                node.stats.skipped += 1
                except OSError:
                    node.stats.skipped += 1
            frontier = next_frontier
        return nodes, leaf_indexes, device
    finally:
        emit_progress(
            progress_callback,
            "worker",
            worker=worker,
            status="idle",
            path="",
            bytes=0,
            files=0,
            directories=len(nodes),
        )


def prepare_target(
    path: str,
    split_depth: int,
    device: int | None,
    progress_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> PreparedTarget:
    """Count a shallow prefix and turn deeper branches into parallel jobs."""
    base = ScanStats()
    frontier = [(path, 0)]
    jobs: list[str] = []
    worker = threading.current_thread().name
    last_update = 0.0
    try:
        while frontier:
            ensure_not_cancelled(cancel_event)
            directory, level = frontier.pop()
            now = time.monotonic()
            if now - last_update >= 0.2:
                emit_progress(
                    progress_callback,
                    "worker",
                    worker=worker,
                    status="preparing",
                    path=directory,
                    bytes=base.bytes,
                    files=base.files,
                    directories=base.directories,
                )
                last_update = now
            if level >= split_depth:
                jobs.append(directory)
                continue

            base.directories += 1
            try:
                with os.scandir(directory) as entries:
                    for entry_index, entry in enumerate(entries, 1):
                        if entry_index % 1024 == 0:
                            ensure_not_cancelled(cancel_event)
                            now = time.monotonic()
                            if now - last_update >= 0.2:
                                emit_progress(
                                    progress_callback,
                                    "worker",
                                    worker=worker,
                                    status="preparing",
                                    path=directory,
                                    bytes=base.bytes,
                                    files=base.files,
                                    directories=base.directories,
                                )
                                last_update = now
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                if is_same_filesystem(entry, device):
                                    frontier.append((entry.path, level + 1))
                            else:
                                base.bytes += entry.stat(
                                    follow_symlinks=False
                                ).st_size
                                base.files += 1
                        except OSError:
                            base.skipped += 1
            except OSError:
                base.skipped += 1
        return PreparedTarget(base, jobs)
    finally:
        emit_progress(
            progress_callback,
            "worker",
            worker=worker,
            status="idle",
            path="",
            bytes=base.bytes,
            files=base.files,
            directories=base.directories,
        )


def scan_subtree(
    path: str,
    device: int | None,
    progress_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> ScanStats:
    """Iteratively scan one independent directory subtree."""
    stats = ScanStats()
    stack = [path]
    worker = threading.current_thread().name
    last_update = 0.0
    try:
        while stack:
            ensure_not_cancelled(cancel_event)
            directory = stack.pop()
            stats.directories += 1
            now = time.monotonic()
            if now - last_update >= 0.2:
                emit_progress(
                    progress_callback,
                    "worker",
                    worker=worker,
                    status="scanning",
                    path=directory,
                    bytes=stats.bytes,
                    files=stats.files,
                    directories=stats.directories,
                )
                last_update = now
            try:
                with os.scandir(directory) as entries:
                    for entry_index, entry in enumerate(entries, 1):
                        if entry_index % 1024 == 0:
                            ensure_not_cancelled(cancel_event)
                            now = time.monotonic()
                            if now - last_update >= 0.2:
                                emit_progress(
                                    progress_callback,
                                    "worker",
                                    worker=worker,
                                    status="scanning",
                                    path=directory,
                                    bytes=stats.bytes,
                                    files=stats.files,
                                    directories=stats.directories,
                                )
                                last_update = now
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                if is_same_filesystem(entry, device):
                                    stack.append(entry.path)
                            else:
                                stats.bytes += entry.stat(
                                    follow_symlinks=False
                                ).st_size
                                stats.files += 1
                        except OSError:
                            stats.skipped += 1
            except OSError:
                stats.skipped += 1
        return stats
    finally:
        emit_progress(
            progress_callback,
            "worker",
            worker=worker,
            status="idle",
            path="",
            bytes=stats.bytes,
            files=stats.files,
            directories=stats.directories,
        )


def print_progress(
    completed: int, total: int, stats: ScanStats, started: float, final: bool = False
) -> None:
    elapsed = max(time.perf_counter() - started, 0.001)
    rate = human_size(int(stats.bytes / elapsed))
    suffix = "\n" if final else ""
    print(
        f"\r  chunks {completed:,}/{total:,} | "
        f"files {stats.files:,} | data {human_size(stats.bytes)} | {rate}/s",
        end=suffix,
        file=sys.stderr,
        flush=True,
    )


def scan_drives(
    drives: list[Drive],
    depth: int,
    workers: int,
    split_depth: int,
    show_progress: bool,
    progress_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    emit_progress(
        progress_callback,
        "stage",
        stage="chunking",
        message="Mapping folders for parallel chunks",
    )
    if show_progress:
        print("Discovering directory tree...", file=sys.stderr)

    discoveries: list[
        tuple[list[DirectoryNode], list[int], int | None] | None
    ] = [None] * len(drives)
    with ThreadPoolExecutor(
        max_workers=min(workers, len(drives)),
        thread_name_prefix="worker",
    ) as executor:
        futures = {
            executor.submit(
                discover_directory_tree,
                drive,
                depth,
                progress_callback,
                cancel_event,
            ): index
            for index, drive in enumerate(drives)
        }
        for future in as_completed(futures):
            ensure_not_cancelled(cancel_event)
            discoveries[futures[future]] = future.result()

    drive_nodes: list[list[DirectoryNode]] = [[] for _ in drives]
    leaf_references: list[tuple[int, int, int | None]] = []
    for drive_index, discovery in enumerate(discoveries):
        if discovery is None:
            continue
        nodes, leaf_indexes, device = discovery
        drive_nodes[drive_index] = nodes
        leaf_references.extend(
            (drive_index, node_index, device) for node_index in leaf_indexes
        )

    prepared: dict[int, PreparedTarget] = {}
    emit_progress(
        progress_callback,
        "stage",
        stage="chunking",
        message=f"Splitting {len(leaf_references):,} leaf directories into chunks",
    )
    if show_progress:
        print(
            f"Preparing parallel chunks from {len(leaf_references):,} leaf directories...",
            file=sys.stderr,
        )
    with ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="worker"
    ) as executor:
        futures = {
            executor.submit(
                prepare_target,
                drive_nodes[drive_index][node_index].path,
                split_depth,
                device,
                progress_callback,
                cancel_event,
            ): reference_index
            for reference_index, (drive_index, node_index, device) in enumerate(
                leaf_references
            )
        }
        for future in as_completed(futures):
            ensure_not_cancelled(cancel_event)
            prepared[futures[future]] = future.result()

    jobs: list[tuple[int, str, int | None]] = []
    for reference_index, (drive_index, node_index, device) in enumerate(
        leaf_references
    ):
        item = prepared[reference_index]
        drive_nodes[drive_index][node_index].stats = item.base
        jobs.extend((reference_index, path, device) for path in item.jobs)

    completed_stats = ScanStats()
    last_progress = 0.0
    last_event_progress = 0.0
    emit_progress(
        progress_callback,
        "stage",
        stage="scanning",
        message=f"Scanning {len(jobs):,} chunks with {workers} workers",
    )
    emit_progress(
        progress_callback,
        "progress",
        completed=0,
        total=len(jobs),
        bytes=0,
        files=0,
        directories=0,
    )
    if show_progress:
        print(
            f"Scanning with {workers} workers across {len(jobs):,} chunks...",
            file=sys.stderr,
        )
    with ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="worker"
    ) as executor:
        futures = {
            executor.submit(
                scan_subtree,
                path,
                device,
                progress_callback,
                cancel_event,
            ): reference_index
            for reference_index, path, device in jobs
        }
        for completed, future in enumerate(as_completed(futures), 1):
            ensure_not_cancelled(cancel_event)
            stats = future.result()
            drive_index, node_index, _ = leaf_references[futures[future]]
            drive_nodes[drive_index][node_index].stats.add(stats)
            completed_stats.add(stats)
            now = time.perf_counter()
            if now - last_event_progress >= 0.1 or completed == len(jobs):
                emit_progress(
                    progress_callback,
                    "progress",
                    completed=completed,
                    total=len(jobs),
                    bytes=completed_stats.bytes,
                    files=completed_stats.files,
                    directories=completed_stats.directories,
                )
                last_event_progress = now
            if show_progress and (now - last_progress >= 0.5 or completed == len(jobs)):
                print_progress(
                    completed,
                    len(jobs),
                    completed_stats,
                    started,
                    final=completed == len(jobs),
                )
                last_progress = now

    emit_progress(
        progress_callback,
        "stage",
        stage="generating",
        message="Generating the interactive report",
    )
    for nodes in drive_nodes:
        for node_index in range(len(nodes) - 1, 0, -1):
            node = nodes[node_index]
            if node.parent is not None:
                nodes[node.parent].stats.add(node.stats)

    duration = time.perf_counter() - started
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "settings": {
            "depth": depth,
            "workers": workers,
            "split_depth": split_depth,
        },
        "duration_seconds": round(duration, 3),
        "drives": [
            {
                "path": str(drive.path),
                "total": drive.total,
                "used": drive.used,
                "free": drive.free,
                "scan": asdict(drive_nodes[index][0].stats),
                "tree": serialize_directory_tree(drive_nodes[index]),
            }
            for index, drive in enumerate(drives)
        ],
    }


def serialize_directory_tree(nodes: list[DirectoryNode]) -> dict[str, Any]:
    """Convert indexed nodes into a nested, snapshot-friendly dictionary."""
    serialized: list[dict[str, Any] | None] = [None] * len(nodes)
    for node_index in range(len(nodes) - 1, -1, -1):
        node = nodes[node_index]
        name = (
            node.path
            if node.parent is None
            else os.path.basename(os.path.normpath(node.path)) or node.path
        )
        serialized[node_index] = {
            "name": name,
            "path": node.path,
            **asdict(node.stats),
            "children": [serialized[index] for index in node.children],
        }
    root = serialized[0]
    if root is None:
        raise ValueError("Cannot serialize an empty directory tree.")
    return root


def save_snapshot(report: dict[str, Any], path: Path) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(report, file, indent=2)
            file.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def save_html_report(report: dict[str, Any], path: Path) -> None:
    """Write a self-contained interactive HTML report with embedded scan data."""
    template_path = Path(__file__).with_name("report_template.html")
    template = template_path.read_text(encoding="utf-8")
    placeholder = "__DISK_REPORT_DATA__"
    if placeholder not in template:
        raise ValueError(f"HTML template is missing {placeholder!r}.")

    # Keep folder names from being interpreted as markup or closing the script tag.
    embedded_data = (
        json.dumps(report, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    output = template.replace(placeholder, embedded_data)
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(output, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_snapshot(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as file:
            report = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not load snapshot {path}: {error}") from error
    if report.get("schema_version") not in (1, SCHEMA_VERSION):
        raise ValueError(
            f"Unsupported snapshot schema {report.get('schema_version')!r}; "
            f"expected 1 or {SCHEMA_VERSION}."
        )
    return report


def print_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    widths = [len(header) for header in headers]
    for row in rows:
        widths = [max(width, len(value)) for width, value in zip(widths, row)]
    print("  ".join(header.ljust(width) for header, width in zip(headers, widths)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(width) for value, width in zip(row, widths)))


def print_storage_tree(root: dict[str, Any], top: int) -> None:
    """Render a size-sorted ASCII tree, limiting children at each directory."""
    print(f"{root['name']}  [{human_size(root['bytes'])}]")

    def render(node: dict[str, Any], prefix: str) -> None:
        children = sorted(
            node.get("children", []),
            key=lambda child: child["bytes"],
            reverse=True,
        )
        visible = children[:top]
        omitted = len(children) - len(visible)
        for index, child in enumerate(visible):
            is_last = index == len(visible) - 1 and omitted == 0
            connector = "`-- " if is_last else "|-- "
            print(
                f"{prefix}{connector}{child['name']}  "
                f"[{human_size(child['bytes'])}]"
            )
            child_prefix = prefix + ("    " if is_last else "|   ")
            render(child, child_prefix)
        if omitted:
            print(f"{prefix}`-- ... {omitted:,} more directories")

    render(root, "")


def print_report(
    report: dict[str, Any], top: int, show_capacity: bool = True
) -> None:
    print(f"Snapshot: {report['created_at']} on {report['host']}")
    print(f"Scan duration: {report['duration_seconds']:.1f} seconds")
    if show_capacity:
        print("\nDrive capacity (least free space first)")
        print_table(
            ("Drive", "Total", "Used", "Free", "Free %"),
            [
                (
                    drive["path"],
                    human_size(drive["total"]),
                    human_size(drive["used"]),
                    human_size(drive["free"]),
                    f"{drive['free'] / drive['total']:.1%}",
                )
                for drive in report["drives"]
            ],
        )

    depth = report["settings"]["depth"]
    for drive in report["drives"]:
        if "tree" in drive:
            print(f"\nStorage tree for {drive['path']} (depth {depth})")
            print_storage_tree(drive["tree"], top)
        else:
            # Schema 1 snapshots used a flat list at exactly one depth.
            largest = sorted(
                drive["paths"], key=lambda item: item["bytes"], reverse=True
            )[:top]
            print(f"\nLargest paths on {drive['path']} (depth {depth})")
            print_table(
                ("Size", "Files", "Folders", "Path"),
                [
                    (
                        human_size(item["bytes"]),
                        human_count(item["files"]),
                        human_count(item["directories"]),
                        item["path"],
                    )
                    for item in largest
                ],
            )
        scan = drive["scan"]
        print(
            f"  Indexed {human_size(scan['bytes'])} in "
            f"{human_count(scan['files'])} files and "
            f"{human_count(scan['directories'])} folders; "
            f"skipped {human_count(scan['skipped'])} inaccessible entries."
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find large paths using a parallel scan or view a saved snapshot."
    )
    parser.add_argument(
        "--drive",
        action="append",
        metavar="PATH",
        help="Scan only this drive or directory (repeatable).",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=1,
        help="Path depth to report: 1 means direct children (default: 1).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=15,
        help="Children shown per directory in the tree (default: 15).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Concurrent I/O workers (default: {DEFAULT_WORKERS}).",
    )
    parser.add_argument(
        "--split-depth",
        type=int,
        default=2,
        help="Levels used to create parallel chunks (default: 2).",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        metavar="FILE",
        help="Save the completed scan as a reusable JSON snapshot.",
    )
    parser.add_argument(
        "--load-snapshot",
        type=Path,
        metavar="FILE",
        help="Display an existing snapshot immediately without scanning.",
    )
    parser.add_argument(
        "--html",
        type=Path,
        metavar="FILE",
        help=(
            "Write a self-contained interactive HTML report instead of "
            "terminal results."
        ),
    )
    parser.add_argument(
        "--no-progress", action="store_true", help="Disable live scan progress."
    )
    args = parser.parse_args()
    if args.depth < 0 or args.split_depth < 0:
        parser.error("--depth and --split-depth must be >= 0")
    if args.top < 1 or args.workers < 1:
        parser.error("--top and --workers must be >= 1")
    if args.load_snapshot and (args.drive or args.snapshot):
        parser.error("--load-snapshot cannot be combined with --drive or --snapshot")
    return args


def main() -> int:
    args = parse_args()
    if args.load_snapshot:
        try:
            report = load_snapshot(args.load_snapshot)
        except ValueError as error:
            print(error, file=sys.stderr)
            return 1
        if args.html:
            try:
                save_html_report(report, args.html)
            except (OSError, ValueError) as error:
                print(
                    f"Could not save HTML report {args.html}: {error}",
                    file=sys.stderr,
                )
                return 1
            print(f"HTML report saved to {args.html.resolve()}")
        else:
            print_report(report, args.top)
        return 0

    drives = discover_drives(args.drive)
    if not drives:
        print("No accessible drives or scan roots found.", file=sys.stderr)
        return 1

    if not args.html:
        print("Drive capacity (least free space first)")
        print_table(
            ("Drive", "Total", "Used", "Free", "Free %"),
            [
                (
                    str(drive.path),
                    human_size(drive.total),
                    human_size(drive.used),
                    human_size(drive.free),
                    f"{drive.free / drive.total:.1%}",
                )
                for drive in drives
            ],
        )
        print(file=sys.stderr)
    elif not args.no_progress:
        print(
            f"Scanning {len(drives)} drive(s) for interactive HTML output...",
            file=sys.stderr,
        )

    try:
        report = scan_drives(
            drives,
            args.depth,
            args.workers,
            args.split_depth,
            not args.no_progress,
        )
    except KeyboardInterrupt:
        print("\nScan cancelled; no report was written.", file=sys.stderr)
        return 130

    if not args.html:
        print()
        print_report(report, args.top, show_capacity=False)
    if args.snapshot:
        try:
            save_snapshot(report, args.snapshot)
        except OSError as error:
            print(f"Could not save snapshot {args.snapshot}: {error}", file=sys.stderr)
            return 1
        print(f"\nSnapshot saved to {args.snapshot.resolve()}")
    if args.html:
        try:
            save_html_report(report, args.html)
        except (OSError, ValueError) as error:
            print(f"Could not save HTML report {args.html}: {error}", file=sys.stderr)
            return 1
        print(f"\nHTML report saved to {args.html.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
