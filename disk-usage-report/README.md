# Disk Usage Report

A local web application for parallel storage analysis. It discovers mounted
drives, scans selected drives or folders with a bounded worker pool, shows live
worker activity, and renders a searchable size tree when the scan completes.

## Run the application

On Windows, double-click **`Start Disk Usage Report.cmd`** in File Explorer.
It starts the local server and opens the application in your default browser.
Keep the command window open while using the application; close it or press
`Ctrl+C` to stop the server.

The launcher uses a local `.venv` when one exists, otherwise it uses the
installed Python 3 launcher or `python` command. Install the optional drive
discovery dependency with `python -m pip install -r requirements.txt`.

To start it from a terminal instead, run these commands from this directory:

```powershell
python -m pip install -r requirements.txt
python disk_usage_report.py
```

The application opens at [http://127.0.0.1:8765](http://127.0.0.1:8765). It is
served only on the local computer by default. Press `Ctrl+C` in the terminal to
stop it.

If you do not want the default browser to open automatically:

```powershell
python disk_usage_report.py --no-browser
```

Use another local port when needed:

```powershell
python disk_usage_report.py --port 9000
```

## Configure a scan in the browser

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

While scanning, the page shows the current stage, completed chunk progress,
elapsed time, indexed files and bytes, and one animated card per worker. Each
active worker reports the directory it is currently reading and its current
chunk totals. The scan can be cancelled from the page.

## Interactive report

The completed report stays in the browser and includes every directory through
the selected depth—there is no terminal `--top` truncation. It provides:

- folder/path search and a minimum-size filter;
- expand-all, collapse-all, and per-folder disclosure controls;
- responsive bars showing each child as a percentage of its parent; and
- a calculated **Loose files in this folder** row equal to the parent total
  minus the total of its child folders.

For example, a 1 TB parent with three 200 GB child folders shows the remaining
400 GB as loose files. At the final selected depth, the remainder is labeled as
files and deeper folders because those deeper directories were not expanded.

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

Every completed browser scan is saved locally and appears under **Recent
scans** on the start page. Each entry shows when the scan ran, how long it took,
the roots and settings used, and the indexed size. Select **Open report** to
view the complete interactive report without scanning again.

The newest 10 completed reports are retained as compressed JSON in `.history/`.
Older reports are removed automatically, and the directory is excluded from
Git. Cancelled or failed scans are not added to history.
