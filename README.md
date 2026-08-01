# Utils
Utility scripts.

## Disk usage report

`disk_usage_report.py` discovers mounted drives, prints a capacity table ordered
by free space, then renders a size-sorted directory tree for each drive. Large
directory trees are split into chunks and scanned concurrently with a bounded
worker pool.

```powershell
# Scan every drive with live progress and save the result.
python disk_usage_report.py --workers 16 --snapshot disk-report.json

# Reopen the report instantly later; this performs no disk scan.
python disk_usage_report.py --load-snapshot disk-report.json

# Scan one drive and nest the report two directory levels deep.
python disk_usage_report.py --drive D:\ --depth 2 --top 25 --snapshot d-drive.json
```

The default report depth is `1`, which shows the root and its immediate
directories. Use `--depth 2` or higher to nest additional levels. Children are
ordered largest-first, and `--top` limits the number shown under each directory:

```text
D:\  [3.5 TiB]
|-- Media  [2.4 TiB]
|   |-- Movies  [1.6 TiB]
|   `-- Music  [800.0 GiB]
`-- Backups  [1.1 TiB]
    `-- Workstation  [1.0 TiB]
```

File sizes are included in their containing directory totals. `--split-depth`
controls how aggressively leaf directories are divided into parallel scan
chunks; the default of `2` is a practical balance for multi-terabyte volumes.
SSDs generally benefit from `--workers 16`, while `--workers 4` to `8` may be
faster on a single mechanical disk.

Snapshots are JSON files containing drive capacity, the complete directory
tree, file/folder counts, scan settings, duration, and capture time. They can be
archived, reopened with different `--top` limits, or compared by other tools
without rescanning the drives. Snapshots from the original flat-report format
remain readable.

For the most reliable cross-platform mount discovery, install `psutil`:

```powershell
python -m pip install psutil
```
