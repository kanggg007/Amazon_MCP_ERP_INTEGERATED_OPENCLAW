#!/usr/bin/env python3
"""
Ads Data Store — Local JSON-based storage for daily ad report ingestion
Fast, reliable, no DB dependencies. Cron writes, queries read near-instantly.
"""
import json, os
from pathlib import Path
from datetime import datetime, timedelta

DATA_DIR = Path("/Users/jiahe/.openclaw/workspace/Planned system project/amazon-ops-intel/data_lake/ads_daily")
DATA_DIR.mkdir(parents=True, exist_ok=True)

REPORTS = ["sp_campaigns", "sp_search_terms", "sp_targeting", "sp_advertised_product", "sp_purchased_product"]

def save(store, market, report_type, date, rows):
    """Save report data to JSON file."""
    filepath = DATA_DIR / f"{store}_{market}_{report_type}_{date}.json"
    with open(filepath, "w") as f:
        json.dump({
            "store": store, "market": market, "report_type": report_type,
            "date": date, "rows": rows, "count": len(rows)
        }, f, indent=2, ensure_ascii=False)
    return filepath

def load(store, market, report_type, date=None):
    """Load report data from JSON file. Near-instant."""
    if date is None:
        date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    filepath = DATA_DIR / f"{store}_{market}_{report_type}_{date}.json"
    if not filepath.exists():
        return None, f"No data for {store}/{market}/{report_type} on {date}"
    with open(filepath) as f:
        data = json.load(f)
    return data.get("rows", []), None

def get_latest_date(store, market, report_type):
    """Get most recent available date."""
    files = sorted(DATA_DIR.glob(f"{store}_{market}_{report_type}_*.json"))
    if not files: return None
    return files[-1].stem.rsplit("_", 1)[-1]

def query_campaign_summary(store, market, date=None):
    """Campaign summary for a store/market."""
    rows, err = load(store, market, "sp_campaigns", date)
    if err: return None, err
    
    cost = sum(float(r.get("cost",0)) for r in rows)
    sales = sum(float(r.get("sales1d",0)) for r in rows)
    impr = sum(int(r.get("impressions",0)) for r in rows)
    clk = sum(int(r.get("clicks",0)) for r in rows)
    acos = (cost/sales*100) if sales else 0
    
    campaigns = []
    for r in rows:
        if isinstance(r, dict):
            c = float(r.get("cost",0)); s = float(r.get("sales1d",0))
            a = (c/s*100) if s else 0
            cl = int(r.get("clicks",0))
            campaigns.append({
                "name": r.get("campaignName",""),
                "cost": c, "sales": s, "acos": a,
                "impressions": int(r.get("impressions",0)),
                "clicks": cl,
                "cpc": c/cl if cl else 0,
                "status": r.get("campaignStatus","")
            })
    
    return {
        "store": store, "market": market, "date": date,
        "cost": cost, "sales": sales, "acos": acos,
        "impressions": impr, "clicks": clk,
        "campaigns": sorted(campaigns, key=lambda x: x["cost"], reverse=True)
    }, None

if __name__ == "__main__":
    # Status check
    for store in ["CUCZUUS", "BOOLUU", "Heliumx"]:
        for market in ["US", "CA"]:
            dt = get_latest_date(store, market, "sp_campaigns")
            if dt: print(f"  {store}/{market}: {dt}")
            else: print(f"  {store}/{market}: NO DATA")
