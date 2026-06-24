"""
Ads Data Sync Engine
Pulls historical campaign + keyword data from Amazon Ads API → Railway PostgreSQL.
Built for CUCZUUS & BOOLUU, all markets.

Usage:
    python3 engines/ads_sync_engine.py --store CUCZUUS --months 12
    python3 engines/ads_sync_engine.py --store ALL --months 6 --collect-only
"""
import httpx, os, time, json, gzip, sys, argparse
from pathlib import Path
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from collections import defaultdict

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

# ── Config ────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent

def load_env():
    for line in (BASE_DIR / ".env").read_text().splitlines():
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

def get_db():
    engine = sa.create_engine(os.environ["DATABASE_URL"], pool_size=5, max_overflow=10)
    return sessionmaker(bind=engine)()

# ── Ads Profile Registry ──────────────────────────────────────
ADS_PROFILES = {
    "CUCZUUS": {
        "US": {"profile_id": "3465892858543553", "ads_client": "STORE_02_ADS_CLIENT_ID",
               "ads_secret": "STORE_02_ADS_CLIENT_SECRET", "ads_refresh": "STORE_02_ADS_REFRESH_TOKEN"},
        "CA": {"profile_id": "3474360124295876", "ads_client": "STORE_02_ADS_CLIENT_ID",
               "ads_secret": "STORE_02_ADS_CLIENT_SECRET", "ads_refresh": "STORE_02_ADS_REFRESH_TOKEN"},
        "BR": {"profile_id": "4006392837291348", "ads_client": "STORE_02_ADS_CLIENT_ID",
               "ads_secret": "STORE_02_ADS_CLIENT_SECRET", "ads_refresh": "STORE_02_ADS_REFRESH_TOKEN"},
        "MX": {"profile_id": "1967957568965418", "ads_client": "STORE_02_ADS_CLIENT_ID",
               "ads_secret": "STORE_02_ADS_CLIENT_SECRET", "ads_refresh": "STORE_02_ADS_REFRESH_TOKEN"},
    },
    "BOOLUU": {
        "US": {"profile_id": "2902488181372396", "ads_client": "STORE_03_ADS_CLIENT_ID",
               "ads_secret": "STORE_03_ADS_CLIENT_SECRET", "ads_refresh": "STORE_03_ADS_REFRESH_TOKEN"},
        "CA": {"profile_id": "1000058889542591", "ads_client": "STORE_03_ADS_CLIENT_ID",
               "ads_secret": "STORE_03_ADS_CLIENT_SECRET", "ads_refresh": "STORE_03_ADS_REFRESH_TOKEN"},
        "MX": {"profile_id": "3078520327811488", "ads_client": "STORE_03_ADS_CLIENT_ID",
               "ads_secret": "STORE_03_ADS_CLIENT_SECRET", "ads_refresh": "STORE_03_ADS_REFRESH_TOKEN"},
        "BR": {"profile_id": "3825787828028947", "ads_client": "STORE_03_ADS_CLIENT_ID",
               "ads_secret": "STORE_03_ADS_CLIENT_SECRET", "ads_refresh": "STORE_03_ADS_REFRESH_TOKEN"},
    },
}

# ── Report Engine ─────────────────────────────────────────────
class AdsReportEngine:
    """Creates, polls, and downloads Amazon Ads API reports."""
    
    def __init__(self, client_id, client_secret, refresh_token):
        self.cid = client_id
        self.csec = client_secret
        self.ref = refresh_token
        self._token = None
        self._token_expiry = 0
    
    def _get_token(self):
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        r = httpx.post("https://api.amazon.com/auth/o2/token", json={
            "grant_type": "refresh_token", "client_id": self.cid,
            "client_secret": self.csec, "refresh_token": self.ref
        }, timeout=10)
        self._token = r.json()["access_token"]
        self._token_expiry = time.time() + 3500
        return self._token
    
    def _headers(self, profile_id):
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Amazon-Advertising-API-ClientId": self.cid,
            "Amazon-Advertising-API-Scope": profile_id,
            "Content-Type": "application/json"
        }
    
    def create_report(self, profile_id, start_date, end_date, report_type="spCampaigns",
                      group_by="campaign", columns=None):
        if columns is None:
            columns = ["cost", "sales30d", "clicks", "impressions", "purchases30d"]
        
        body = {
            "startDate": start_date,
            "endDate": end_date,
            "configuration": {
                "adProduct": "SPONSORED_PRODUCTS",
                "groupBy": [group_by],
                "columns": columns,
                "reportTypeId": report_type,
                "timeUnit": "SUMMARY",
                "format": "GZIP_JSON"
            }
        }
        r = httpx.post("https://advertising-api.amazon.com/reporting/reports",
                        headers=self._headers(profile_id), json=body, timeout=15)
        if r.status_code != 200:
            raise Exception(f"Report create failed: {r.status_code} {r.text[:200]}")
        return r.json()["reportId"]
    
    def poll_report(self, profile_id, report_id, max_wait=180):
        """Poll until COMPLETED. Returns parsed rows."""
        h = self._headers(profile_id)
        deadline = time.time() + max_wait
        
        while time.time() < deadline:
            time.sleep(5)
            r = httpx.get(f"https://advertising-api.amazon.com/reporting/reports/{report_id}",
                          headers=h, timeout=10)
            d = r.json()
            status = d.get("status")
            if status == "COMPLETED":
                raw = httpx.get(d["url"], timeout=30).content
                data = gzip.decompress(raw).decode()
                return [json.loads(l) for l in data.strip().split("\n") if l]
            elif status == "FAILURE":
                raise Exception(f"Report failed: {d}")
        
        raise TimeoutError(f"Report {report_id} timed out after {max_wait}s")
    
    def pull_campaign_data(self, profile_id, year, month):
        """Pull one month of campaign data."""
        start = f"{year}-{month:02d}-01"
        end = datetime(year, month, 1) + relativedelta(months=1, days=-1)
        end_str = end.strftime("%Y-%m-%d")
        
        rid = self.create_report(profile_id, start, end_str)
        rows = self.poll_report(profile_id, rid)
        return rows
    
    def pull_keyword_data(self, profile_id, year, month):
        """Pull search term performance data."""
        start = f"{year}-{month:02d}-01"
        end = datetime(year, month, 1) + relativedelta(months=1, days=-1)
        end_str = end.strftime("%Y-%m-%d")
        
        rid = self.create_report(profile_id, start, end_str, "spSearchTerm",
                                  "searchTerm", ["cost", "clicks", "impressions",
                                                  "sales30d", "purchases30d", "conversions30d"])
        rows = self.poll_report(profile_id, rid)
        return rows

