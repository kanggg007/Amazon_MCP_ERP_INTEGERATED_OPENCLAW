"""
Ads Health Scanner — Quick diagnostic for campaign problems
Pulls Ads API data and flags: high ACOS, overspending, paused campaigns, anomalies.

Usage: python3 engines/ads_health_scanner.py
"""
import httpx, os, json, gzip, time
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent.parent

def load_env():
    for line in (BASE / ".env").read_text().splitlines():
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

def get_ads_creds(store):
    if store == "04":
        return ("amzn1.application-oa2-client.13e97ea3d70a44528b083c71a6f52d04",
                os.environ["STORE_04_ADS_CLIENT_SECRET"],
                os.environ["STORE_04_ADS_REFRESH_TOKEN"])
    return (os.environ[f"STORE_{store}_ADS_CLIENT_ID"],
            os.environ[f"STORE_{store}_ADS_CLIENT_SECRET"],
            os.environ[f"STORE_{store}_ADS_REFRESH_TOKEN"])

PROFILES = {
    "CUCZUUS": {"US": ("02", "3465892858543553"), "CA": ("02", "3474360124295876")},
    "BOOLUU":  {"US": ("03", "2902488181372396"), "CA": ("03", "1000058889542591")},
    "Heliumx": {"US": ("04", "1416052288010129"), "CA": ("04", "3865316191299322")},
}

def pull_campaign_report(cid, csec, ref, pid, start, end, retries=3):
    r = httpx.post("https://api.amazon.com/auth/o2/token",
                   json={"grant_type": "refresh_token", "client_id": cid,
                         "client_secret": csec, "refresh_token": ref}, timeout=10)
    tk = r.json()["access_token"]
    h = {"Authorization": f"Bearer {tk}", "Amazon-Advertising-API-ClientId": cid,
         "Amazon-Advertising-API-Scope": pid, "Content-Type": "application/json"}
    body = {"startDate": start, "endDate": end,
            "configuration": {"adProduct": "SPONSORED_PRODUCTS", "groupBy": ["campaign"],
            "columns": ["cost", "campaignName", "campaignStatus", "campaignBudgetAmount","sales1d", "clicks",
            "impressions"],
            "reportTypeId": "spCampaigns", "timeUnit": "SUMMARY", "format": "GZIP_JSON"}}
    
    for attempt in range(retries):
        rr = httpx.post("https://advertising-api.amazon.com/reporting/reports",
                        headers=h, json=body, timeout=15)
        if rr.status_code == 200:
            break
        if rr.status_code in (425, 429):
            wait = 5 * (2 ** attempt)  # 5, 10, 20 sec backoff
            time.sleep(wait)
            continue
        return None, f"Report create failed: {rr.status_code} — {rr.text[:200]}"
    else:
        return None, f"Report create failed after {retries} retries (rate limited)"
    
    rid = rr.json()["reportId"]
    for i in range(30):
        time.sleep(5)
        rp = httpx.get(f"https://advertising-api.amazon.com/reporting/reports/{rid}",
                       headers=h, timeout=10)
        s = rp.json().get("status")
        if s == "COMPLETED":
            raw = gzip.decompress(httpx.get(rp.json()["url"], timeout=30).content).decode()
            rows = json.loads(raw)
            if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], list):
                rows = rows[0]
            return rows, None
        elif s == "FAILURE":
            return None, "Report failed"
    return None, "Report timeout"

