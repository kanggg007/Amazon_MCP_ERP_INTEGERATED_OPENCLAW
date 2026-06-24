#!/usr/bin/env python3
"""Daily Ads Health Scan — run by cron, saves report to workspace."""
import httpx, os, json, gzip, time
from pathlib import Path
from datetime import datetime
from collections import defaultdict

BASE = Path("/Users/jiahe/.openclaw/workspace/Planned system project/amazon-ops-intel")
REPORT = Path("/Users/jiahe/.openclaw/workspace/ads_health_report.md")

def load_env():
    for line in (BASE / ".env").read_text().splitlines():
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

def creds(s):
    if s == "04":
        return ("amzn1.application-oa2-client.13e97ea3d70a44528b083c71a6f52d04",
                os.environ["STORE_04_ADS_CLIENT_SECRET"],
                os.environ["STORE_04_ADS_REFRESH_TOKEN"])
    return (os.environ[f"STORE_{s}_ADS_CLIENT_ID"],
            os.environ[f"STORE_{s}_ADS_CLIENT_SECRET"],
            os.environ[f"STORE_{s}_ADS_REFRESH_TOKEN"])

PROFILES = {
    "CUCZUUS": [("02", "3465892858543553", "US"), ("02", "3474360124295876", "CA")],
    "BOOLUU":  [("03", "2902488181372396", "US"), ("03", "1000058889542591", "CA")],
    "Heliumx": [("04", "1416052288010129", "US")],
}

load_env()
now = datetime.now()
start = f"{now.year}-{now.month:02d}-01"
end = now.strftime("%Y-%m-%d")

report_lines = [f"# Ads Health Report — {now.strftime('%Y-%m-%d')}",
                f"Period: {start} to {end}",
                "", "| Store | Market | Spend | Sales | ACOS | Campaigns | 🔴 | 🟡 | 🟢 |",
                "|---|---|---|---|---|---|---|---|---|"]

for store, profiles in PROFILES.items():
    for sn, pid, mkt in profiles:
        cid, csec, ref = creds(sn)
        r = httpx.post("https://api.amazon.com/auth/o2/token",
                       json={"grant_type": "refresh_token", "client_id": cid,
                             "client_secret": csec, "refresh_token": ref}, timeout=10)
        tk = r.json()["access_token"]
        h = {"Authorization": f"Bearer {tk}", "Amazon-Advertising-API-ClientId": cid,
             "Amazon-Advertising-API-Scope": pid, "Content-Type": "application/json"}
        
        body = {"startDate": start, "endDate": end,
                "configuration": {"adProduct": "SPONSORED_PRODUCTS", "groupBy": ["campaign"],
                "columns": ["cost", "campaignName", "campaignStatus", "sales30d", "clicks"],
                "reportTypeId": "spCampaigns", "timeUnit": "SUMMARY", "format": "GZIP_JSON"}}
        
        rr = httpx.post("https://advertising-api.amazon.com/reporting/reports",
                        headers=h, json=body, timeout=15)
        if rr.status_code != 200:
            report_lines.append(f"| {store} | {mkt} | ❌ | | | | | | |")
            continue
        
        rid = rr.json()["reportId"]
        # Poll up to 8 min
        got = False
        for i in range(40):
            time.sleep(12)
            rp = httpx.get(f"https://advertising-api.amazon.com/reporting/reports/{rid}",
                           headers=h, timeout=10)
            s = rp.json().get("status")
            if s == "COMPLETED":
                raw = gzip.decompress(httpx.get(rp.json()["url"], timeout=30).content).decode()
                rows = json.loads(raw)
                if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], list):
                    rows = rows[0]
                
                cost = sum(float(r.get("cost", 0)) for r in rows if isinstance(r, dict))
                sales = sum(float(r.get("sales30d", 0)) for r in rows if isinstance(r, dict))
                acos = (cost / sales * 100) if sales > 0 else 0
                
                red = sum(1 for r in rows if isinstance(r, dict) and (
                    float(r.get("cost",0)) > 50 and float(r.get("sales30d",0)) == 0 or
                    (float(r.get("sales30d",0)) > 0 and float(r.get("cost",0))/float(r.get("sales30d",0)) > 0.3)))
                yellow = sum(1 for r in rows if isinstance(r, dict) and (
                    float(r.get("cost",0)) > 50 and float(r.get("sales30d",0)) > 0 and
                    0.2 < float(r.get("cost",0))/float(r.get("sales30d",0)) <= 0.3))
                green = sum(1 for r in rows if isinstance(r, dict) and (
                    float(r.get("cost",0)) > 100 and float(r.get("sales30d",0)) > 0 and
                    float(r.get("cost",0))/float(r.get("sales30d",0)) < 0.1))
                
                report_lines.append(f"| {store} | {mkt} | ${cost:,.0f} | ${sales:,.0f} | {acos:.0f}% | {len(rows)} | {red} | {yellow} | {green} |")
                got = True
                break
            elif s == "FAILURE":
                report_lines.append(f"| {store} | {mkt} | ❌ FAILED | | | | | | |")
                got = True
                break
        if not got:
            report_lines.append(f"| {store} | {mkt} | ⏰ | | | | | | |")
        time.sleep(2)

report_lines.append(f"\n*Generated: {now.strftime('%Y-%m-%d %H:%M')} | Source: Ads API spCampaigns reports*")
REPORT.write_text("\n".join(report_lines))
print(f"✅ Report saved: {REPORT}")
