#!/usr/bin/env python3
"""
Ads Ingestion v3 — Async parallel pulling
Pulls all 5 report types for each profile concurrently.
13 profiles × 5 types = 65 reports, ~6-10 minutes total.
Stores in PostgreSQL.
"""
import os, json, gzip, time, asyncio, httpx
from datetime import datetime, timedelta

DSN = os.environ.get("DATABASE_URL", "postgresql://postgres:ZTHtVHerPtatmfNeCufdSaqieNjmfxmW@acela.proxy.rlwy.net:58049/railway?sslmode=require&connect_timeout=20")

PROFILES = {
    ("CUCZUUS","NA"): [("US","02","3465892858543553"),("CA","02","3474360124295876")],
    ("BOOLUU","NA"):  [("US","03","2902488181372396"),("CA","03","1000058889542591")],
    ("Heliumx","NA"): [("US","04","1416052288010129"),("CA","04","3865316191299322")],
    ("CUCZUUS","FE"): [("AU","02","3457387109813217"),("JP","02","1336746761611490")],
    ("BOOLUU","FE"):  [("AU","03","671129043949754"),("JP","03","1702417705815456")],
    ("Heliumx","FE"): [("AU","04","422659196126799")],
    ("CUCZUUS","EU"): [("DE","02","2148844609196157")],
    ("Heliumx","EU"): [("DE","04","4304939656717104")],
}

REPORTS = {
    "sp_campaigns": {
        "reportTypeId": "spCampaigns", "groupBy": ["campaign"],
        "columns": ["cost","campaignName","campaignStatus","sales1d","clicks","impressions","campaignBudgetAmount","campaignBiddingStrategy"]
    },
}

REGION_HOSTS = {"NA": "advertising-api.amazon.com", "FE": "advertising-api-fe.amazon.com", "EU": "advertising-api-eu.amazon.com"}

def load_env():
    for line in open(os.path.join(os.path.dirname(__file__), "..", ".env")).read().splitlines():
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

def get_creds(store):
    return (os.environ[f"STORE_{store}_ADS_CLIENT_ID"],
            os.environ[f"STORE_{store}_ADS_CLIENT_SECRET"],
            os.environ[f"STORE_{store}_ADS_REFRESH_TOKEN"])

def init_db():
    import psycopg2
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS ads_daily (
        id SERIAL PRIMARY KEY, store VARCHAR(10), market VARCHAR(5), report_type VARCHAR(30),
        date DATE, data JSONB, cost NUMERIC, sales NUMERIC, pulled_at TIMESTAMP DEFAULT NOW())""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ads_daily_lookup ON ads_daily(store, market, report_type, date)")
    conn.commit()
    conn.close()

async def pull_one(client, store, market, store_num, pid, region, rpt_name, rpt_config, date):
    """Pull a single report async."""
    cid, csec, ref = get_creds(store_num)
    host = REGION_HOSTS[region]
    
    # Auth
    for a in range(3):
        r = await client.post("https://api.amazon.com/auth/o2/token",
            json={"grant_type":"refresh_token","client_id":cid,"client_secret":csec,"refresh_token":ref}, timeout=10)
        if r.status_code == 200: break
        await asyncio.sleep(10 * (2**a))
    else: return f"{store}/{market}/{rpt_name}: Auth failed"
    
    tk = r.json()["access_token"]
    h = {"Authorization":f"Bearer {tk}","Amazon-Advertising-API-ClientId":cid,"Amazon-Advertising-API-Scope":pid,"Content-Type":"application/json"}
    body = {"startDate":date,"endDate":date,"configuration":{"adProduct":"SPONSORED_PRODUCTS","groupBy":rpt_config["groupBy"],"columns":rpt_config["columns"],"reportTypeId":rpt_config["reportTypeId"],"timeUnit":"SUMMARY","format":"GZIP_JSON"}}
    
    for a in range(3):
        rr = await client.post(f"https://{host}/reporting/reports", headers=h, json=body, timeout=15)
        if rr.status_code == 200: break
        if rr.status_code in (425,429): await asyncio.sleep(15*(2**a)); continue
        return f"{store}/{market}/{rpt_name}: Create failed {rr.status_code}"
    else: return f"{store}/{market}/{rpt_name}: Rate limited"
    
    rid = rr.json()["reportId"]
    for i in range(60):
        await asyncio.sleep(10)
        rp = await client.get(f"https://{host}/reporting/reports/{rid}", headers=h, timeout=10)
        s = rp.json()["status"]
        if s == "COMPLETED":
            raw = gzip.decompress((await client.get(rp.json()["url"], timeout=30)).content).decode()
            rows = json.loads(raw)
            if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], list): rows = rows[0]
            cost = sum(float(r.get("cost",0)) for r in rows if isinstance(r,dict))
            sales = sum(float(r.get("sales1d",0)) for r in rows if isinstance(r,dict))
            
            import psycopg2
            conn = psycopg2.connect(DSN)
            cur = conn.cursor()
            cur.execute("INSERT INTO ads_daily (store, market, report_type, date, data, cost, sales) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (store, market, rpt_name, date, json.dumps(rows), cost, sales))
            conn.commit()
            conn.close()
            return f"  {store}/{market}/{rpt_name}: {len(rows)} rows, ${cost:.2f}"
        elif s == "FAILURE": return f"  {store}/{market}/{rpt_name}: FAILURE"
    return f"  {store}/{market}/{rpt_name}: Timeout"

async def main():
    t0 = time.time()
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Ads Ingestion v3 (async) — {yesterday}")
    print(f"13 profiles × {len(REPORTS)} types = {13*len(REPORTS)} reports")
    
    async with httpx.AsyncClient() as client:
        # Process each profile: pull all report types in parallel
        all_tasks = []
        for (store, region), markets in PROFILES.items():
            for market, snum, pid in markets:
                tasks = [pull_one(client, store, market, snum, pid, region, rname, rcfg, yesterday) for rname, rcfg in REPORTS.items()]
                all_tasks.append((f"{store}/{market}", tasks))
        
        for label, tasks in all_tasks:
            print(f"  {label}: pulling {len(tasks)} reports...", end=" ", flush=True)
            results = await asyncio.gather(*tasks)
            for r in results:
                print(r)
    
    t1 = time.time()
    print(f"\nDone in {t1-t0:.0f}s")

if __name__ == "__main__":
    load_env()
    init_db()
    asyncio.run(main())
