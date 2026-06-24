#!/usr/bin/env python3
"""
Ads Report Poller — checks pending reports and downloads completed ones.
Run this every 5 minutes to collect ads data.

Usage: python3 engines/ads_poller.py
"""
import httpx, os, json, gzip, time
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).parent.parent
QUEUE_FILE = Path("/tmp/ads_report_queue.json")
DATA_DIR = BASE / "data_lake" / "ads"
DATA_DIR.mkdir(parents=True, exist_ok=True)

def load_env():
    for line in (BASE / ".env").read_text().splitlines():
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

load_env()

if not QUEUE_FILE.exists():
    print("No pending reports. Run ads_sync_engine.py first to create reports.")
    exit(0)

with open(QUEUE_FILE) as f:
    queue = json.load(f)

completed = []
for item in queue:
    rid = item["report_id"]
    name = item["name"]
    store = item["store"]
    market = item["market"]
    month = item["month"]
    
    cid = os.environ.get(f"STORE_{store}_ADS_CLIENT_ID", "")
    csec = os.environ.get(f"STORE_{store}_ADS_CLIENT_SECRET", "")
    ref = os.environ.get(f"STORE_{store}_ADS_REFRESH_TOKEN", "")
    pid = item["profile_id"]
    
    r = httpx.post("https://api.amazon.com/auth/o2/token", json={
        "grant_type": "refresh_token", "client_id": cid,
        "client_secret": csec, "refresh_token": ref
    }, timeout=10)
    tk = r.json()["access_token"]
    h = {"Authorization": f"Bearer {tk}", "Amazon-Advertising-API-ClientId": cid,
         "Amazon-Advertising-API-Scope": pid}
    
    rp = httpx.get(f"https://advertising-api.amazon.com/reporting/reports/{rid}", headers=h, timeout=10)
    d = rp.json()
    status = d.get("status")
    
    if status == "COMPLETED":
        raw = gzip.decompress(httpx.get(d["url"], timeout=30).content).decode()
        rows = [json.loads(l) for l in raw.strip().split("\n") if l]
        cost = sum(float(r.get("cost", 0)) for r in rows)
        sales = sum(float(r.get("sales30d", 0)) for r in rows)
        
        fn = DATA_DIR / f"{name}_{month}.json"
        with open(fn, "w") as f:
            json.dump({"meta": {"store": name, "market": market, "month": month,
                                "total_cost": cost, "total_sales": sales}, "rows": rows}, f)
        print(f"✅ {name}/{market}/{month}: ${cost:,.0f} spend | ${sales:,.0f} sales | {len(rows)} campaigns")
        completed.append(item)
    elif status == "FAILURE":
        print(f"❌ {name}/{market}/{month}: FAILED")
        completed.append(item)
    else:
        print(f"⏳ {name}/{market}/{month}: {status}")

# Remove completed from queue
queue = [q for q in queue if q not in completed]
with open(QUEUE_FILE, "w") as f:
    json.dump(queue, f)

remaining = len(queue)
completed = len(completed)
print(f"\nDone. {completed} reports downloaded. {remaining} still pending.")