# ── Database Sync ─────────────────────────────────────────────
def sync_to_db(session, store_id, marketplace_id, rows, profile_id):
    """Insert/update campaign performance rows."""
    from db.models import AdPerformance
    
    for row in rows:
        campaign_id = row.get("campaignId", "")
        campaign_name = row.get("campaignName", "")
        cost = float(row.get("cost", 0))
        sales = float(row.get("sales30d", 0))
        clicks = int(row.get("clicks", 0))
        impressions = int(row.get("impressions", 0))
        orders = int(row.get("purchases30d", 0))
        
        # Upsert logic
        existing = session.query(AdPerformance).filter_by(
            store_id=store_id, marketplace_id=marketplace_id,
            campaign_id=campaign_id, date=datetime.now().date()
        ).first()
        
        if existing:
            existing.spend = cost
            existing.sales = sales
            existing.clicks = clicks
            existing.impressions = impressions
            existing.orders = orders
            existing.raw_data = row
        else:
            session.add(AdPerformance(
                store_id=store_id, marketplace_id=marketplace_id,
                campaign_id=campaign_id, ad_group_id=None, sku=None, asin=None,
                keyword=campaign_name, match_type="campaign",
                date=datetime.now().date(),
                impressions=impressions, clicks=clicks, spend=cost,
                sales=sales, orders=orders, raw_data=row
            ))
    session.commit()


# ── CLI ───────────────────────────────────────────────────────
def main():
    load_env()
    
    parser = argparse.ArgumentParser(description="Ads Sync Engine")
    parser.add_argument("--store", default="CUCZUUS", choices=["CUCZUUS", "BOOLUU", "ALL"])
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--collect-only", action="store_true", help="Save JSON, skip DB")
    parser.add_argument("--market", default=None, help="Filter to specific market (US, CA)")
    args = parser.parse_args()
    
    stores = ["CUCZUUS", "BOOLUU"] if args.store == "ALL" else [args.store]
    
    for store in stores:
        profiles = ADS_PROFILES[store]
        if args.market:
            profiles = {args.market: profiles[args.market]}
        
        engine = AdsReportEngine(
            os.environ[profiles[list(profiles.keys())[0]]["ads_client"]],
            os.environ[profiles[list(profiles.keys())[0]]["ads_secret"]],
            os.environ[profiles[list(profiles.keys())[0]]["ads_refresh"]],
        )
        
        session = get_db() if not args.collect_only else None
        data_dir = BASE_DIR / "data_lake" / "ads" / store
        data_dir.mkdir(parents=True, exist_ok=True)
        
        for mkt, cfg in profiles.items():
            pid = cfg["profile_id"]
            print(f"\n{'='*50}")
            print(f"  {store} / {mkt} — Pulling {args.months} months")
            print(f"{'='*50}")
            
            for i in range(args.months):
                d = datetime.now() - relativedelta(months=i)
                y, m = d.year, d.month
                
                try:
                    # Campaign data
                    rows = engine.pull_campaign_data(pid, y, m)
                    total_cost = sum(float(r.get("cost", 0)) for r in rows)
                    total_sales = sum(float(r.get("sales30d", 0)) for r in rows)
                    
                    # Save locally
                    fn = data_dir / f"{mkt}_{y}-{m:02d}_campaigns.json"
                    with open(fn, "w") as f:
                        json.dump({"meta": {"store": store, "market": mkt, "year": y, "month": m,
                                            "total_cost": total_cost, "total_sales": total_sales},
                                   "rows": rows}, f, indent=2)
                    
                    acos = f"{total_cost/total_sales*100:.0f}%" if total_sales > 0 else "N/A"
                    print(f"  {y}-{m:02d}: ${total_cost:,.0f} spend | ${total_sales:,.0f} sales | ACOS {acos}")
                    
                    # Sync to DB
                    if session:
                        sync_to_db(session, store, f"AMAZON_{mkt}", rows, pid)
                    
                    time.sleep(2)  # Rate limit
                    
                except Exception as e:
                    print(f"  {y}-{m:02d}: ❌ {e}")
    
    if session:
        session.close()
    
    print(f"\n✅ Done. Data saved to data_lake/ads/")

if __name__ == "__main__":
    main()
