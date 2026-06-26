#!/usr/bin/env python3
"""
Ads Query Engine — Instant reads from Railway DB
No Ads API calls. Data already ingested by ads_ingestion.py
"""
import json, psycopg2
from datetime import datetime, timedelta

DSN = "postgresql://postgres:ZTHtVHerPtatmfNeCufdSaqieNjmfxmW@acela.proxy.rlwy.net:58049/railway"

def query(store, market, report_type, date=None):
    """Pull stored report data from DB. Near-instant."""
    if date is None:
        date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute(f"SELECT data FROM {report_type} WHERE store=%s AND market=%s AND report_date=%s ORDER BY pulled_at DESC LIMIT 1", (store, market, date))
    row = cur.fetchone()
    conn.close()
    
    if not row:
        return None, f"No data for {store}/{market}/{report_type} on {date}"
    
    return json.loads(row[0]), None

def query_campaign_summary(store, market, date=None):
    """Get campaign summary for a store/market."""
    rows, err = query(store, market, "sp_campaigns", date)
    if err: return None, err
    
    cost = sum(float(r.get("cost",0)) for r in rows)
    sales = sum(float(r.get("sales1d",0)) for r in rows)
    impr = sum(int(r.get("impressions",0)) for r in rows)
    clk = sum(int(r.get("clicks",0)) for r in rows)
    acos = (cost/sales*100) if sales else 0
    
    return {
        "store": store, "market": market, "date": date,
        "cost": cost, "sales": sales, "acos": acos,
        "impressions": impr, "clicks": clk,
        "campaigns": [{
            "name": r.get("campaignName",""),
            "cost": float(r.get("cost",0)),
            "sales": float(r.get("sales1d",0)),
            "impressions": int(r.get("impressions",0)),
            "clicks": int(r.get("clicks",0)),
            "status": r.get("campaignStatus","")
        } for r in rows if isinstance(r, dict)]
    }, None

def query_search_terms(store, market, date=None, min_cost=0):
    """Get search terms with spend."""
    rows, err = query(store, market, "sp_search_terms", date)
    if err: return None, err
    
    terms = []
    for r in rows:
        if not isinstance(r, dict): continue
        c = float(r.get("cost",0))
        if c >= min_cost:
            terms.append({
                "searchTerm": r.get("searchTerm",""),
                "keyword": r.get("keyword",""),
                "matchType": r.get("matchType",""),
                "cost": c,
                "clicks": int(r.get("clicks",0)),
                "impressions": int(r.get("impressions",0)),
                "campaignName": r.get("campaignName","")
            })
    return sorted(terms, key=lambda x: x["cost"], reverse=True), None

if __name__ == "__main__":
    # Quick test
    summary, err = query_campaign_summary("CUCZUUS", "US", "2026-06-24")
    if err: print(f"Error: {err}")
    else: print(json.dumps(summary, indent=2, ensure_ascii=False)[:500])