def scan(store, months_back=1):
    """Run health scan for a store. Returns flagged issues."""
    
    from datetime import datetime, timedelta
    d = datetime.now() - timedelta(days=1)  # Yesterday — Ads API needs ~12h to process
    # Daily scan = yesterday's data
    yesterday = d.strftime("%Y-%m-%d")
    start = yesterday
    end = yesterday
    
    issues = []
    store_total = {"cost": 0, "sales": 0, "campaigns": 0}
    
    for market, (store_num, pid) in PROFILES.get(store, {}).items():
        cid, csec, ref = get_ads_creds(store_num)
        rows, error = pull_campaign_report(cid, csec, ref, pid, start, end)
        
        if error:
            issues.append({"severity": "error", "market": market, "message": f"Report failed: {error}"})
            continue
        
        market_cost = sum(float(r.get("cost", 0)) for r in rows if isinstance(r, dict))
        market_sales = sum(float(r.get("sales1d", 0)) for r in rows if isinstance(r, dict))
        market_acos = (market_cost / market_sales * 100) if market_sales > 0 else 0
        
        store_total["cost"] += market_cost
        store_total["sales"] += market_sales
        store_total["campaigns"] += len(rows)
        
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("campaignName", "Unknown")
            cost = float(row.get("cost", 0))
            sales = float(row.get("sales1d", 0))
            status = row.get("campaignStatus", "UNKNOWN")
            clicks = int(row.get("clicks", 0))
            acos = (cost / sales * 100) if sales > 0 else float("inf")
            
            # 🔴 High ACOS
            if cost > 50 and acos > 30:
                issues.append({
                    "severity": "red",
                    "market": market,
                    "campaign": name,
                    "metric": f"ACOS {acos:.0f}%",
                    "detail": f"${cost:.0f} spend → ${sales:.0f} sales, ACOS {acos:.0f}%",
                    "action": "Pause or lower bids"
                })
            # 🟡 Moderate ACOS
            elif cost > 50 and acos > 20:
                issues.append({
                    "severity": "yellow",
                    "market": market,
                    "campaign": name,
                    "metric": f"ACOS {acos:.0f}%",
                    "detail": f"${cost:.0f} spend → ${sales:.0f} sales",
                    "action": "Monitor and optimize keywords"
                })
            # ⚠️ Paused but had spend
            if status == "PAUSED" and cost > 0:
                issues.append({
                    "severity": "yellow",
                    "market": market,
                    "campaign": name,
                    "metric": f"PAUSED but spent ${cost:.0f}",
                    "detail": f"Campaign paused but had ${cost:.0f} spend this period",
                    "action": "Review — may need to reactivate or investigate"
                })
            # ⚠️ Zero sales
            if cost > 30 and sales == 0:
                issues.append({
                    "severity": "red",
                    "market": market,
                    "campaign": name,
                    "metric": "No sales",
                    "detail": f"${cost:.0f} spend with 0 sales",
                    "action": "Pause immediately"
                })
            # ✅ Excellent performance
            if cost > 100 and acos < 10:
                issues.append({
                    "severity": "green",
                    "market": market,
                    "campaign": name,
                    "metric": f"🔥 ACOS {acos:.0f}%",
                    "detail": f"${cost:.0f} spend → ${sales:.0f} sales",
                    "action": "Could increase budget"
                })
    
    store_total["acos"] = (store_total["cost"] / store_total["sales"] * 100) if store_total["sales"] > 0 else 0
    
    return issues, store_total


def print_scan(store, issues, totals):
    print(f"\n{'='*70}")
    print(f"  {store} Ads Health Scan — {totals['campaigns']} campaigns")
    print(f"  Total Spend: ${totals['cost']:,.0f} | Sales: ${totals['sales']:,.0f} | ACOS: {totals['acos']:.0f}%")
    print(f"{'='*70}")
    
    if not issues:
        print("  ✅ No issues found. All campaigns healthy.")
        return
    
    severities = {"red": "🔴", "yellow": "🟡", "green": "🟢", "error": "❌"}
    by_sev = defaultdict(list)
    for i in issues:
        by_sev[i["severity"]].append(i)
    
    for sev in ["red", "yellow", "green", "error"]:
        items = by_sev.get(sev, [])
        if not items:
            continue
        emoji = severities[sev]
        print(f"\n  {emoji} {sev.upper()} ({len(items)})")
        for item in items:
            mkt = item.get("market", "")
            camp = item.get("campaign", "")
            metric = item.get("metric", "")
            detail = item.get("detail", "")
            action = item.get("action", "")
            msg = item.get("message", "")
            if sev == "error":
                print(f"     [{mkt}] {msg}")
            else:
                print(f"     [{mkt}] {camp}")
                print(f"     {metric}: {detail}")
                print(f"     → {action}")
    
    print(f"\n  📊 Summary:")
    red_count = len(by_sev.get("red", []))
    yellow_count = len(by_sev.get("yellow", []))
    green_count = len(by_sev.get("green", []))
    print(f"     🔴 {red_count} critical  🟡 {yellow_count} warning  🟢 {green_count} excellent")


if __name__ == "__main__":
    load_env()
    for store in ["CUCZUUS", "BOOLUU", "Heliumx"]:
        issues, totals = scan(store)
        print_scan(store, issues, totals)
        time.sleep(2)  # Rate limit
