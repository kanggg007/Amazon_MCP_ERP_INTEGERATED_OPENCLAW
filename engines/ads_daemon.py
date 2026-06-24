#!/usr/bin/env python3
"""
Background Ads Report Poller
Runs continuously until all reports complete.
Usage: python3 engines/ads_daemon.py
"""
import httpx, os, json, gzip, time
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).parent.parent
QUEUE = Path("/tmp/ads_report_queue.json")
DATA = BASE / "data_lake" / "ads"
DATA.mkdir(parents=True, exist_ok=True)

def load_env():
    for line in (BASE / ".env").read_text().splitlines():
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

def get_store_creds(store):
    if store == "04":  # Heliumx
        return ("amzn1.application-oa2-client.13e97ea3d70a44528b083c71a6f52d04",
                os.environ["STORE_04_ADS_CLIENT_SECRET"],
                os.environ["STORE_04_ADS_REFRESH_TOKEN"])
    return (os.environ[f"STORE_{store}_ADS_CLIENT_ID"],
            os.environ[f"STORE_{store}_ADS_CLIENT_SECRET"],
            os.environ[f"STORE_{store}_ADS_REFRESH_TOKEN"])

load_env()

if not QUEUE.exists():
    # Initialize queue with BOOLUU May reports
    cid, csec, ref = get_store_creds("03")
    r = httpx.post("https://api.amazon.com/auth/o2/token",
                   json={"grant_type": "refresh_token", "client_id": cid,
                         "client_secret": csec, "refresh_token": ref}, timeout=10)
    tk = r.json()["access_token"]
    queue_items = []
    
    for market, pid in [("US", "2902488181372396"), ("CA", "1000058889542591")]:
        h = {"Authorization": f"Bearer {tk}", "Amazon-Advertising-API-ClientId": cid,
             "Amazon-Advertising-API-Scope": pid, "Content-Type": "application/json"}
        body = {"startDate": "2026-05-01", "endDate": "2026-05-31",
                "configuration": {"adProduct": "SPONSORED_PRODUCTS", "groupBy": ["campaign"],
                "columns": ["cost", "campaignName", "sales30d", "clicks"],
                "reportTypeId": "spCampaigns", "timeUnit": "SUMMARY", "format": "GZIP_JSON"}}
        rr = httpx.post("https://advertising-api.amazon.com/reporting/reports",
                        headers=h, json=body, timeout=15)
        rid = rr.json().get("reportId", "")
        if rid:
            queue_items.append({"name": "BOOLUU", "market": market, "store": "03",
                               "month": "2026-05", "report_type": "campaigns",
                               "report_id": rid, "profile_id": pid})
            print(f"Created BOOLUU {market}: {rid[:30]}...")
        time.sleep(1)
    
    with open(QUEUE, "w") as f:
        json.dump(queue_items, f)

# Poll until done
start = time.time()
while time.time() - start < 900:  # 15 min max
    with open(QUEUE) as f:
        queue = json.load(f)
    
    if not queue:
        print("✅ All reports collected!")
        break
    
    completed = []
    for item in queue:
        rid = item["report_id"]
        store = item["store"]
        pid = item["profile_id"]
        cid, csec, ref = get_store_creds(store)
        
        r = httpx.post("https://api.amazon.com/auth/o2/token",
                       json={"grant_type": "refresh_token", "client_id": cid,
                             "client_secret": csec, "refresh_token": ref}, timeout=10)
        tk = r.json()["access_token"]
        h = {"Authorization": f"Bearer {tk}", "Amazon-Advertising-API-ClientId": cid,
             "Amazon-Advertising-API-Scope": pid}
        
        rp = httpx.get(f"https://advertising-api.amazon.com/reporting/reports/{rid}",
                       headers=h, timeout=10)
        s = rp.json().get("status")
        
        if s == "COMPLETED":
            raw = gzip.decompress(httpx.get(rp.json()["url"], timeout=30).content).decode()
            rows = [json.loads(l) for l in raw.strip().split("\n") if l]
            cost = sum(float(r.get("cost", 0)) for r in rows if isinstance(r, dict))
            sales = sum(float(r.get("sales30d", 0)) for r in rows if isinstance(r, dict))
            
            fn = DATA / f"{item['name']}_{item['market']}_{item['month']}.json"
            with open(fn, "w") as f:
                json.dump({"cost": cost, "sales": sales, "rows": rows}, f)
            
            print(f"✅ {item['name']}/{item['market']}/{item['month']}: ${cost:,.0f} ({len(rows)} campaigns)")
            completed.append(item)
        elif s == "FAILURE":
            print(f"❌ {item['name']}/{item['market']}: FAILED")
            completed.append(item)
    
    queue = [q for q in queue if q not in completed]
    with open(QUEUE, "w") as f:
        json.dump(queue, f)
    
    if queue:
        print(f"  {len(queue)} pending... waiting 60s")
        time.sleep(60)
    else:
        print("✅ All done!")
        break
else:
    print("⏰ 15 min timeout. Reports still pending — they'll complete on Amazon's side.")
