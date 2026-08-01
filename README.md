# Utils
Utility scripts.

## Disk usage report

`disk_usage_report.py` discovers mounted drives, prints a capacity table ordered
by free space, then reports the largest folders on each drive.

```powershell
python disk_usage_report.py
python disk_usage_report.py --depth 2 --top 25
```

For the most reliable cross-platform mount discovery, install `psutil`:

```powershell
python -m pip install psutil
```
