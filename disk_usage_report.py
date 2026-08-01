#!/usr/bin/env python3
"""Fast, parallel disk usage reports with reusable JSON snapshots.

Examples:
    python disk_usage_report.py --snapshot disk-report.json
    python disk_usage_report.py --load-snapshot disk-report.json
    python disk_usage_report.py --drive C:\\ --workers 16 --top 25

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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
DEFAULT_WORKERS = min(16, max(4, (os.cpu_count() or 4) * 2))


@dataclass(frozen=True)
class Drive:
    path: Path
    total: int
    used: int
    free: int


@dataclass(frozen=True)
class ReportTarget:
    path: str
    is_directory: bool
    size: int = 0


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


def find_report_targets(
    drive: Drive, depth: int
) -> tuple[list[ReportTarget], int, int | None]:
    """Find files and directories exactly ``depth`` levels below a scan root."""
    device = root_device(drive.path)
    if depth == 0:
        return [ReportTarget(str(drive.path), True)], 0, device

    frontier = [str(drive.path)]
    skipped = 0
    for current_depth in range(depth):
        at_report_depth = current_depth + 1 == depth
        next_frontier: list[str] = []
        targets: list[ReportTarget] = []
        for directory in frontier:
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                if not is_same_filesystem(entry, device):
                                    continue
                                if at_report_depth:
                                    targets.append(ReportTarget(entry.path, True))
                                else:
                                    next_frontier.append(entry.path)
                            elif at_report_depth:
                                targets.append(
                                    ReportTarget(
                                        entry.path,
                                        False,
                                        entry.stat(follow_symlinks=False).st_size,
                                    )
                                )
                        except OSError:
                            skipped += 1
            except OSError:
                skipped += 1
        if at_report_depth:
            return targets, skipped, device
        frontier = next_frontier
    return [], skipped, device


def prepare_target(path: str, split_depth: int, device: int | None) -> PreparedTarget:
    """Count a shallow prefix and turn deeper branches into parallel jobs."""
    base = ScanStats()
    frontier = [(path, 0)]
    jobs: list[str] = []
    while frontier:
        directory, level = frontier.pop()
        if level >= split_depth:
            jobs.append(directory)
            continue

        base.directories += 1
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if is_same_filesystem(entry, device):
                                frontier.append((entry.path, level + 1))
                        else:
                            base.bytes += entry.stat(follow_symlinks=False).st_size
                            base.files += 1
                    except OSError:
                        base.skipped += 1
        except OSError:
            base.skipped += 1
    return PreparedTarget(base, jobs)


def scan_subtree(path: str, device: int | None) -> ScanStats:
    """Iteratively scan one independent directory subtree."""
    stats = ScanStats()
    stack = [path]
    while stack:
        directory = stack.pop()
        stats.directories += 1
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if is_same_filesystem(entry, device):
                                stack.append(entry.path)
                        else:
                            stats.bytes += entry.stat(follow_symlinks=False).st_size
                            stats.files += 1
                    except OSError:
                        stats.skipped += 1
        except OSError:
            stats.skipped += 1
    return stats


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
) -> dict[str, Any]:
    started = time.perf_counter()
    if show_progress:
        print("Discovering report paths...", file=sys.stderr)

    discoveries: list[tuple[list[ReportTarget], int, int | None] | None] = [
        None
    ] * len(drives)
    with ThreadPoolExecutor(max_workers=min(workers, len(drives))) as executor:
        futures = {
            executor.submit(find_report_targets, drive, depth): index
            for index, drive in enumerate(drives)
        }
        for future in as_completed(futures):
            discoveries[futures[future]] = future.result()

    all_targets: list[tuple[int, ReportTarget, int | None]] = []
    discovery_skips = [0] * len(drives)
    for drive_index, discovery in enumerate(discoveries):
        if discovery is None:
            continue
        targets, skipped, device = discovery
        discovery_skips[drive_index] = skipped
        all_targets.extend((drive_index, target, device) for target in targets)

    directory_indexes = [
        index for index, (_, target, _) in enumerate(all_targets) if target.is_directory
    ]
    prepared: dict[int, PreparedTarget] = {}
    if show_progress:
        print(
            f"Preparing parallel chunks from {len(directory_indexes):,} directories...",
            file=sys.stderr,
        )
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                prepare_target,
                all_targets[index][1].path,
                split_depth,
                all_targets[index][2],
            ): index
            for index in directory_indexes
        }
        for future in as_completed(futures):
            prepared[futures[future]] = future.result()

    target_stats: list[ScanStats] = []
    jobs: list[tuple[int, str, int | None]] = []
    for index, (_, target, device) in enumerate(all_targets):
        if target.is_directory:
            item = prepared[index]
            target_stats.append(item.base)
            jobs.extend((index, path, device) for path in item.jobs)
        else:
            target_stats.append(ScanStats(bytes=target.size, files=1))

    completed_stats = ScanStats()
    last_progress = 0.0
    if show_progress:
        print(
            f"Scanning with {workers} workers across {len(jobs):,} chunks...",
            file=sys.stderr,
        )
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(scan_subtree, path, device): target_index
            for target_index, path, device in jobs
        }
        for completed, future in enumerate(as_completed(futures), 1):
            stats = future.result()
            target_stats[futures[future]].add(stats)
            completed_stats.add(stats)
            now = time.perf_counter()
            if show_progress and (now - last_progress >= 0.5 or completed == len(jobs)):
                print_progress(
                    completed,
                    len(jobs),
                    completed_stats,
                    started,
                    final=completed == len(jobs),
                )
                last_progress = now

    drive_paths: list[list[dict[str, Any]]] = [[] for _ in drives]
    drive_stats = [ScanStats(skipped=count) for count in discovery_skips]
    for (drive_index, target, _), stats in zip(all_targets, target_stats):
        drive_stats[drive_index].add(stats)
        drive_paths[drive_index].append(
            {
                "path": target.path,
                "bytes": stats.bytes,
                "files": stats.files,
                "directories": stats.directories,
            }
        )

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
                "scan": asdict(drive_stats[index]),
                "paths": drive_paths[index],
            }
            for index, drive in enumerate(drives)
        ],
    }


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


def load_snapshot(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as file:
            report = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not load snapshot {path}: {error}") from error
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported snapshot schema {report.get('schema_version')!r}; "
            f"expected {SCHEMA_VERSION}."
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
        "--top", type=int, default=15, help="Paths shown per drive (default: 15)."
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
        print_report(report, args.top)
        return 0

    drives = discover_drives(args.drive)
    if not drives:
        print("No accessible drives or scan roots found.", file=sys.stderr)
        return 1

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

    try:
        report = scan_drives(
            drives,
            args.depth,
            args.workers,
            args.split_depth,
            not args.no_progress,
        )
    except KeyboardInterrupt:
        print("\nScan cancelled; no snapshot was written.", file=sys.stderr)
        return 130

    print()
    print_report(report, args.top, show_capacity=False)
    if args.snapshot:
        try:
            save_snapshot(report, args.snapshot)
        except OSError as error:
            print(f"Could not save snapshot {args.snapshot}: {error}", file=sys.stderr)
            return 1
        print(f"\nSnapshot saved to {args.snapshot.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
