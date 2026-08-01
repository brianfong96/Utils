# Utils
Utility scripts.

## Disk usage report

`disk_usage_report.py` discovers mounted drives, prints a capacity table ordered
by free space, then reports the largest paths on each drive. Large directory
trees are split into chunks and scanned concurrently with a bounded worker pool.

```powershell
# Scan every drive with live progress and save the result.
python disk_usage_report.py --workers 16 --snapshot disk-report.json

# Reopen the report instantly later; this performs no disk scan.
python disk_usage_report.py --load-snapshot disk-report.json

# Scan one drive and show more top-level paths.
python disk_usage_report.py --drive D:\ --top 25 --snapshot d-drive.json
```

The default report depth is `1`, which compares each drive's immediate files and
folders. Use `--depth 2` for a more detailed breakdown. `--split-depth` controls
how aggressively large folders are divided into parallel scan chunks; the
default of `2` is a practical balance for multi-terabyte volumes. SSDs generally
benefit from `--workers 16`, while `--workers 4` to `8` may be faster on a single
mechanical disk.

Snapshots are JSON files containing drive capacity, path sizes, file/folder
counts, scan settings, duration, and capture time. They can be archived or
compared by other tools without rescanning the drives.

For the most reliable cross-platform mount discovery, install `psutil`:

```powershell
python -m pip install psutil
```
