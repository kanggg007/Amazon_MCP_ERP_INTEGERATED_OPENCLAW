#!/usr/bin/env python3
"""Daily Ads Health Scan — parallel async, run by cron, saves report to workspace."""
import asyncio, httpx, os, json, gzip
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass

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

@dataclass
class AdResult:
    store: str
    market: str
    spend: float = 0
    sales: float = 0
    acos: float = 0
    campaigns: int = 0
    red: int = 0
    yellow: int = 0
    green: int = 0
    error: str = ""

async def fetch_one(client: httpx.AsyncClient, store: str, sn: str, pid: str, mkt: str, start: str, end: str, sem: asyncio.Semaphore) -> AdResult:
    res = AdResult(store=store, market=mkt)
    try:
        async with sem:
            cid, csec, ref = creds(sn)
            # 1. Auth
            tr = await client.post("https://api.amazon.com/auth/o2/token",
                json={"grant_type": "refresh_token", "client_id": cid,
                      "client_secret": csec, "refresh_token": ref}, timeout=httpx.Timeout(15))
            if tr.status_code != 200:
                res.error = f"Auth failed: {tr.status_code}"
                return res
            tk = tr.json()["access_token"]

            h = {"Authorization": f"Bearer {tk}", "Amazon-Advertising-API-ClientId": cid,
                 "Amazon-Advertising-API-Scope": pid, "Content-Type": "application/json"}

            # 2. Request report
            body = {"startDate": start, "endDate": end,
                    "configuration": {"adProduct": "SPONSORED_PRODUCTS", "groupBy": ["campaign"],
                    "columns": ["cost", "campaignName", "campaignStatus", "sales30d", "clicks"],
                    "reportTypeId": "spCampaigns", "timeUnit": "SUMMARY", "format": "GZIP_JSON"}}
            
            rr = await client.post("https://advertising-api.amazon.com/reporting/reports",
                                   headers=h, json=body, timeout=httpx.Timeout(15))
            if rr.status_code != 200:
                res.error = f"Report request failed: {rr.status_code} {rr.text[:200]}"
                return res

            rid = rr.json()["reportId"]

            # 3. Poll (up to 8 min, 15s intervals)
            for _ in range(32):
                await asyncio.sleep(15)
                rp = await client.get(f"https://advertising-api.amazon.com/reporting/reports/{rid}",
                                      headers=h, timeout=httpx.Timeout(15))
                status = rp.json().get("status")
                if status == "COMPLETED":
                    raw = gzip.decompress((await client.get(rp.json()["url"], timeout=httpx.Timeout(60))).content).decode()
                    rows = json.loads(raw)
                    if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], list):
                        rows = rows[0]

                    res.spend = sum(float(r.get("cost", 0)) for r in rows if isinstance(r, dict))
                    res.sales = sum(float(r.get("sales30d", 0)) for r in rows if isinstance(r, dict))
                    res.acos = (res.spend / res.sales * 100) if res.sales > 0 else 0
                    res.campaigns = len(rows)

                    for r in rows:
                        if not isinstance(r, dict):
                            continue
                        c = float(r.get("cost", 0))
                        s = float(r.get("sales30d", 0))
                        if c > 50 and s == 0:
                            res.red += 1  # spending with zero sales
                        elif s > 0 and c / s > 0.3:
                            res.red += 1  # ACOS > 30%
                        elif c > 50 and s > 0 and 0.2 < c / s <= 0.3:
                            res.yellow += 1  # ACOS 20-30%
                        elif c > 100 and s > 0 and c / s < 0.1:
                            res.green += 1  # ACOS < 10% high-spend winner
                    return res
                elif status == "FAILURE":
                    res.error = "Report generation FAILED"
                    return res
            res.error = "Polling timeout (8 min)"
            return res
    except Exception as e:
        res.error = str(e)[:200]
        return res

async def main():
    load_env()
    now = datetime.now()
    start = f"{now.year}-{now.month:02d}-01"
    end = now.strftime("%Y-%m-%d")

    sem = asyncio.Semaphore(5)  # Allow all 5 profiles to auth in parallel

    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)) as client:
        tasks = []
        for store, profiles in PROFILES.items():
            for sn, pid, mkt in profiles:
                tasks.append(fetch_one(client, store, sn, pid, mkt, start, end, sem))
        results = await asyncio.gather(*tasks)

    report_lines = [f"# Ads Health Report — {now.strftime('%Y-%m-%d')}",
                    f"Period: {start} to {end}",
                    "", "| Store | Market | Spend | Sales | ACOS | Campaigns | 🔴 High ACOS | 🟡 Watch | 🟢 Healthy |",
                    "|---|---|---|---|---|---|---|---|---|"]

    for r in results:
        if r.error:
            report_lines.append(f"| {r.store} | {r.market} | ❌ {r.error} | | | | | | |")
        else:
            report_lines.append(f"| {r.store} | {r.market} | ${r.spend:,.0f} | ${r.sales:,.0f} | {r.acos:.0f}% | {r.campaigns} | {r.red} | {r.yellow} | {r.green} |")

    report_lines.append(f"\n*Generated: {now.strftime('%Y-%m-%d %H:%M')} | Source: Ads API spCampaigns | 🟢 ACOS<10% high-spend | 🟡 ACOS 20-30% | 🔴 ACOS>30% or zero-sales*")
    REPORT.write_text("\n".join(report_lines))
    print(f"✅ Report saved: {REPORT}")

if __name__ == "__main__":
    asyncio.run(main())
