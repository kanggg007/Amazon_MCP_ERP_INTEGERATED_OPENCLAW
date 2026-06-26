"""
Railway Scheduler — runs cron jobs 24/7 on Railway
Imports and runs the same scripts as macOS crontab.
"""
import asyncio
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent.parent

SCHEDULE = [
    # (hour, minute, day_of_week, script, description)
    (8, 0, None, "engines/ads_health_scanner.py", "Ads Health Scan"),
    (8, 0, None, "engines/inbox_monitor.py", "Inbox Monitor"),
    (8, 0, 1, "engines/discrepancy_scanner.py", "Discrepancy Mon"),
    (8, 0, 5, "engines/discrepancy_scanner.py", "Discrepancy Fri"),
    (14, 0, None, "engines/ads_ingestion.py", "Ads Ingestion"),
    (17, 0, None, "daily_revenue_report.py", "Revenue Report"),
]

async def run_script(script, name):
    path = BASE / script
    if not path.exists():
        print(f"[{name}] Script not found: {path}")
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            print(f"[{name}] OK ({datetime.now():%H:%M})")
        else:
            print(f"[{name}] FAIL: {stderr.decode()[:200]}")
    except Exception as e:
        print(f"[{name}] ERROR: {e}")

async def scheduler_loop():
    while True:
        now = datetime.now()
        for hour, minute, dow, script, name in SCHEDULE:
            if now.hour == hour and now.minute == minute:
                if dow is None or now.isoweekday() == dow:
                    await run_script(script, name)
        await asyncio.sleep(60)  # Check every minute

if __name__ == "__main__":
    print(f"Railway Scheduler started ({datetime.now():%H:%M})")
    print(f"Scheduled {len(SCHEDULE)} jobs")
    asyncio.run(scheduler_loop())
