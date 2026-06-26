#!/usr/bin/env python3 -u
"""Create & poll March + April 2026 spSearchTerm reports."""
import httpx, os, json, gzip, time
from pathlib import Path

BASE = Path("/Users/jiahe/.openclaw/workspace/Planned system project/amazon-ops-intel")
DL = BASE / "data_lake/ads/search_terms"
DL.mkdir(parents=True, exist_ok=True)

for line in (BASE / ".env").read_text().splitlines():
    line = line.strip()
    if line and "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

CID = os.environ["STORE_02_ADS_CLIENT_ID"]
CSEC = os.environ["STORE_02_ADS_CLIENT_SECRET"]
REF = os.environ["STORE_02_ADS_REFRESH_TOKEN"]
PID = "3465892858543553"

def get_token():
    r = httpx.post("https://api.amazon.com/auth/o2/token", json={
        "grant_type":"refresh_token","client_id":CID,
        "client_secret":CSEC,"refresh_token":REF}, timeout=30)
    return r.json()["access_token"]

def hdrs(tk):
    return {"Authorization":f"Bearer {tk}","Amazon-Advertising-API-ClientId":CID,
            "Amazon-Advertising-API-Scope":PID,"Content-Type":"application/json"}

COLS = ["campaignName","campaignId","searchTerm","matchType","cost","impressions",
        "clicks","attributedSalesSameSku14d","purchasesSameSku14d"]

def create(tk, sd, ed):
    r = httpx.post("https://advertising-api.amazon.com/reporting/reports",
                   headers=hdrs(tk), json={"startDate":sd,"endDate":ed,
                   "configuration":{"adProduct":"SPONSORED_PRODUCTS","groupBy":["searchTerm"],
                   "columns":COLS,"reportTypeId":"spSearchTerm","timeUnit":"SUMMARY",
                   "format":"GZIP_JSON"}}, timeout=30)
    if r.status_code == 425 and "duplicate of" in r.text:
        return r.text.split("duplicate of : ")[-1].rstrip('".} ')
    if r.status_code != 200:
        print(f"  ERROR {r.status_code}: {r.text[:300]}", flush=True)
        return None
    return r.json()["reportId"]

def poll(tk, rid, label):
    deadline = time.time() + 900
    while time.time() < deadline:
        time.sleep(15)
        r = httpx.get(f"https://advertising-api.amazon.com/reporting/reports/{rid}",
                      headers=hdrs(tk), timeout=10)
        s = r.json().get("status")
        elapsed = int(time.time() - (deadline - 900))
        if elapsed % 60 < 15:
            print(f"  [{label}] {elapsed}s: {s}", flush=True)
        if s == "COMPLETED":
            url = r.json()["url"]
            raw = gzip.decompress(httpx.get(url, timeout=60).content).decode()
            return [json.loads(line) for line in raw.strip().split("\n") if line]
        elif s in ("FAILURE","CANCELLED"):
            print(f"  [{label}] {s}: {r.text[:200]}", flush=True)
            return None
    return None

# ── STEP 1: Create both reports ──
tk = get_token()
print("Token OK", flush=True)

# First check what happened with our earlier March report
rp = httpx.get("https://advertising-api.amazon.com/reporting/reports/f91b2a99-82d9-4875-9234-7c0fee666c4f",
               headers=hdrs(tk), timeout=10)
ms = rp.json().get("status")
print(f"Existing March report f91b2a99: {ms}", flush=True)

mar_rid = None
if ms == "COMPLETED":
    url = rp.json()["url"]
    raw = gzip.decompress(httpx.get(url, timeout=60).content).decode()
    mar_rows = [json.loads(line) for line in raw.strip().split("\n") if line]
    print(f"March: {len(mar_rows)} rows (existing report)", flush=True)
    with open(DL / "CUCZUUS_US_2026-03.json", "w") as f:
        json.dump({"report_id":"f91b2a99","start":"2026-03-22","end":"2026-03-31","rows":mar_rows}, f)
elif ms == "PENDING":
    print("March report still PENDING, polling it...", flush=True)
    mar_rows = poll(tk, "f91b2a99-82d9-4875-9234-7c0fee666c4f", "MAR")
    if mar_rows:
        with open(DL / "CUCZUUS_US_2026-03.json", "w") as f:
            json.dump({"report_id":"f91b2a99","start":"2026-03-22","end":"2026-03-31","rows":mar_rows}, f)
        print(f"March: {len(mar_rows)} rows saved", flush=True)
elif ms in ("FAILURE","CANCELLED"):
    # Try with new date range (March 23-31)
    mar_rid = create(tk, "2026-03-23", "2026-03-31")
    print(f"New March report: {mar_rid}", flush=True)
    if mar_rid:
        mar_rows = poll(tk, mar_rid, "MAR")
        if mar_rows:
            with open(DL / "CUCZUUS_US_2026-03.json", "w") as f:
                json.dump({"report_id":mar_rid,"start":"2026-03-23","end":"2026-03-31","rows":mar_rows}, f)
            print(f"March: {len(mar_rows)} rows saved", flush=True)

# Now create & poll April
print("\nCreating April report...", flush=True)
apr_rid = create(tk, "2026-04-01", "2026-04-30")
if apr_rid:
    print(f"April report: {apr_rid}", flush=True)
    apr_rows = poll(tk, apr_rid, "APR")
    if apr_rows:
        with open(DL / "CUCZUUS_US_2026-04.json", "w") as f:
            json.dump({"report_id":apr_rid,"start":"2026-04-01","end":"2026-04-30","rows":apr_rows}, f)
        print(f"April: {len(apr_rows)} rows saved", flush=True)

print("\n✓ Done.", flush=True)
