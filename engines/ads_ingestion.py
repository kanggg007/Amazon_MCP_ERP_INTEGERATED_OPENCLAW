"""
Ads Ingestion v2 — stores in PostgreSQL (Railway persistent)
"""
import os, httpx, json, gzip, time, psycopg2
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).parent.parent
DSN = os.environ.get("DATABASE_URL", "postgresql://postgres:ZTHtVHerPtatmfNeCufdSaqieNjmfxmW@acela.proxy.rlwy.net:58049/railway?sslmode=require&connect_timeout=20")

PROFILES = {
    # NA region (advertising-api.amazon.com)
    "CUCZUUS": {"US": ("02","3465892858543553","na"), "CA": ("02","3474360124295876","na")},
    "BOOLUU":  {"US": ("03","2902488181372396","na"), "CA": ("03","1000058889542591","na")},
    "Heliumx": {"US": ("04","1416052288010129","na"), "CA": ("04","3865316191299322","na")},
    # EU region (advertising-api-eu.amazon.com)
    "CUCZUUS_EU": {"DE": ("02","2148844609196157","eu")},
    "Heliumx_EU": {"DE": ("04","4304939656717104","eu")},
}

REPORTS = {
    "sp_campaigns": {
        "reportTypeId": "spCampaigns",
        "groupBy": ["campaign"],
        "columns": ["cost","campaignName","campaignStatus","sales1d","clicks","impressions","campaignBudgetAmount","campaignBiddingStrategy"]
    },
}

def init_db():
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ads_daily (
            id SERIAL PRIMARY KEY,
            store VARCHAR(10), market VARCHAR(5), report_type VARCHAR(30),
            date DATE, data JSONB, cost NUMERIC, sales NUMERIC,
            pulled_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ads_daily_lookup ON ads_daily(store, market, report_type, date)")
    conn.commit()
    conn.close()

def get_ads_creds(store):
    return (os.environ[f"STORE_{store}_ADS_CLIENT_ID"],
            os.environ[f"STORE_{store}_ADS_CLIENT_SECRET"],
            os.environ[f"STORE_{store}_ADS_REFRESH_TOKEN"])

def pull_and_store(store, market, store_num, pid, region, rpt_name, rpt_config, date):
    cid, csec, ref = get_ads_creds(store_num)
    api_host = "advertising-api-fe.amazon.com" if region == "fe" else ("advertising-api-eu.amazon.com" if region == "eu" else "advertising-api.amazon.com")
    for a in range(3):
        r = httpx.post("https://api.amazon.com/auth/o2/token",
            json={"grant_type":"refresh_token","client_id":cid,"client_secret":csec,"refresh_token":ref}, timeout=10)
        if r.status_code == 200: break
        time.sleep(10 * (2**a))
    else: return f"Auth failed"
    
    tk = r.json()["access_token"]
    h = {"Authorization":f"Bearer {tk}","Amazon-Advertising-API-ClientId":cid,"Amazon-Advertising-API-Scope":pid,"Content-Type":"application/json"}
    body = {"startDate":date,"endDate":date,"configuration":{"adProduct":"SPONSORED_PRODUCTS","groupBy":rpt_config["groupBy"],"columns":rpt_config["columns"],"reportTypeId":rpt_config["reportTypeId"],"timeUnit":"SUMMARY","format":"GZIP_JSON"}}
    
    for a in range(3):
        rr = httpx.post(f"https://{api_host}/reporting/reports", headers=h, json=body, timeout=15)
        if rr.status_code == 200: break
        if rr.status_code in (425,429): time.sleep(15*(2**a)); continue
        return f"Create failed: {rr.status_code}"
    else: return "Rate limited"
    
    rid = rr.json()["reportId"]
    for i in range(60):
        time.sleep(10)
        rp = httpx.get(f"https://{api_host}/reporting/reports/{rid}", headers=h, timeout=10)
        s = rp.json()["status"]
        if s == "COMPLETED":
            raw = gzip.decompress(httpx.get(rp.json()["url"], timeout=30).content).decode()
            rows = json.loads(raw)
            if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], list): rows = rows[0]
            cost = sum(float(r.get("cost",0)) for r in rows if isinstance(r,dict))
            sales = sum(float(r.get("sales1d",0)) for r in rows if isinstance(r,dict))
            
            conn = psycopg2.connect(DSN)
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO ads_daily (store, market, report_type, date, data, cost, sales) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (store, market, rpt_name, date, json.dumps(rows), cost, sales)
            )
            conn.commit()
            conn.close()
            return f"OK: {len(rows)} rows, ${cost:.2f}"
        elif s == "FAILURE": return "FAILURE"
    return "Timeout"

if __name__ == "__main__":
    init_db()
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Ads Ingestion v2 (PostgreSQL) — {yesterday}")
    for store, markets in PROFILES.items():
        base_store = store.replace("_FE","").replace("_EU","")
        for market, (snum, pid, region) in markets.items():
            for rname, rcfg in REPORTS.items():
                try:
                    result = pull_and_store(base_store, market, snum, pid, region, rname, rcfg, yesterday)
                    print(f"  {base_store}/{market}/{rname}: {result}")
                except Exception as e:
                    print(f"  {store}/{market}/{rname}: ERROR — {e}")
                time.sleep(5)
    print("Done.")
