# Disk Usage Report

A standalone Windows application for parallel storage analysis. It discovers
mounted drives, scans selected drives or folders with a bounded worker pool,
shows live worker activity, and renders a searchable size tree when the scan
completes.

## Build the standalone app

Double-click **`Build Windows App.cmd`** in File Explorer. The build uses an
isolated `.venv`, installs its requirements, and creates one executable:

```text
dist\DiskUsageReport.exe
```

Double-click that executable to run the app. It opens as one native desktop
window with no browser or background terminal. Its private local service uses
`127.0.0.1:5336` only while the app is open.

The generated storage-tree mark is embedded in the executable and reused for
the browser favicon, touch icon, and in-app header branding. Versioned asset
URLs prevent stale embedded-browser caches, and the header includes a compact
branded fallback if an image request ever fails.

Python 3 is required to build from source, but the resulting executable is
standalone and can be run without installing Python or project dependencies.

For development, install the dependencies and run the desktop entry point:

```powershell
python -m pip install -r requirements.txt
python desktop_app.py
```

The browser-based development launcher remains available with
`python disk_usage_report.py`.

## Configure a scan

The start page contains only the scan controls:

- **Workers** selects 1–64 parallel I/O workers. Start around 16 for SSDs or
  4–8 for one mechanical disk.
- **Tree depth** selects how many directory layers the final report retains.
- **Drives** lets you include or exclude each mounted drive.
- **Additional paths** accepts a list of specific folders or network paths.

For the earlier 32-worker, three-level, all-drive scan: leave every drive
selected, set **Workers** to `32`, set **Tree depth** to `3`, and select
**Start scan**.

## Live scan view

The app presents three distinct phases: **Chunking** maps the selected roots and
divides them into parallel work, **Scanning** measures those chunks, and
**Generating** builds the tree and saves its history snapshot. The current phase
is highlighted as the scan advances.

The same view shows chunk progress, elapsed time, indexed files and bytes, and
one animated card per worker. Each active worker reports the directory it is
currently reading and its current chunk totals. The scan can be cancelled.

## Interactive report

The completed report stays in the app and includes every directory through
the selected depth—there is no terminal `--top` truncation. It provides:

- folder/path search and a minimum-size filter;
- expand-all, collapse-all, and per-folder disclosure controls;
- a **Scan deeper** action on every folder, with focused worker and depth
  controls for a new scan rooted at that path;
- responsive bars showing each child as a percentage of its parent; and
- a calculated **Loose files in this folder** row equal to the parent total
  minus the total of its child folders.

For example, a 1 TB parent with three 200 GB child folders shows the remaining
400 GB as loose files. At the final selected depth, the remainder is labeled as
files and deeper folders because those deeper directories were not expanded.

Large reports are transferred with fast compression and render folders lazily.
Only roots and opened branches create interface elements, while search and size
filters are debounced to keep typing and navigation responsive.

## Optional batch reports

The underlying scanner remains available for automation and standalone JSON or
HTML exports:

```powershell
python scanner.py --workers 32 --depth 3 --html all-drives-report.html
python scanner.py --workers 32 --depth 3 --snapshot all-drives-report.json
python scanner.py --load-snapshot all-drives-report.json --html converted-report.html
```

Omit `--drive` to scan every discovered drive, or repeat it to scan selected
roots:

```powershell
python scanner.py --drive C:\ --drive D:\ --workers 16 --depth 2
```

JSON snapshots from the earlier flat-report format remain readable.

## Recent scan history

Every completed scan is saved locally and appears under **Recent
scans** on the start page. Each entry shows when the scan ran, how long it took,
the roots and settings used, and the indexed size. Select **Open report** to
view the complete interactive report without scanning again.

The desktop app retains its newest 10 compressed reports under
`%LOCALAPPDATA%\DiskUsageReport\history`. Older reports are removed
automatically. Cancelled or failed scans are not added to history. Source-mode
browser runs continue to use the project-local `.history/` directory.
