#!/usr/bin/env python3
"""
Pull March, April, May 2026 search term data for 斜边镜自动 campaign analysis.
"""
import httpx, os, json, gzip, time, sys
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from dateutil.relativedelta import relativedelta

BASE = Path("/Users/jiahe/.openclaw/workspace/Planned system project/amazon-ops-intel")
DATA_LAKE = BASE / "data_lake" / "ads" / "search_terms"
DATA_LAKE.mkdir(parents=True, exist_ok=True)

# ── Load env ──
for line in (BASE / ".env").read_text().splitlines():
    line = line.strip()
    if line and "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

# ── CUCZUUS US credentials ──
CLIENT_ID = os.environ["STORE_02_ADS_CLIENT_ID"]
CLIENT_SECRET = os.environ["STORE_02_ADS_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["STORE_02_ADS_REFRESH_TOKEN"]
PROFILE_ID = "3465892858543553"

AUTH_URL = "https://api.amazon.com/auth/o2/token"
ADS_URL = "https://advertising-api.amazon.com"

def get_token():
    r = httpx.post(AUTH_URL, json={
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
    }, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]

def headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Amazon-Advertising-API-ClientId": CLIENT_ID,
        "Amazon-Advertising-API-Scope": PROFILE_ID,
        "Content-Type": "application/json",
    }

def create_report(token, start_date, end_date):
    """Create spSearchTerm report."""
    body = {
        "startDate": start_date,
        "endDate": end_date,
        "configuration": {
            "adProduct": "SPONSORED_PRODUCTS",
            "groupBy": ["searchTerm"],
            "columns": [
                "campaignName", "campaignId", "searchTerm", "matchType",
                "cost", "impressions", "clicks",
                "attributedSalesSameSku14d", "purchasesSameSku14d"
            ],
            "reportTypeId": "spSearchTerm",
            "timeUnit": "SUMMARY",
            "format": "GZIP_JSON",
        }
    }
    print(f"  Creating report {start_date} → {end_date}...")
    r = httpx.post(f"{ADS_URL}/reporting/reports", headers=headers(token), json=body, timeout=15)
    print(f"  Response: {r.status_code}")
    if r.status_code != 200:
        print(f"  ERROR: {r.text[:500]}")
        r.raise_for_status()
    data = r.json()
    print(f"  Report ID: {data.get('reportId')}")
    return data.get("reportId")

def poll_report(token, report_id, label, max_wait=300):
    """Poll until COMPLETED."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        time.sleep(5)
        r = httpx.get(f"{ADS_URL}/reporting/reports/{report_id}", headers=headers(token), timeout=10)
        if r.status_code != 200:
            print(f"  Poll error: {r.status_code} {r.text[:200]}")
            continue
        data = r.json()
        status = data.get("status")
        print(f"  [{label}] Status: {status}")
        if status == "COMPLETED":
            url = data.get("url")
            raw = httpx.get(url, timeout=60).content
            decompressed = gzip.decompress(raw).decode()
            rows = [json.loads(line) for line in decompressed.strip().split("\n") if line]
            print(f"  [{label}] Got {len(rows)} rows")
            return rows
        elif status in ("FAILURE", "CANCELLED"):
            print(f"  [{label}] Report FAILED: {data}")
            return None
    print(f"  [{label}] TIMEOUT after {max_wait}s")
    return None

def pull_month(token, year, month, label):
    """Pull one month of search term data."""
    start = f"{year}-{month:02d}-01"
    end_dt = datetime(year, month, 1) + relativedelta(months=1, days=-1)
    max_end = datetime.now() + timedelta(days=1)
    if end_dt > max_end:
        end_dt = max_end
    end = end_dt.strftime("%Y-%m-%d")
    
    print(f"\n{'='*60}")
    print(f"Pulling {label} ({start} → {end})")
    print(f"{'='*60}")
    
    rid = create_report(token, start, end)
    if not rid:
        return None
    
    rows = poll_report(token, rid, label)
    if rows is None:
        return None
    
    # Save to data lake
    filepath = DATA_LAKE / f"CUCZUUS_US_{year}-{month:02d}.json"
    with open(filepath, "w") as f:
        json.dump({"report_id": rid, "start": start, "end": end, "rows": rows}, f, indent=2)
    print(f"  Saved: {filepath}")
    
    return rows

def load_json(path):
    with open(path) as f:
        data = json.load(f)
    return data.get("rows", data) if isinstance(data, dict) else data

# ── MAIN ──
def main():
    token = get_token()
    print(f"Token obtained.")
    
    # Pull March (from 3/21 due to data retention) and April (May already exists)
    mar_start = "2026-03-21"
    mar_end = "2026-03-31"
    print(f"\n{'='*60}\nPulling March 2026 ({mar_start} → {mar_end}) [retention-limited]\n{'='*60}")
    rid = create_report(token, mar_start, mar_end)
    mar_rows = poll_report(token, rid, "March 2026") if rid else None
    if mar_rows:
        filepath = DATA_LAKE / "CUCZUUS_US_2026-03.json"
        with open(filepath, "w") as f:
            json.dump({"report_id": rid, "start": mar_start, "end": mar_end, "rows": mar_rows}, f, indent=2)
        print(f"  Saved: {filepath}")
    
    apr_rows = pull_month(token, 2026, 4, "April 2026")
    
    print("\n✓ All data pulled. Run analysis script next.")

if __name__ == "__main__":
    main()
