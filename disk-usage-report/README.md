# Disk Usage Report

Fast, parallel disk usage analysis with reusable snapshots.

`disk_usage_report.py` discovers mounted drives, prints a capacity table ordered
by free space, then renders a size-sorted directory tree for each drive. Large
directory trees are split into chunks and scanned concurrently with a bounded
worker pool.

```powershell
# Generate a self-contained interactive HTML report (no JSON required).
python disk_usage_report.py --workers 16 --depth 2 --html disk-report.html

# Scan every drive with live progress and save the result.
python disk_usage_report.py --workers 16 --snapshot disk-report.json

# Reopen the report instantly later; this performs no disk scan.
python disk_usage_report.py --load-snapshot disk-report.json

# Scan one drive and nest the report two directory levels deep.
python disk_usage_report.py --drive D:\ --depth 2 --top 25 --snapshot d-drive.json
```

### Interactive HTML reports

Use `--html` when you want a standalone browser report instead of the full
terminal tree or a JSON snapshot:

```powershell
python disk_usage_report.py --workers 32 --depth 3 --html all-drives-report.html
```

The resulting HTML file embeds the complete scan and has no external assets or
JSON sidecar. Open it directly in any modern browser. It provides:

- every directory through the requested depth, without applying the terminal
  `--top` truncation;
- folder/path search and a minimum-size filter;
- expand-all, collapse-all, and per-folder disclosure controls;
- responsive bars showing every child as a percentage of its parent; and
- a calculated **Loose files in this folder** row equal to the parent total
  minus its displayed child folders.

For example, if a parent contains 1 TB and three child directories consume
200 GB each, its remaining 400 GB is shown as loose files in that parent. At
the final requested depth, the remainder is labeled as files and deeper folders
because those deeper directories were intentionally not expanded.

You can also turn an existing JSON snapshot into HTML without rescanning:

```powershell
python disk_usage_report.py --load-snapshot disk-report.json --html disk-report.html
```

### Scan all drives with 32 workers and three tree levels

From the `disk-usage-report` directory, run:

```powershell
python disk_usage_report.py --workers 32 --depth 3 --html all-drives-report.html
```

This command:

- omits `--drive`, so every discovered drive is scanned;
- uses 32 concurrent I/O workers across the discovered drives;
- renders the root plus three nested directory levels; and
- writes a complete, searchable report to `all-drives-report.html`.

Open `all-drives-report.html` directly in a browser. If you also want the raw
JSON snapshot for later conversion or programmatic analysis, supply both output
options in the same scan:

```powershell
python disk_usage_report.py --workers 32 --depth 3 --html all-drives-report.html --snapshot all-drives-report.json
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

Install the utility's dependencies from this directory:

```powershell
python -m pip install -r requirements.txt
```
