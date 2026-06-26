#!/usr/bin/env python3
"""
Ads Data Ingestion Pipeline
Run daily via cron (3 AM) → pulls all report types for all stores → stores as local JSON
Reads instantly by ads_data_store.py for queries and health scans.
"""
import os, httpx, json, gzip, time
from pathlib import Path
from datetime import datetime, timedelta

BASE = Path(__file__).parent.parent
DATA_DIR = BASE / "data_lake" / "ads_daily"
DATA_DIR.mkdir(parents=True, exist_ok=True)

PROFILES = {
    "CUCZUUS": {"US": ("02","3465892858543553"), "CA": ("02","3474360124295876")},
    "BOOLUU":  {"US": ("03","2902488181372396"), "CA": ("03","1000058889542591")},
    "Heliumx": {"US": ("04","1416052288010129"), "CA": ("04","3865316191299322")},
}

REPORTS = {
    "sp_campaigns": {
        "reportTypeId": "spCampaigns",
        "groupBy": ["campaign"],
        "columns": ["cost","campaignName","campaignStatus","campaignBudgetAmount","sales1d","clicks","impressions","campaignBiddingStrategy"]
    },
    "sp_search_terms": {
        "reportTypeId": "spSearchTerm",
        "groupBy": ["searchTerm"],
        "columns": ["searchTerm","keyword","matchType","cost","clicks","impressions","sales1d","campaignName"]
    },
    "sp_targeting": {
        "reportTypeId": "spTargeting",
        "groupBy": ["targeting"],
        "columns": ["keyword","matchType","cost","clicks","impressions","sales1d","campaignName"]
    },
    "sp_advertised_product": {
        "reportTypeId": "spAdvertisedProduct",
        "groupBy": ["advertiser"],
        "columns": ["advertisedAsin","advertisedSku","cost","clicks","impressions","sales1d"]
    },
    "sp_purchased_product": {
        "reportTypeId": "spPurchasedProduct",
        "groupBy": ["asin"],
        "columns": ["purchasedAsin","advertisedAsin","advertisedSku","sales1d","sales7d","sales30d","purchases1d","purchases7d","purchases30d","campaignName"]
    },
}

def load_env():
    for line in open(BASE / ".env").read().splitlines():
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

def get_ads_creds(store):
    return (os.environ[f"STORE_{store}_ADS_CLIENT_ID"],
            os.environ[f"STORE_{store}_ADS_CLIENT_SECRET"],
            os.environ[f"STORE_{store}_ADS_REFRESH_TOKEN"])

def save_report(store, market, report_type, date, rows):
    filepath = DATA_DIR / f"{store}_{market}_{report_type}_{date}.json"
    with open(filepath, "w") as f:
        json.dump({
            "store": store, "market": market, "report_type": report_type,
            "date": date, "rows": rows, "count": len(rows)
        }, f, indent=2, ensure_ascii=False)

def pull_and_store(store, market, store_num, profile_id, report_name, rpt_config, report_date):
    cid, csec, ref = get_ads_creds(store_num)
    
    for attempt in range(3):
        r = httpx.post("https://api.amazon.com/auth/o2/token",
            json={"grant_type":"refresh_token","client_id":cid,"client_secret":csec,"refresh_token":ref}, timeout=10)
        if r.status_code == 200: break
        time.sleep(10 * (2**attempt))
    else: return f"Auth failed: {r.status_code}"
    
    tk = r.json()["access_token"]
    h = {"Authorization":f"Bearer {tk}","Amazon-Advertising-API-ClientId":cid,"Amazon-Advertising-API-Scope":profile_id,"Content-Type":"application/json"}
    
    body = {
        "startDate": report_date, "endDate": report_date,
        "configuration": {
            "adProduct": "SPONSORED_PRODUCTS",
            "groupBy": rpt_config["groupBy"],
            "columns": rpt_config["columns"],
            "reportTypeId": rpt_config["reportTypeId"],
            "timeUnit": "SUMMARY",
            "format": "GZIP_JSON"
        }
    }
    
    for attempt in range(3):
        rr = httpx.post("https://advertising-api.amazon.com/reporting/reports", headers=h, json=body, timeout=15)
        if rr.status_code == 200: break
        if rr.status_code in (425,429): time.sleep(15*(2**attempt)); continue
        return f"Create failed: {rr.status_code}"
    else: return f"Create failed after 3 retries"
    
    rid = rr.json()["reportId"]
    
    for i in range(60):
        time.sleep(10)
        rp = httpx.get(f"https://advertising-api.amazon.com/reporting/reports/{rid}", headers=h, timeout=10)
        s = rp.json()["status"]
        if s == "COMPLETED":
            raw = gzip.decompress(httpx.get(rp.json()["url"], timeout=30).content).decode()
            rows = json.loads(raw)
            if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], list):
                rows = rows[0]
            save_report(store, market, report_name, report_date, rows)
            cost = sum(float(r.get("cost",0)) for r in rows if isinstance(r,dict))
            return f"OK: {len(rows)} rows, ${cost:,.2f}"
        elif s == "FAILURE": return "FAILURE"
    return "Timeout after 10min"

if __name__ == "__main__":
    load_env()
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    print(f"Ads Ingestion — {yesterday}")
    print("="*60)
    
    total_time = 0
    for store, markets in PROFILES.items():
        for market, (store_num, pid) in markets.items():
            for rpt_name, rpt_config in REPORTS.items():
                print(f"  {store}/{market}/{rpt_name}...", end=" ", flush=True)
                try:
                    result = pull_and_store(store, market, store_num, pid, rpt_name, rpt_config, yesterday)
                    print(result)
                except Exception as e:
                    print(f"ERROR: {e}")
                time.sleep(5)
    
    print()
    print("Done. Data in data_lake/ads_daily/")
