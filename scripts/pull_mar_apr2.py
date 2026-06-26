#!/usr/bin/env python3 -u
import httpx, os, json, gzip, time, sys
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)

BASE = Path("/Users/jiahe/.openclaw/workspace/Planned system project/amazon-ops-intel")

# Load env
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

print("Getting token...", flush=True)
r = httpx.post(AUTH_URL, json={
    "grant_type": "refresh_token",
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "refresh_token": REFRESH_TOKEN,
}, timeout=30)
tk = r.json()["access_token"]
print("Token obtained.", flush=True)

def hdrs():
    return {
        "Authorization": f"Bearer {tk}",
        "Amazon-Advertising-API-ClientId": CLIENT_ID,
        "Amazon-Advertising-API-Scope": PROFILE_ID,
        "Content-Type": "application/json",
    }

COLUMNS = [
    "campaignName", "campaignId", "searchTerm", "matchType",
    "cost", "impressions", "clicks",
    "attributedSalesSameSku14d", "purchasesSameSku14d"
]

def create_and_poll(start_date, end_date, label, dlpath):
    body = {
        "startDate": start_date,
        "endDate": end_date,
        "configuration": {
            "adProduct": "SPONSORED_PRODUCTS",
            "groupBy": ["searchTerm"],
            "columns": COLUMNS,
            "reportTypeId": "spSearchTerm",
            "timeUnit": "SUMMARY",
            "format": "GZIP_JSON",
        }
    }
    print(f"[{label}] Creating report...", flush=True)
    rr = httpx.post(f"{ADS_URL}/reporting/reports", headers=hdrs(), json=body, timeout=30)
    print(f"[{label}] Create status: {rr.status_code}", flush=True)
    
    if rr.status_code == 425:
        msg = rr.text
        if "duplicate of" in msg:
            rid = msg.split("duplicate of : ")[-1].rstrip('".} ')
            print(f"[{label}] Using existing report: {rid}", flush=True)
    elif rr.status_code == 200:
        rid = rr.json()["reportId"]
        print(f"[{label}] New report: {rid}", flush=True)
    else:
        print(f"[{label}] Error: {rr.text[:500]}", flush=True)
        return None
    
    # Check the existing report status
    print(f"[{label}] Checking existing report {rid}...", flush=True)
    rp = httpx.get(f"{ADS_URL}/reporting/reports/{rid}", headers=hdrs(), timeout=10)
    status = rp.json().get("status")
    print(f"[{label}] Current status: {status}", flush=True)
    
    if status == "COMPLETED":
        url = rp.json().get("url")
        raw = httpx.get(url, timeout=60).content
        decompressed = gzip.decompress(raw).decode()
        rows = [json.loads(line) for line in decompressed.strip().split("\n") if line]
        print(f"[{label}] Got {len(rows)} rows from completed report", flush=True)
    elif status in ("FAILURE", "CANCELLED"):
        print(f"[{label}] Old report {status}, creating new...", flush=True)
        body["startDate"] = start_date
        rr2 = httpx.post(f"{ADS_URL}/reporting/reports", headers=hdrs(), json=body, timeout=30)
        print(f"[{label}] Create2 status: {rr2.status_code}", flush=True)
        if rr2.status_code == 200:
            rid = rr2.json()["reportId"]
        elif rr2.status_code == 425:
            rid = rr2.text.split("duplicate of : ")[-1].rstrip('".} ')
        else:
            print(f"[{label}] Error on retry: {rr2.text[:300]}", flush=True)
            return None
        print(f"[{label}] Using report: {rid}", flush=True)
        status = "PENDING"
    
    if status == "PENDING":
        deadline = time.time() + 300
        while time.time() < deadline:
            time.sleep(5)
            rp = httpx.get(f"{ADS_URL}/reporting/reports/{rid}", headers=hdrs(), timeout=10)
            s = rp.json().get("status")
            print(f"[{label}] Status: {s}", flush=True)
            if s == "COMPLETED":
                url = rp.json().get("url")
                raw = httpx.get(url, timeout=60).content
                decompressed = gzip.decompress(raw).decode()
                rows = [json.loads(line) for line in decompressed.strip().split("\n") if line]
                print(f"[{label}] Got {len(rows)} rows", flush=True)
                break
            elif s in ("FAILURE", "CANCELLED"):
                print(f"[{label}] Report {s}", flush=True)
                return None
        else:
            print(f"[{label}] Timeout", flush=True)
            return None
    
    # Save
    outpath = BASE / dlpath
    outpath.parent.mkdir(parents=True, exist_ok=True)
    with open(outpath, "w") as f:
        json.dump({"report_id": rid, "start": start_date, "end": end_date, "rows": rows}, f, indent=2)
    print(f"[{label}] Saved to {dlpath}", flush=True)
    return rows

# March 21-31
mar = create_and_poll("2026-03-21", "2026-03-31", "MAR", "data_lake/ads/search_terms/CUCZUUS_US_2026-03.json")
print(f"March rows: {len(mar) if mar else 0}", flush=True)

# April 1-30
apr = create_and_poll("2026-04-01", "2026-04-30", "APR", "data_lake/ads/search_terms/CUCZUUS_US_2026-04.json")
print(f"April rows: {len(apr) if apr else 0}", flush=True)

print("\nDone", flush=True)
