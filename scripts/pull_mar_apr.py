#!/usr/bin/env python3 -u
"""
Pull March, April 2026 search term data for 斜边镜自动 campaign analysis.
-U flag for unbuffered output.
"""
import httpx, os, json, gzip, time, sys
from pathlib import Path
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

sys.stdout.reconfigure(line_buffering=True)

BASE = Path("/Users/jiahe/.openclaw/workspace/Planned system project/amazon-ops-intel")
DATA_LAKE = BASE / "data_lake" / "ads" / "search_terms"
DATA_LAKE.mkdir(parents=True, exist_ok=True)

# ── Load env ──
for line in (BASE / ".env").read_text().splitlines():
    line = line.strip()
    if line and "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

CLIENT_ID = os.environ["STORE_02_ADS_CLIENT_ID"]
CLIENT_SECRET = os.environ["STORE_02_ADS_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["STORE_02_ADS_REFRESH_TOKEN"]
PROFILE_ID = "3465892858543553"

AUTH_URL = "https://api.amazon.com/auth/o2/token"
ADS_URL = "https://advertising-api.amazon.com"

def log(msg):
    print(msg, flush=True)

def get_token():
    log("Getting access token...")
    r = httpx.post(AUTH_URL, json={
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
    }, timeout=30)
    log(f"Token response: {r.status_code}")
    r.raise_for_status()
    return r.json()["access_token"]

def hdrs(token):
    return {
        "Authorization": f"Bearer {token}",
        "Amazon-Advertising-API-ClientId": CLIENT_ID,
        "Amazon-Advertising-API-Scope": PROFILE_ID,
        "Content-Type": "application/json",
    }

def create_report(token, start_date, end_date, label):
    log(f"\n[{label}] Creating report {start_date} → {end_date}")
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
    r = httpx.post(f"{ADS_URL}/reporting/reports", headers=hdrs(token), json=body, timeout=30)
    log(f"[{label}] Create status: {r.status_code}")
    if r.status_code == 425:
        # Duplicate - extract existing report ID from error message
        msg = r.text
        if "duplicate of" in msg:
            rid = msg.split("duplicate of : ")[-1].rstrip('".} ')
            log(f"[{label}] Duplicate - using existing Report ID: {rid}")
            return rid
    if r.status_code != 200:
        log(f"[{label}] ERROR: {r.text[:500]}")
        r.raise_for_status()
    rid = r.json().get("reportId")
    log(f"[{label}] Report ID: {rid}")
    return rid

def poll_report(token, report_id, label, max_wait=300):
    deadline = time.time() + max_wait
    while time.time() < deadline:
        time.sleep(5)
        r = httpx.get(f"{ADS_URL}/reporting/reports/{report_id}", headers=hdrs(token), timeout=30)
        if r.status_code != 200:
            log(f"[{label}] Poll error: {r.status_code}")
            continue
        status = r.json().get("status")
        log(f"[{label}] Status: {status}")
        if status == "COMPLETED":
            url = r.json().get("url")
            raw = httpx.get(url, timeout=60).content
            decompressed = gzip.decompress(raw).decode()
            rows = [json.loads(line) for line in decompressed.strip().split("\n") if line]
            log(f"[{label}] Got {len(rows)} rows")
            return rows
        elif status in ("FAILURE", "CANCELLED"):
            log(f"[{label}] Report FAILED: {json.dumps(r.json(), default=str)}")
            return None
    log(f"[{label}] TIMEOUT after {max_wait}s")
    return None

def pull_full_month(token, year, month, label):
    start = f"{year}-{month:02d}-01"
    end_dt = datetime(year, month, 1) + relativedelta(months=1, days=-1)
    end = end_dt.strftime("%Y-%m-%d")
    rid = create_report(token, start, end, label)
    if not rid:
        return None
    rows = poll_report(token, rid, label)
    if rows:
        fp = DATA_LAKE / f"CUCZUUS_US_{year}-{month:02d}.json"
        with open(fp, "w") as f:
            json.dump({"report_id": rid, "start": start, "end": end, "rows": rows}, f, indent=2)
        log(f"[{label}] Saved: {fp}")
    return rows

# ── MAIN ──
token = get_token()
log("Token obtained.")

# March: only 3/21-3/31 due to data retention
mar_start, mar_end = "2026-03-21", "2026-03-31"
mar_rid = create_report(token, mar_start, mar_end, "MAR")
mar_rows = poll_report(token, mar_rid, "MAR") if mar_rid else None
if mar_rows:
    fp = DATA_LAKE / "CUCZUUS_US_2026-03.json"
    with open(fp, "w") as f:
        json.dump({"report_id": mar_rid, "start": mar_start, "end": mar_end, "rows": mar_rows}, f, indent=2)
    log(f"[MAR] Saved: {fp}")

# April: full month
apr_rows = pull_full_month(token, 2026, 4, "APR")

log("\n✓ Done pulling data.")
