#!/usr/bin/env python3
"""Report capacity and largest folders for every mounted drive.

Examples:
    python disk_usage_report.py
    python disk_usage_report.py --depth 2 --top 25

For best cross-platform mount discovery, optionally install psutil:
    python -m pip install psutil
"""

from __future__ import annotations

import argparse
import ctypes
import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Drive:
    path: Path
    total: int
    used: int
    free: int


def human_size(value: int) -> str:
    """Format bytes in readable binary units."""
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if value < 1024 or unit == "PiB":
            return f"{value} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def discover_drives() -> list[Drive]:
    """Find ready mounted drives and their free/used capacity."""
    try:
        import psutil  # type: ignore[import-not-found]

        mount_points = [Path(partition.mountpoint) for partition in psutil.disk_partitions(all=False)]
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
        try:
            key = os.path.normcase(os.path.abspath(mount_point))
            if key in seen:
                continue
            capacity = shutil.disk_usage(mount_point)
        except OSError:
            continue  # Empty removable disks or inaccessible shares.
        seen.add(key)
        drives.append(Drive(mount_point, capacity.total, capacity.used, capacity.free))
    return sorted(drives, key=lambda drive: drive.free)


def largest_folders(drive: Drive, depth: int) -> tuple[list[tuple[Path, int]], int]:
    """Calculate inclusive folder sizes without following symlinks or sub-mounts."""
    folders: list[tuple[Path, int]] = []
    skipped = 0
    try:
        root_device = os.stat(drive.path, follow_symlinks=False).st_dev
    except OSError:
        root_device = None

    def walk(folder: Path, level: int) -> int:
        nonlocal skipped
        total = 0
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            # Windows reports a zero st_dev for DirEntry objects on
                            # some filesystems, so device-boundary detection is only
                            # reliable on POSIX.
                            if (
                                os.name != "nt"
                                and root_device is not None
                                and entry.stat(follow_symlinks=False).st_dev != root_device
                            ):
                                continue
                            child_size = walk(Path(entry.path), level + 1)
                            total += child_size
                            if level + 1 == depth:
                                folders.append((Path(entry.path), child_size))
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        skipped += 1
        except OSError:
            skipped += 1
        return total

    total = walk(drive.path, 0)
    if depth == 0:
        folders.append((drive.path, total))
    return folders, skipped


def print_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    widths = [len(header) for header in headers]
    for row in rows:
        widths = [max(width, len(value)) for width, value in zip(widths, row)]
    print("  ".join(header.ljust(width) for header, width in zip(headers, widths)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(width) for value, width in zip(row, widths)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Find the largest folders on mounted drives.")
    parser.add_argument("--depth", type=int, default=1, help="Folder depth: 1 means direct children (default: 1).")
    parser.add_argument("--top", type=int, default=15, help="Folders shown per drive (default: 15).")
    parser.add_argument("--workers", type=int, default=4, help="Drives scanned concurrently (default: 4).")
    args = parser.parse_args()
    if args.depth < 0 or args.top < 1 or args.workers < 1:
        parser.error("--depth must be >= 0; --top and --workers must be >= 1")

    drives = discover_drives()
    if not drives:
        print("No accessible drives found.")
        return 1

    print("Drive capacity (least free space first)")
    print_table(("Drive", "Total", "Used", "Free", "Free %"), [
        (str(drive.path), human_size(drive.total), human_size(drive.used), human_size(drive.free), f"{drive.free / drive.total:.1%}")
        for drive in drives
    ])

    print("\nScanning folders; this may take a while on large drives...")
    with ThreadPoolExecutor(max_workers=min(args.workers, len(drives))) as executor:
        futures = {executor.submit(largest_folders, drive, args.depth): drive for drive in drives}
        for future in as_completed(futures):
            drive = futures[future]
            folders, skipped = future.result()
            largest = sorted(folders, key=lambda item: item[1], reverse=True)[:args.top]
            print(f"\nLargest folders on {drive.path} (depth {args.depth})")
            print_table(("Size", "Path"), [(human_size(folder_size), str(path)) for path, folder_size in largest])
            if skipped:
                print(f"Skipped {skipped} inaccessible or disappearing entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
