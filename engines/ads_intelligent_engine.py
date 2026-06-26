#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║        AMAZON ADS INTELLIGENT ENGINE (AAIE) v1.0            ║
║  Unified ads intelligence for CUCZUUS · BOOLUU · Heliumx    ║
╚══════════════════════════════════════════════════════════════╝

Capabilities:
  1. Data Pipeline    — Pull, poll, sync Ads API reports → PostgreSQL + data lake
  2. Campaign Intel   — ACoS / RoAS / CTR / CVR analysis + health scoring
  3. Keyword Intel    — Keyword scoring, negative kw detection, discovery suggestions
  4. TACoS Tracking   — Blended ad cost vs total revenue (organic + paid)
  5. Anomaly Detection — Spend spikes, conversion drops, sudden ACoS shifts
  6. Budget Optimizer — Cross-campaign budget allocation recommendations
  7. Cross-Store Intel — Benchmark CUCZUUS vs BOOLUU vs Heliumx
  8. Daily Digest     — Automated summary + top recommendations

Principles:
  - Recommendation-only. NEVER auto-adjusts bids.
  - All bid changes require human (Level 2) approval.
  - Data-first: every recommendation cites actual numbers.

Usage:
  python3 engines/ads_intelligent_engine.py sync --store CUCZUUS --months 6
  python3 engines/ads_intelligent_engine.py analyze --store ALL --days 30
  python3 engines/ads_intelligent_engine.py keywords --store CUCZUUS --campaign-id 123
  python3 engines/ads_intelligent_engine.py digest --store ALL --days 7
  python3 engines/ads_intelligent_engine.py anomalies --store ALL --days 14
"""

import argparse, httpx, os, sys, json, gzip, time, logging
from pathlib import Path
from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple, Union
from collections import defaultdict
from enum import Enum

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from sqlalchemy import func, and_, or_, text

# ── Paths ────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data_lake" / "ads"
DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ads_intelligent")

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

class StoreID(Enum):
    CUCZUUS = "CUCZUUS"
    BOOLUU = "BOOLUU"
    HELIUMX = "Heliumx"

# Store → env var prefix
STORE_ENV_PREFIX = {
    "CUCZUUS": "STORE_02",
    "BOOLUU": "STORE_03",
    "Heliumx": "STORE_04",
}

# Ads profile registry per store per market
ADS_PROFILES: Dict[str, Dict[str, dict]] = {
    "CUCZUUS": {
        "US": {"profile_id": "3465892858543553"},
        "CA": {"profile_id": "3474360124295876"},
        "BR": {"profile_id": "4006392837291348"},
        "MX": {"profile_id": "1967957568965418"},
    },
    "BOOLUU": {
        "US": {"profile_id": "2902488181372396"},
        "CA": {"profile_id": "1000058889542591"},
        "MX": {"profile_id": "3078520327811488"},
        "BR": {"profile_id": "3825787828028947"},
        "JP": {"profile_id": "3307849422376241"},
    },
    "Heliumx": {
        "US": {"profile_id": "3867175376672823"},
    },
}

# Marketplace ID mapping (Amazon standard)
MARKETPLACE_IDS = {
    "US": "ATVPDKIKX0DER",
    "CA": "A2EUQ1WTGCTBG2",
    "MX": "A1AM78C64UM0Y8",
    "BR": "A2Q3Y263D00KWC",
    "JP": "A1VC38T7YXB528",
}

# ═══════════════════════════════════════════════════════════════
# DATA TYPES
# ═══════════════════════════════════════════════════════════════

class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class RecType(Enum):
    ACOS_ALERT = "acos_alert"
    SCALE_OPPORTUNITY = "scale_opportunity"
    NEGATIVE_KEYWORD = "negative_keyword"
    BID_ADJUST = "bid_adjust"
    BUDGET_REALLOCATE = "budget_reallocate"
    PAUSE_CAMPAIGN = "pause_campaign"
    ANOMALY = "anomaly"
    NO_ACTION = "no_action"

@dataclass
class CampaignSnapshot:
    """Single campaign's metrics for a time window."""
    store_id: str
    marketplace: str
    campaign_id: str
    campaign_name: str
    impressions: int = 0
    clicks: int = 0
    spend: float = 0.0
    sales: float = 0.0
    orders: int = 0
    acos: float = 0.0
    roas: float = 0.0
    ctr: float = 0.0
    cvr: float = 0.0
    cpc: float = 0.0
    health_score: float = 0.0  # 0-100 composite score

@dataclass
class KeywordScore:
    """Scored keyword with performance metrics."""
    keyword: str
    campaign_id: str
    match_type: str
    impressions: int = 0
    clicks: int = 0
    spend: float = 0.0
    sales: float = 0.0
    orders: int = 0
    acos: float = 0.0
    roas: float = 0.0
    ctr: float = 0.0
    cvr: float = 0.0
    score: float = 0.0  # composite performance score
    is_negative_candidate: bool = False
    action: str = ""  # "keep", "reduce_bid", "negative_exact", "negative_phrase", "scale"

@dataclass
class Anomaly:
    """Detected anomaly in campaign performance."""
    store_id: str
    campaign_id: str
    campaign_name: str
    metric: str  # "spend", "acos", "sales", "conversion"
    current_value: float
    expected_value: float
    deviation_pct: float
    severity: RiskLevel
    description: str

@dataclass
class Recommendation:
    """Actionable recommendation."""
    store_id: str
    campaign_id: str
    campaign_name: str
    rec_type: RecType
    message: str
    confidence: float  # 0-1
    risk_level: RiskLevel
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "store_id": self.store_id,
            "campaign_id": self.campaign_id,
            "campaign_name": self.campaign_name,
            "type": self.rec_type.value,
            "message": self.message,
            "confidence": round(self.confidence, 3),
            "risk_level": self.risk_level.value,
            "metrics": self.metrics,
        }

@dataclass
class StoreDigest:
    """Daily/weekly digest for a single store."""
    store_id: str
    period_days: int
    total_spend: float = 0.0
    total_sales: float = 0.0
    total_orders: int = 0
    acos: float = 0.0
    roas: float = 0.0
    tacos: float = 0.0
    campaign_count: int = 0
    active_campaigns: int = 0
    recommendations: List[Recommendation] = field(default_factory=list)
    anomalies: List[Anomaly] = field(default_factory=list)
    top_keywords: List[KeywordScore] = field(default_factory=list)
    worst_keywords: List[KeywordScore] = field(default_factory=list)

# ═══════════════════════════════════════════════════════════════
# CONFIG LOADER
# ═══════════════════════════════════════════════════════════════

def load_env():
    """Load .env into os.environ."""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        logger.warning("No .env file found at %s", env_file)
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()
    logger.info("Loaded environment from .env")

def get_store_creds(store: str) -> Tuple[str, str, str]:
    """Get (client_id, client_secret, refresh_token) for a store."""
    prefix = STORE_ENV_PREFIX[store]
    cid = os.environ.get(f"{prefix}_ADS_CLIENT_ID", "")
    csec = os.environ.get(f"{prefix}_ADS_CLIENT_SECRET", "")
    ref = os.environ.get(f"{prefix}_ADS_REFRESH_TOKEN", "")
    return cid, csec, ref

def get_db():
    """Create a DB session."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set in environment")
    engine = sa.create_engine(db_url, pool_size=5, max_overflow=10,
                              pool_pre_ping=True, pool_recycle=3600)
    return sessionmaker(bind=engine)()

# ═══════════════════════════════════════════════════════════════
# DATA PIPELINE — Ads API Client
# ═══════════════════════════════════════════════════════════════

class AdsAPIClient:
    """Amazon Ads API client with token management and rate limiting."""

    BASE_URL = "https://advertising-api.amazon.com"
    AUTH_URL = "https://api.amazon.com/auth/o2/token"

    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self._access_token: Optional[str] = None
        self._token_expiry: float = 0
        self._last_request: float = 0
        self._min_interval: float = 1.0  # Rate limit: 1 req/sec

    def _rate_limit(self):
        """Ensure minimum interval between requests."""
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    def _get_token(self) -> str:
        """Get or refresh access token."""
        if self._access_token and time.time() < self._token_expiry - 120:
            return self._access_token

        logger.info("Refreshing Ads API access token...")
        r = httpx.post(self.AUTH_URL, json={
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
        }, timeout=15)
        r.raise_for_status()
        data = r.json()
        self._access_token = data["access_token"]
        self._token_expiry = time.time() + data.get("expires_in", 3600)
        return self._access_token

    def _headers(self, profile_id: str) -> dict:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Amazon-Advertising-API-ClientId": self.client_id,
            "Amazon-Advertising-API-Scope": profile_id,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, profile_id: str,
                 json_body: dict = None, timeout: int = 30) -> dict:
        """Make an Ads API request with rate limiting."""
        self._rate_limit()
        url = f"{self.BASE_URL}{path}"
        h = self._headers(profile_id)
        r = httpx.request(method, url, headers=h, json=json_body, timeout=timeout)
        self._last_request = time.time()
        if r.status_code == 429:
            retry_after = int(r.headers.get("Retry-After", 10))
            logger.warning("Rate limited. Waiting %ds...", retry_after)
            time.sleep(retry_after)
            return self._request(method, path, profile_id, json_body, timeout)
        if r.status_code == 425:
            retry_after = int(r.headers.get("Retry-After", 5))
            logger.warning("425 Too Early. Waiting %ds...", retry_after)
            time.sleep(retry_after)
            return self._request(method, path, profile_id, json_body, timeout)
        if r.status_code >= 400:
            logger.error("API error %d: %s", r.status_code, r.text[:500])
        r.raise_for_status()
        return r.json()

    def create_report(self, profile_id: str, start_date: str, end_date: str,
                      report_type: str = "spCampaigns", group_by: str = "campaign",
                      columns: List[str] = None) -> str:
        """Create an async report. Returns reportId."""
        if columns is None:
            columns = ["campaignName", "campaignId", "cost", "attributedSalesSameSku30d",
                       "clicks", "impressions", "purchasesSameSku30d"]

        config = {
            "adProduct": "SPONSORED_PRODUCTS",
            "groupBy": [group_by],
            "columns": columns,
            "reportTypeId": report_type,
            "timeUnit": "SUMMARY",
            "format": "GZIP_JSON",
        }

        body = {"startDate": start_date, "endDate": end_date, "configuration": config}
        result = self._request("POST", "/reporting/reports", profile_id, body)
        return result["reportId"]

    def poll_report(self, profile_id: str, report_id: str,
                    max_wait: int = 300) -> List[dict]:
        """Poll until COMPLETED, return parsed rows."""
        deadline = time.time() + max_wait
        while time.time() < deadline:
            time.sleep(5)
            d = self._request("GET", f"/reporting/reports/{report_id}", profile_id)
            status = d.get("status")
            if status == "COMPLETED":
                raw = httpx.get(d["url"], timeout=60).content
                data = gzip.decompress(raw).decode()
                return [json.loads(line) for line in data.strip().split("\n") if line]
            elif status in ("FAILURE", "CANCELLED"):
                raise RuntimeError(f"Report {status}: {d}")
            logger.debug("Report %s: %s", report_id, status)
        raise TimeoutError(f"Report {report_id} timed out after {max_wait}s")

    def pull_campaign_data(self, profile_id: str, year: int, month: int) -> List[dict]:
        """Pull one month of Sponsored Products campaign data."""
        start = f"{year}-{month:02d}-01"
        end_dt = datetime(year, month, 1) + relativedelta(months=1, days=-1)
        max_end = datetime.now() + timedelta(days=1)
        if end_dt > max_end:
            end_dt = max_end
        end = end_dt.strftime("%Y-%m-%d")

        rid = self.create_report(profile_id, start, end,
                                  "spCampaigns", "campaign",
                                  ["campaignName", "campaignId", "campaignBudgetAmount",
                                   "cost", "attributedSalesSameSku30d", "clicks",
                                   "impressions", "purchasesSameSku30d"])
        return self.poll_report(profile_id, rid)

    def pull_keyword_data(self, profile_id: str, year: int, month: int) -> List[dict]:
        """Pull one month of search term performance data."""
        start = f"{year}-{month:02d}-01"
        end_dt = datetime(year, month, 1) + relativedelta(months=1, days=-1)
        max_end = datetime.now() + timedelta(days=1)
        if end_dt > max_end:
            end_dt = max_end
        end = end_dt.strftime("%Y-%m-%d")

        rid = self.create_report(profile_id, start, end,
                                  "spSearchTerm", "searchTerm",
                                  ["campaignName", "campaignId", "keyword",
                                   "matchType", "cost", "attributedSalesSameSku30d",
                                   "clicks", "impressions", "purchasesSameSku30d"])
        return self.poll_report(profile_id, rid)

    def pull_targeting_data(self, profile_id: str, year: int, month: int) -> List[dict]:
        """Pull targeting (keyword/ASIN) performance."""
        start = f"{year}-{month:02d}-01"
        end_dt = datetime(year, month, 1) + relativedelta(months=1, days=-1)
        max_end = datetime.now() + timedelta(days=1)
        if end_dt > max_end:
            end_dt = max_end
        end = end_dt.strftime("%Y-%m-%d")

        rid = self.create_report(profile_id, start, end,
                                  "spTargeting", "targeting",
                                  ["campaignName", "campaignId", "keyword",
                                   "keywordId", "matchType", "cost",
                                   "attributedSalesSameSku30d", "clicks",
                                   "impressions", "purchasesSameSku30d"])
        return self.poll_report(profile_id, rid)

    def list_campaigns(self, profile_id: str) -> List[dict]:
        """List all Sponsored Products campaigns."""
        result = self._request("GET", "/sp/campaigns", profile_id)
        return result if isinstance(result, list) else result.get("campaigns", [])

    def list_ad_groups(self, profile_id: str) -> List[dict]:
        """List all ad groups."""
        result = self._request("GET", "/sp/adGroups", profile_id)
        return result if isinstance(result, list) else result.get("adGroups", [])

    def list_keywords(self, profile_id: str, campaign_id: str = None) -> List[dict]:
        """List targeting keywords."""
        path = "/sp/keywords"
        if campaign_id:
            path += f"?campaignIdFilter={campaign_id}"
        result = self._request("GET", path, profile_id)
        return result if isinstance(result, list) else result.get("keywords", [])

# ═══════════════════════════════════════════════════════════════
# DATA LAKE — Local JSON Storage
# ═══════════════════════════════════════════════════════════════

class DataLake:
    """Local JSON storage for ads data. Complements PostgreSQL."""

    def __init__(self, base_dir: Path = DATA_DIR):
        self.base = base_dir

    def _store_dir(self, store: str, market: str) -> Path:
        d = self.base / store.lower() / market.lower()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save(self, store: str, market: str, data_type: str,
             year: int, month: int, data: dict):
        """Save data to data lake."""
        d = self._store_dir(store, market)
        fn = d / f"{year}-{month:02d}_{data_type}.json"
        with open(fn, "w") as f:
            json.dump(data, f, indent=2, default=str)
        logger.info("Saved: %s", fn)

    def load(self, store: str, market: str, data_type: str,
             year: int, month: int) -> Optional[dict]:
        """Load data from data lake."""
        d = self._store_dir(store, market)
        fn = d / f"{year}-{month:02d}_{data_type}.json"
        if fn.exists():
            with open(fn) as f:
                return json.load(f)
        return None

    def list_files(self, store: str, market: str = None) -> List[Path]:
        """List all stored files for a store."""
        base = self.base / store.lower()
        if market:
            base = base / market.lower()
        if base.exists():
            return sorted(base.rglob("*.json"))
        return []

# ═══════════════════════════════════════════════════════════════
# DATABASE SYNC
# ═══════════════════════════════════════════════════════════════

class DatabaseSyncer:
    """Syncs Ads API data to PostgreSQL."""

    def __init__(self, session):
        self.session = session

    def upsert_performance(self, store_id: str, marketplace_id: str,
                           rows: List[dict], data_type: str = "campaign") -> int:
        """Upsert ad performance rows. Returns count."""

        # Import model dynamically to avoid circular imports
        from db.models import AdPerformance

        count = 0
        today = date.today()

        for row in rows:
            try:
                if data_type == "campaign":
                    campaign_id = str(row.get("campaignId", ""))
                    keyword = None
                    match_type_val = "campaign"
                    ad_group_id = None
                    sku = None
                    asin = None
                elif data_type == "keyword":
                    campaign_id = str(row.get("campaignId", ""))
                    keyword = row.get("keywordText", "")
                    match_type_val = row.get("matchType", "broad")
                    ad_group_id = None
                    sku = None
                    asin = None
                elif data_type == "targeting":
                    campaign_id = str(row.get("campaignId", ""))
                    keyword = row.get("keywordText", "")
                    match_type_val = row.get("matchType", "targeting")
                    ad_group_id = None
                    sku = None
                    asin = None
                else:
                    campaign_id = str(row.get("campaignId", ""))
                    keyword = None
                    match_type_val = data_type
                    ad_group_id = None
                    sku = None
                    asin = None

                cost = float(row.get("cost", 0))
                sales = float(row.get("attributedSalesSameSku30d", 0) or row.get("attributedSales30d", 0) or row.get("sales30d", 0))
                clicks = int(row.get("clicks", 0))
                impressions = int(row.get("impressions", 0))
                orders = int(row.get("purchasesSameSku30d", 0) or row.get("attributedPurchases30d", 0) or row.get("purchases30d", 0))

                # Check for existing record
                existing = self.session.query(AdPerformance).filter_by(
                    store_id=store_id, marketplace_id=marketplace_id,
                    campaign_id=campaign_id, keyword=keyword, date=today
                ).first()

                if existing:
                    existing.spend = cost
                    existing.sales = sales
                    existing.clicks = clicks
                    existing.impressions = impressions
                    existing.orders = orders
                    existing.raw_data = row
                else:
                    self.session.add(AdPerformance(
                        store_id=store_id, marketplace_id=marketplace_id,
                        campaign_id=campaign_id, ad_group_id=ad_group_id,
                        sku=sku, asin=asin, keyword=keyword,
                        match_type=match_type_val, date=today,
                        impressions=impressions, clicks=clicks,
                        spend=cost, sales=sales, orders=orders,
                        raw_data=row,
                    ))
                count += 1
            except Exception as e:
                logger.error("Failed to upsert row: %s", e)

        self.session.commit()
        logger.info("Synced %d rows to DB", count)
        return count

# ═══════════════════════════════════════════════════════════════
# CAMPAIGN INTELLIGENCE ENGINE
# ═══════════════════════════════════════════════════════════════

class CampaignIntelligence:
    """
    Analyzes campaign performance with health scoring and recommendations.

    Health Score (0-100):
      - ACoS < 10%:  +40 points
      - ACoS 10-20%: +25 points
      - ACoS 20-30%: +10 points
      - ACoS > 30%:  +0 points
      - RoAS > 5x:   +20 points
      - RoAS 2-5x:   +10 points
      - CTR > 0.5%:  +15 points
      - CVR > 10%:   +15 points
      - Active spend: +10 points (penalizes dead campaigns)
    """

    HIGH_ACOS = 0.25
    CRITICAL_ACOS = 0.40
    LOW_ACOS = 0.10
    MIN_SPEND_FOR_ALERT = 50.0

    def __init__(self, db_session):
        self.db = db_session
        from db.models import AdPerformance, ProfitDailySnapshot
        self.AdPerformance = AdPerformance
        self.ProfitDailySnapshot = ProfitDailySnapshot

    def get_campaigns(self, store_id: str, days: int = 30) -> List[CampaignSnapshot]:
        """Fetch all campaigns with aggregated metrics."""
        start = date.today() - timedelta(days=days)

        rows = self.db.query(
            self.AdPerformance.store_id,
            self.AdPerformance.marketplace_id,
            self.AdPerformance.campaign_id,
            func.max(self.AdPerformance.keyword).label("campaign_name"),
            func.sum(self.AdPerformance.impressions).label("impressions"),
            func.sum(self.AdPerformance.clicks).label("clicks"),
            func.sum(self.AdPerformance.spend).label("spend"),
            func.sum(self.AdPerformance.sales).label("sales"),
            func.sum(self.AdPerformance.orders).label("orders"),
        ).filter(
            self.AdPerformance.store_id == store_id,
            self.AdPerformance.date >= start,
        ).group_by(
            self.AdPerformance.store_id,
            self.AdPerformance.marketplace_id,
            self.AdPerformance.campaign_id,
        ).all()

        # Also get campaign names from raw_data if keyword field is None
        campaigns = []
        for r in rows:
            cid = r.campaign_id
            # Try to get campaign name from the most recent raw_data
            name_row = self.db.query(self.AdPerformance.raw_data).filter(
                self.AdPerformance.campaign_id == cid,
                self.AdPerformance.store_id == store_id,
                self.AdPerformance.date >= start,
            ).order_by(self.AdPerformance.date.desc()).first()

            name = r.campaign_name or ""
            if name_row and name_row.raw_data:
                name = name_row.raw_data.get("campaignName", name)

            impressions = int(r.impressions or 0)
            clicks = int(r.clicks or 0)
            spend = float(r.spend or 0)
            sales = float(r.sales or 0)
            orders = int(r.orders or 0)

            acos = spend / sales if sales > 0 else (float("inf") if spend > 0 else 0)
            roas = sales / spend if spend > 0 else 0
            ctr = clicks / impressions if impressions > 0 else 0
            cvr = orders / clicks if clicks > 0 else 0
            cpc = spend / clicks if clicks > 0 else 0

            # Health score
            health = 0
            if acos <= 0.10: health += 40
            elif acos <= 0.20: health += 25
            elif acos <= 0.30: health += 10
            if roas > 5: health += 20
            elif roas >= 2: health += 10
            if ctr > 0.005: health += 15
            if cvr > 0.10: health += 15
            if spend > 0: health += 10
            health = min(100, health)

            campaigns.append(CampaignSnapshot(
                store_id=store_id,
                marketplace=r.marketplace_id or "US",
                campaign_id=cid,
                campaign_name=name,
                impressions=impressions,
                clicks=clicks,
                spend=round(spend, 2),
                sales=round(sales, 2),
                orders=orders,
                acos=round(acos, 4),
                roas=round(roas, 2),
                ctr=round(ctr, 4),
                cvr=round(cvr, 4),
                cpc=round(cpc, 2),
                health_score=health,
            ))

        return sorted(campaigns, key=lambda c: c.health_score)

    def analyze(self, store_id: str, days: int = 30) -> List[Recommendation]:
        """Generate recommendations for all campaigns."""
        campaigns = self.get_campaigns(store_id, days)
        recs = []

        for c in campaigns:
            recs.extend(self._analyze_campaign(c, days))

        return sorted(recs, key=lambda r: r.confidence, reverse=True)

    def _analyze_campaign(self, c: CampaignSnapshot, days: int) -> List[Recommendation]:
        """Generate recommendations for a single campaign."""
        recs = []

        # Critical ACoS
        if c.acos > self.CRITICAL_ACOS and c.spend > self.MIN_SPEND_FOR_ALERT:
            recs.append(Recommendation(
                c.store_id, c.campaign_id, c.campaign_name,
                RecType.ACOS_ALERT,
                f"🚨 CRITICAL: ACoS {c.acos:.1%} with ${c.spend:.0f} spend over {days} days. "
                f"Only {c.orders} orders generated. Consider pausing or major bid reduction.",
                0.90, RiskLevel.CRITICAL,
                {"acos": c.acos, "spend": c.spend, "sales": c.sales, "orders": c.orders}
            ))
        elif c.acos > self.HIGH_ACOS and c.spend > self.MIN_SPEND_FOR_ALERT:
            risk = RiskLevel.HIGH if c.acos > 0.35 else RiskLevel.MEDIUM
            recs.append(Recommendation(
                c.store_id, c.campaign_id, c.campaign_name,
                RecType.BID_ADJUST,
                f"⚠️ High ACoS {c.acos:.1%} — ${c.spend:.0f} spend, ${c.sales:.0f} sales. "
                f"Consider reducing bids by 15-25% and adding negative keywords.",
                0.70, risk,
                {"acos": c.acos, "spend": c.spend, "sales": c.sales}
            ))

        # Low ACoS scaling opportunity
        if c.acos < self.LOW_ACOS and c.impressions > 1000 and c.spend > 10:
            recs.append(Recommendation(
                c.store_id, c.campaign_id, c.campaign_name,
                RecType.SCALE_OPPORTUNITY,
                f"📈 Scale opportunity: ACoS only {c.acos:.1%}, RoAS {c.roas:.1f}x, "
                f"CTR {c.ctr:.2%}. Consider increasing bids by 10-20% and budget.",
                0.65, RiskLevel.LOW,
                {"acos": c.acos, "roas": c.roas, "ctr": c.ctr, "impressions": c.impressions}
            ))

        # Zero sales, high spend
        if c.sales == 0 and c.spend > self.MIN_SPEND_FOR_ALERT:
            recs.append(Recommendation(
                c.store_id, c.campaign_id, c.campaign_name,
                RecType.PAUSE_CAMPAIGN,
                f"🛑 Zero-attribution: ${c.spend:.0f} spent with 0 sales in {days} days. "
                f"Review immediately — may need pausing or complete restructure.",
                0.85, RiskLevel.HIGH,
                {"spend": c.spend, "clicks": c.clicks, "impressions": c.impressions}
            ))

        # Low CTR
        if c.ctr < 0.001 and c.impressions > 5000:
            recs.append(Recommendation(
                c.store_id, c.campaign_id, c.campaign_name,
                RecType.BID_ADJUST,
                f"📉 Very low CTR {c.ctr:.2%} with {c.impressions:,} impressions. "
                f"Ad creative or targeting may need revision.",
                0.55, RiskLevel.MEDIUM,
                {"ctr": c.ctr, "impressions": c.impressions}
            ))

        return recs

    def calculate_tacos(self, store_id: str, days: int = 30) -> Dict:
        """TACoS = Total Ad Spend / Total Revenue (organic + paid)."""
        start = date.today() - timedelta(days=days)

        ad_spend = self.db.query(func.sum(self.AdPerformance.spend)).filter(
            self.AdPerformance.store_id == store_id,
            self.AdPerformance.date >= start,
        ).scalar() or 0

        total_rev = self.db.query(func.sum(self.ProfitDailySnapshot.revenue)).filter(
            self.ProfitDailySnapshot.store_id == store_id,
            self.ProfitDailySnapshot.date >= start,
        ).scalar() or 0

        tacos = float(ad_spend) / float(total_rev) if total_rev > 0 else 0

        return {
            "store_id": store_id,
            "days": days,
            "total_ad_spend": round(float(ad_spend), 2),
            "total_revenue": round(float(total_rev), 2),
            "TACoS": round(tacos, 4),
            "TACoS_pct": f"{tacos*100:.1f}%",
            "alert": tacos > 0.25,
            "benchmark": "healthy" if tacos < 0.15 else ("watch" if tacos < 0.25 else "over-investing"),
        }

# ═══════════════════════════════════════════════════════════════
# KEYWORD INTELLIGENCE ENGINE
# ═══════════════════════════════════════════════════════════════

class KeywordIntelligence:
    """
    Scores keywords, identifies negative candidates, and suggests new keywords.
    """

    NEGATIVE_THRESHOLD_CLICKS = 15    # Min clicks to flag as negative
    NEGATIVE_THRESHOLD_ACOS = 0.50    # ACoS above this → negative candidate
    DISCOVERY_MIN_IMPRESSIONS = 100   # Min impressions to score for discovery

    def __init__(self, db_session):
        self.db = db_session
        from db.models import AdPerformance
        self.AdPerformance = AdPerformance

    def fetch_keywords(self, store_id: str, campaign_id: str = None,
                       days: int = 30) -> List[KeywordScore]:
        """Fetch and score all keywords."""
        start = date.today() - timedelta(days=days)

        filters = [
            self.AdPerformance.store_id == store_id,
            self.AdPerformance.date >= start,
            self.AdPerformance.keyword.isnot(None),
            self.AdPerformance.keyword != "",
        ]
        if campaign_id:
            filters.append(self.AdPerformance.campaign_id == campaign_id)

        rows = self.db.query(
            self.AdPerformance.campaign_id,
            self.AdPerformance.keyword,
            self.AdPerformance.match_type,
            func.sum(self.AdPerformance.impressions),
            func.sum(self.AdPerformance.clicks),
            func.sum(self.AdPerformance.spend),
            func.sum(self.AdPerformance.sales),
            func.sum(self.AdPerformance.orders),
        ).filter(*filters).group_by(
            self.AdPerformance.campaign_id,
            self.AdPerformance.keyword,
            self.AdPerformance.match_type,
        ).all()

        scored = []
        for cid, kw, mt, imp, clk, sp, sl, ords in rows:
            imp_v = int(imp or 0)
            clk_v = int(clk or 0)
            sp_v = float(sp or 0)
            sl_v = float(sl or 0)
            ords_v = int(ords or 0)

            acos = sp_v / sl_v if sl_v > 0 else (float("inf") if sp_v > 0 else 0)
            roas = sl_v / sp_v if sp_v > 0 else 0
            ctr = clk_v / imp_v if imp_v > 0 else 0
            cvr = ords_v / clk_v if clk_v > 0 else 0

            # Composite score: reward sales efficiency, penalize spend without returns
            efficiency = 1.0 / (acos + 0.01) if acos < 100 else 0
            scale = min(ords_v / 10, 1.0) if ords_v > 0 else 0
            ctr_bonus = min(ctr * 100, 1.0)
            score = round((efficiency * 0.5 + scale * 0.3 + ctr_bonus * 0.2), 2)

            is_negative = (
                clk_v >= self.NEGATIVE_THRESHOLD_CLICKS
                and sp_v > 5
                and (acos > self.NEGATIVE_THRESHOLD_ACOS or sl_v == 0)
            )

            if sl_v == 0 and sp_v > 10:
                action = "negative_exact"
            elif acos > 0.50:
                action = "reduce_bid" if ords_v > 0 else "negative_phrase"
            elif acos < 0.10 and ords_v > 0:
                action = "scale"
            else:
                action = "keep"

            scored.append(KeywordScore(
                keyword=kw or "(empty)",
                campaign_id=cid,
                match_type=mt or "broad",
                impressions=imp_v, clicks=clk_v,
                spend=round(sp_v, 2), sales=round(sl_v, 2),
                orders=ords_v, acos=round(acos, 4),
                roas=round(roas, 2), ctr=round(ctr, 4),
                cvr=round(cvr, 4), score=score,
                is_negative_candidate=is_negative,
                action=action,
            ))

        return sorted(scored, key=lambda k: k.score, reverse=True)

    def negative_keyword_recommendations(self, store_id: str,
                                          days: int = 30) -> List[Recommendation]:
        """Generate negative keyword recommendations."""
        keywords = self.fetch_keywords(store_id, days=days)
        recs = []

        for kw in keywords:
            if not kw.is_negative_candidate:
                continue

            confidence = min(0.95, kw.spend / 50 * 0.5 + 0.5)
            recs.append(Recommendation(
                store_id, kw.campaign_id, f"KW: {kw.keyword}",
                RecType.NEGATIVE_KEYWORD,
                f"🔇 Negative keyword candidate: '{kw.keyword}' — "
                f"${kw.spend:.0f} spend, {kw.clicks} clicks, 0 sales. "
                f"Add as negative {kw.match_type}.",
                confidence,
                RiskLevel.LOW if kw.spend < 20 else RiskLevel.MEDIUM,
                {"keyword": kw.keyword, "spend": kw.spend, "clicks": kw.clicks,
                 "match_type": kw.match_type}
            ))

        return sorted(recs, key=lambda r: r.confidence, reverse=True)

    def discovery_keywords(self, store_id: str, campaign_id: str = None,
                           days: int = 30) -> List[KeywordScore]:
        """Find high-potential keywords worth scaling."""
        keywords = self.fetch_keywords(store_id, campaign_id, days)
        discoveries = []

        for kw in keywords:
            if (kw.impressions >= self.DISCOVERY_MIN_IMPRESSIONS
                    and kw.ctr > 0.005
                    and kw.cvr > 0.05
                    and kw.acos < 0.15
                    and kw.orders > 0):
                discoveries.append(kw)

        return sorted(discoveries, key=lambda k: k.score, reverse=True)

# ═══════════════════════════════════════════════════════════════
# ANOMALY DETECTION
# ═══════════════════════════════════════════════════════════════

class AnomalyDetector:
    """
    Detects anomalies in campaign performance using rolling averages.
    Compares current period vs historical baseline.
    """

    SPIKE_THRESHOLD = 2.0     # 2x baseline = spike
    DROP_THRESHOLD = 0.3      # 70% drop = anomaly
    ACOS_SHIFT = 0.15         # 15 percentage point ACoS shift

    def __init__(self, db_session):
        self.db = db_session
        from db.models import AdPerformance
        self.AdPerformance = AdPerformance

    def detect(self, store_id: str, lookback_days: int = 14,
               baseline_days: int = 30) -> List[Anomaly]:
        """
        Compare last N days vs prior baseline period.
        Returns list of detected anomalies.
        """
        today = date.today()
        current_start = today - timedelta(days=lookback_days)
        baseline_start = current_start - timedelta(days=baseline_days)
        baseline_end = current_start - timedelta(days=1)

        anomalies = []

        # Get current period campaign data
        current = self._aggregate_campaigns(store_id, current_start, today)
        baseline = self._aggregate_campaigns(store_id, baseline_start, baseline_end)

        for cid, curr in current.items():
            base = baseline.get(cid)
            if not base or base["spend"] == 0:
                continue

            # Spend spike
            if curr["spend"] > base["spend"] * self.SPIKE_THRESHOLD:
                dev = (curr["spend"] - base["spend"]) / base["spend"]
                anomalies.append(Anomaly(
                    store_id, cid, curr.get("name", cid),
                    "spend", round(curr["spend"], 2), round(base["spend"], 2),
                    round(dev * 100, 1),
                    RiskLevel.HIGH if dev > 3 else RiskLevel.MEDIUM,
                    f"Spend spike: ${curr['spend']:.0f} vs baseline ${base['spend']:.0f} "
                    f"(+{dev*100:.0f}%)"
                ))

            # Sales drop
            if base["sales"] > 100 and curr["sales"] < base["sales"] * self.DROP_THRESHOLD:
                dev = (curr["sales"] - base["sales"]) / base["sales"]
                anomalies.append(Anomaly(
                    store_id, cid, curr.get("name", cid),
                    "sales", round(curr["sales"], 2), round(base["sales"], 2),
                    round(dev * 100, 1),
                    RiskLevel.HIGH if dev < -0.7 else RiskLevel.MEDIUM,
                    f"Sales drop: ${curr['sales']:.0f} vs baseline ${base['sales']:.0f} "
                    f"({dev*100:.0f}%)"
                ))

            # ACoS surge
            curr_acos = curr["spend"] / curr["sales"] if curr["sales"] > 0 else float("inf")
            base_acos = base["spend"] / base["sales"] if base["sales"] > 0 else float("inf")
            if base_acos < 1.0 and curr_acos > base_acos + self.ACOS_SHIFT:
                anomalies.append(Anomaly(
                    store_id, cid, curr.get("name", cid),
                    "acos", round(curr_acos, 4), round(base_acos, 4),
                    round((curr_acos - base_acos) * 100, 1),
                    RiskLevel.HIGH if curr_acos > 0.40 else RiskLevel.MEDIUM,
                    f"ACoS surge: {curr_acos:.1%} vs baseline {base_acos:.1%}"
                ))

        return sorted(anomalies, key=lambda a: abs(a.deviation_pct), reverse=True)

    def _aggregate_campaigns(self, store_id: str, start: date, end: date) -> dict:
        """Aggregate campaign data for a time period."""
        rows = self.db.query(
            self.AdPerformance.campaign_id,
            func.sum(self.AdPerformance.spend),
            func.sum(self.AdPerformance.sales),
            func.sum(self.AdPerformance.impressions),
            func.sum(self.AdPerformance.clicks),
            func.sum(self.AdPerformance.orders),
        ).filter(
            self.AdPerformance.store_id == store_id,
            self.AdPerformance.date >= start,
            self.AdPerformance.date <= end,
        ).group_by(self.AdPerformance.campaign_id).all()

        result = {}
        for cid, spend, sales, imp, clk, ords in rows:
            result[cid] = {
                "spend": float(spend or 0),
                "sales": float(sales or 0),
                "impressions": int(imp or 0),
                "clicks": int(clk or 0),
                "orders": int(ords or 0),
                "name": cid,  # Will be enriched if available
            }
        return result

# ═══════════════════════════════════════════════════════════════
# BUDGET OPTIMIZER
# ═══════════════════════════════════════════════════════════════

class BudgetOptimizer:
    """
    Cross-campaign budget allocation recommendations.
    Identifies campaigns that deserve more/less budget based on efficiency.
    """

    def __init__(self, db_session):
        self.db = db_session
        self.campaign_intel = CampaignIntelligence(db_session)

    def optimize(self, store_id: str, days: int = 30) -> List[Recommendation]:
        """
        Rank campaigns by efficiency and suggest budget reallocation.

        Strategy:
        - Move budget from high-ACoS campaigns to low-ACoS performers
        - Cap any single campaign at 30% of total budget
        """
        campaigns = self.campaign_intel.get_campaigns(store_id, days)
        recs = []

        # Active campaigns with spend
        active = [c for c in campaigns if c.spend > 10]
        if len(active) < 2:
            return recs

        total_spend = sum(c.spend for c in active)
        total_sales = sum(c.sales for c in active)

        # Sort by efficiency (RoAS descending)
        ranked = sorted(active, key=lambda c: c.roas, reverse=True)

        # Top performer gets more budget
        best = ranked[0]
        if best.roas > 3 and best.acos < 0.15:
            suggested_increase = min(best.spend * 0.20, total_spend * 0.05)
            recs.append(Recommendation(
                store_id, best.campaign_id, best.campaign_name,
                RecType.BUDGET_REALLOCATE,
                f"💰 Top performer: RoAS {best.roas:.1f}x, ACoS {best.acos:.1%}. "
                f"Increase budget by ~${suggested_increase:.0f} (+20%).",
                0.75, RiskLevel.LOW,
                {"current_spend": best.spend, "suggested_increase": suggested_increase,
                 "roas": best.roas, "acos": best.acos}
            ))

        # Worst performer gets budget cut
        worst = ranked[-1] if len(ranked) > 1 else None
        if worst and worst.acos > 0.30 and worst.spend > 100:
            suggested_cut = min(worst.spend * 0.30, total_spend * 0.03)
            recs.append(Recommendation(
                store_id, worst.campaign_id, worst.campaign_name,
                RecType.BUDGET_REALLOCATE,
                f"📉 Reallocate from: ACoS {worst.acos:.1%}, RoAS {worst.roas:.1f}x. "
                f"Reduce budget by ~${suggested_cut:.0f}.",
                0.60, RiskLevel.MEDIUM,
                {"current_spend": worst.spend, "suggested_cut": suggested_cut,
                 "acos": worst.acos}
            ))

        # Concentration risk: one campaign > 30% of total
        for c in active:
            share = c.spend / total_spend if total_spend > 0 else 0
            if share > 0.30 and c.acos > 0.20:
                recs.append(Recommendation(
                    store_id, c.campaign_id, c.campaign_name,
                    RecType.BUDGET_REALLOCATE,
                    f"⚠️ Concentration risk: {c.campaign_name} takes {share:.0%} of total "
                    f"ad budget but ACoS is {c.acos:.1%}. Diversify spend.",
                    0.55, RiskLevel.MEDIUM,
                    {"budget_share": share, "acos": c.acos}
                ))

        return recs

# ═══════════════════════════════════════════════════════════════
# CROSS-STORE INTELLIGENCE
# ═══════════════════════════════════════════════════════════════

class CrossStoreIntelligence:
    """Benchmark campaigns across stores and markets."""

    def __init__(self, db_session):
        self.db = db_session
        self.campaign_intel = CampaignIntelligence(db_session)

    def benchmark(self, store_ids: List[str], days: int = 30) -> Dict:
        """Compare performance across stores."""
        results = {}
        for sid in store_ids:
            campaigns = self.campaign_intel.get_campaigns(sid, days)
            if not campaigns:
                continue

            total_spend = sum(c.spend for c in campaigns)
            total_sales = sum(c.sales for c in campaigns)
            total_orders = sum(c.orders for c in campaigns)
            avg_acos = sum(c.acos for c in campaigns if c.acos < 100) / max(1, len([c for c in campaigns if c.acos < 100]))
            avg_health = sum(c.health_score for c in campaigns) / max(1, len(campaigns))

            results[sid] = {
                "store_id": sid,
                "campaign_count": len(campaigns),
                "active_campaigns": len([c for c in campaigns if c.spend > 10]),
                "total_spend": round(total_spend, 2),
                "total_sales": round(total_sales, 2),
                "total_orders": total_orders,
                "avg_acos": round(avg_acos, 4),
                "avg_health_score": round(avg_health, 1),
                "roas": round(total_sales / total_spend, 2) if total_spend > 0 else 0,
            }

        # Rankings
        if results:
            spend_rank = sorted(results, key=lambda s: results[s]["total_spend"], reverse=True)
            health_rank = sorted(results, key=lambda s: results[s]["avg_health_score"], reverse=True)
            efficiency_rank = sorted(results, key=lambda s: results[s]["roas"], reverse=True)

            for i, sid in enumerate(spend_rank):
                results[sid]["spend_rank"] = i + 1
            for i, sid in enumerate(health_rank):
                results[sid]["health_rank"] = i + 1
            for i, sid in enumerate(efficiency_rank):
                results[sid]["efficiency_rank"] = i + 1

        return results

# ═══════════════════════════════════════════════════════════════
# DAILY DIGEST GENERATOR
# ═══════════════════════════════════════════════════════════════

class DigestGenerator:
    """Generate daily/weekly digest for Feishu delivery."""

    def __init__(self, db_session):
        self.db = db_session
        self.campaign_intel = CampaignIntelligence(db_session)
        self.keyword_intel = KeywordIntelligence(db_session)
        self.anomaly_detector = AnomalyDetector(db_session)
        self.budget_optimizer = BudgetOptimizer(db_session)

    def generate(self, store_ids: List[str], days: int = 7) -> List[StoreDigest]:
        """Generate digest for each store."""
        digests = []

        for sid in store_ids:
            campaigns = self.campaign_intel.get_campaigns(sid, days)

            total_spend = sum(c.spend for c in campaigns)
            total_sales = sum(c.sales for c in campaigns)
            total_orders = sum(c.orders for c in campaigns)

            # TACoS
            tacos_data = self.campaign_intel.calculate_tacos(sid, days)

            # Recommendations
            recs = self.campaign_intel.analyze(sid, days)
            kw_recs = self.keyword_intel.negative_keyword_recommendations(sid, days)
            budget_recs = self.budget_optimizer.optimize(sid, days)

            # Anomalies
            anomalies = self.anomaly_detector.detect(sid, min(14, days), max(30, days * 2))

            # Keywords
            top_kw = self.keyword_intel.fetch_keywords(sid, days=days)[:5]
            worst_kw = self.keyword_intel.fetch_keywords(sid, days=days)[-5:]
            worst_kw.sort(key=lambda k: k.score)

            digests.append(StoreDigest(
                store_id=sid,
                period_days=days,
                total_spend=round(total_spend, 2),
                total_sales=round(total_sales, 2),
                total_orders=total_orders,
                acos=round(total_spend / total_sales, 4) if total_sales > 0 else 0,
                roas=round(total_sales / total_spend, 2) if total_spend > 0 else 0,
                tacos=tacos_data["TACoS"],
                campaign_count=len(campaigns),
                active_campaigns=len([c for c in campaigns if c.spend > 10]),
                recommendations=recs + kw_recs + budget_recs,
                anomalies=anomalies,
                top_keywords=top_kw,
                worst_keywords=worst_kw,
            ))

        return digests

    def format_for_feishu(self, digests: List[StoreDigest]) -> str:
        """Format digests as a Feishu-friendly message."""
        lines = [f"# 📊 Amazon Ads Digest ({digests[0].period_days}d)" if digests else "# 📊 Amazon Ads Digest", ""]

        for d in digests:
            lines.append(f"## {d.store_id}")
            lines.append(f"💰 Spend: **${d.total_spend:,.0f}** | Sales: **${d.total_sales:,.0f}** | Orders: **{d.total_orders}**")
            lines.append(f"📈 ACoS: **{d.acos*100:.1f}%** | RoAS: **{d.roas:.1f}x** | TACoS: **{d.tacos*100:.1f}%**")
            lines.append(f"📋 {d.active_campaigns}/{d.campaign_count} active campaigns")
            lines.append("")

            # Critical alerts first
            critical_recs = [r for r in d.recommendations if r.risk_level in (RiskLevel.CRITICAL, RiskLevel.HIGH)]
            if critical_recs:
                lines.append("### 🚨 Critical Alerts")
                for r in critical_recs[:5]:
                    lines.append(f"- {r.message}")
                lines.append("")

            # Anomalies
            if d.anomalies:
                lines.append("### 🔍 Anomalies Detected")
                for a in d.anomalies[:3]:
                    lines.append(f"- {a.description}")
                lines.append("")

            # Top keywords
            if d.top_keywords:
                lines.append("### ⭐ Top Keywords")
                for k in d.top_keywords[:5]:
                    lines.append(f"- `{k.keyword}` — ${k.sales:.0f} sales, ACoS {k.acos*100:.0f}%, Score {k.score}")
                lines.append("")

            # Worst keywords
            neg_kw = [k for k in d.worst_keywords if k.is_negative_candidate]
            if neg_kw:
                lines.append("### 🗑️ Negative Keyword Candidates")
                for k in neg_kw[:5]:
                    lines.append(f"- `{k.keyword}` — ${k.spend:.0f} wasted, 0 sales")
                lines.append("")

        return "\n".join(lines)

# ═══════════════════════════════════════════════════════════════
# ORCHESTRATOR — Main Engine
# ═══════════════════════════════════════════════════════════════

class AmazonAdsIntelligentEngine:
    """
    Main orchestrator. All sub-engines accessible through one interface.

    Usage:
        engine = AmazonAdsIntelligentEngine()
        engine.sync("CUCZUUS", months=6)
        recs = engine.analyze("CUCZUUS", days=30)
        digest = engine.digest(["CUCZUUS", "BOOLUU"], days=7)
    """

    def __init__(self):
        load_env()
        self.db = get_db()
        self.data_lake = DataLake()
        self.syncer = DatabaseSyncer(self.db)
        self.campaign_intel = CampaignIntelligence(self.db)
        self.keyword_intel = KeywordIntelligence(self.db)
        self.anomaly_detector = AnomalyDetector(self.db)
        self.budget_optimizer = BudgetOptimizer(self.db)
        self.cross_store = CrossStoreIntelligence(self.db)
        self.digest_gen = DigestGenerator(self.db)

    def _get_client(self, store: str) -> AdsAPIClient:
        """Get AdsAPIClient for a store."""
        cid, csec, ref = get_store_creds(store)
        if not all([cid, csec, ref]):
            raise ValueError(f"Missing Ads API credentials for {store}")
        return AdsAPIClient(cid, csec, ref)

    def sync(self, store: str = "CUCZUUS", market: str = None,
             months: int = 6, data_types: List[str] = None):
        """
        Pull Ads API data and sync to PostgreSQL + data lake.

        Args:
            store: Store ID or "ALL"
            market: Market filter (US, CA, etc.) or None for all
            months: Number of months to backfill
            data_types: ["campaign", "keyword", "targeting"] or all
        """
        if data_types is None:
            data_types = ["campaign", "keyword", "targeting"]

        stores = [s.value for s in StoreID] if store == "ALL" else [store]
        if store == "ALL":
            stores = [s for s in stores if s in ADS_PROFILES]

        for s in stores:
            profiles = ADS_PROFILES.get(s, {})
            if market:
                profiles = {market: profiles.get(market, {})}
            if not profiles:
                logger.warning("No profiles for %s", s)
                continue

            client = self._get_client(s)

            for mkt, cfg in profiles.items():
                pid = cfg["profile_id"]
                mkt_id = MARKETPLACE_IDS.get(mkt, "ATVPDKIKX0DER")

                logger.info("=== %s / %s — Syncing %d months ===", s, mkt, months)

                for i in range(months):
                    d = datetime.now() - relativedelta(months=i)
                    y, m = d.year, d.month

                    for dtype in data_types:
                        try:
                            if dtype == "campaign":
                                rows = client.pull_campaign_data(pid, y, m)
                            elif dtype == "keyword":
                                rows = client.pull_keyword_data(pid, y, m)
                            elif dtype == "targeting":
                                rows = client.pull_targeting_data(pid, y, m)
                            else:
                                continue

                            # Save to data lake
                            total_cost = sum(float(r.get("cost", 0)) for r in rows)
                            total_sales = sum(float(r.get("attributedSalesSameSku30d", 0) or r.get("attributedSales30d", 0) or r.get("sales30d", 0)) for r in rows)

                            self.data_lake.save(s, mkt, dtype, y, m, {
                                "meta": {"store": s, "market": mkt, "year": y, "month": m,
                                         "total_cost": total_cost, "total_sales": total_sales,
                                         "row_count": len(rows)},
                                "rows": rows,
                            })

                            # Sync to DB
                            self.syncer.upsert_performance(s, mkt_id, rows, dtype)

                            acos_str = f" ACoS {total_cost/total_sales*100:.0f}%" if total_sales > 0 else ""
                            logger.info("  %s/%s %d-%02d %s: $%s spend, $%s sales%s",
                                       s, mkt, y, m, dtype,
                                       f"{total_cost:,.0f}", f"{total_sales:,.0f}", acos_str)

                            time.sleep(1.5)  # Rate limit

                        except Exception as e:
                            logger.error("  %d-%02d %s: ❌ %s", y, m, dtype, e)

    def analyze(self, store: str = "CUCZUUS", days: int = 30) -> List[Recommendation]:
        """Run full campaign analysis and return recommendations."""
        return self.campaign_intel.analyze(store, days)

    def keywords(self, store: str = "CUCZUUS", campaign_id: str = None,
                 days: int = 30) -> Dict:
        """Keyword analysis with negative candidates and discoveries."""
        all_kw = self.keyword_intel.fetch_keywords(store, campaign_id, days)
        negatives = [kw for kw in all_kw if kw.is_negative_candidate]
        discoveries = self.keyword_intel.discovery_keywords(store, campaign_id, days)

        return {
            "store_id": store,
            "days": days,
            "total_keywords": len(all_kw),
            "negative_candidates": len(negatives),
            "discovery_keywords": len(discoveries),
            "top_keywords": [asdict(kw) for kw in all_kw[:10]],
            "negative_candidates_list": [asdict(kw) for kw in negatives[:20]],
            "discovery_keywords_list": [asdict(kw) for kw in discoveries[:20]],
        }

    def anomalies(self, store: str = "ALL", days: int = 14) -> List[Anomaly]:
        """Detect anomalies across stores."""
        stores = [s.value for s in StoreID] if store == "ALL" else [store]
        all_anomalies = []
        for s in stores:
            all_anomalies.extend(self.anomaly_detector.detect(s, days))
        return sorted(all_anomalies, key=lambda a: abs(a.deviation_pct), reverse=True)

    def tacos(self, store: str = "CUCZUUS", days: int = 30) -> Dict:
        """Calculate TACoS."""
        return self.campaign_intel.calculate_tacos(store, days)

    def benchmark(self, days: int = 30) -> Dict:
        """Cross-store benchmark."""
        stores = [s.value for s in StoreID if s in ADS_PROFILES]
        return self.cross_store.benchmark(stores, days)

    def digest(self, store: str = "ALL", days: int = 7,
               format_for_feishu: bool = False) -> Union[str, List[StoreDigest]]:
        """Generate daily/weekly digest."""
        stores = [s.value for s in StoreID] if store == "ALL" else [store]
        stores = [s for s in stores if s in ADS_PROFILES]
        digests = self.digest_gen.generate(stores, days)

        if format_for_feishu:
            return self.digest_gen.format_for_feishu(digests)
        return digests

    def close(self):
        """Close DB session."""
        self.db.close()

# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Amazon Ads Intelligent Engine (AAIE)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s sync --store CUCZUUS --months 6
  %(prog)s sync --store ALL --months 3 --data-types campaign,keyword
  %(prog)s analyze --store CUCZUUS --days 30
  %(prog)s keywords --store BOOLUU --days 30
  %(prog)s anomalies --store ALL --days 14
  %(prog)s tacos --store CUCZUUS
  %(prog)s benchmark
  %(prog)s digest --store ALL --days 7
  %(prog)s digest --store ALL --days 7 --feishu
        """
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # sync
    p_sync = sub.add_parser("sync", help="Pull Ads API data and sync to DB + data lake")
    p_sync.add_argument("--store", default="CUCZUUS", choices=["CUCZUUS", "BOOLUU", "Heliumx", "ALL"])
    p_sync.add_argument("--market", default=None, help="Filter market (US, CA, MX, BR, JP)")
    p_sync.add_argument("--months", type=int, default=6)
    p_sync.add_argument("--data-types", default="campaign,keyword,targeting",
                         help="Comma-separated: campaign,keyword,targeting")

    # analyze
    p_analyze = sub.add_parser("analyze", help="Campaign analysis with recommendations")
    p_analyze.add_argument("--store", default="CUCZUUS")
    p_analyze.add_argument("--days", type=int, default=30)

    # keywords
    p_kw = sub.add_parser("keywords", help="Keyword intelligence")
    p_kw.add_argument("--store", default="CUCZUUS")
    p_kw.add_argument("--campaign-id", default=None)
    p_kw.add_argument("--days", type=int, default=30)

    # anomalies
    p_anom = sub.add_parser("anomalies", help="Detect performance anomalies")
    p_anom.add_argument("--store", default="ALL")
    p_anom.add_argument("--days", type=int, default=14)

    # tacos
    p_tacos = sub.add_parser("tacos", help="TACoS calculation")
    p_tacos.add_argument("--store", default="CUCZUUS")
    p_tacos.add_argument("--days", type=int, default=30)

    # benchmark
    p_bench = sub.add_parser("benchmark", help="Cross-store benchmark")
    p_bench.add_argument("--days", type=int, default=30)

    # digest
    p_digest = sub.add_parser("digest", help="Daily/weekly digest")
    p_digest.add_argument("--store", default="ALL")
    p_digest.add_argument("--days", type=int, default=7)
    p_digest.add_argument("--feishu", action="store_true", help="Format for Feishu")

    args = parser.parse_args()

    engine = AmazonAdsIntelligentEngine()

    try:
        if args.command == "sync":
            data_types = [dt.strip() for dt in args.data_types.split(",")]
            engine.sync(args.store, args.market, args.months, data_types)

        elif args.command == "analyze":
            recs = engine.analyze(args.store, args.days)
            print(f"\n📊 {args.store} Campaign Analysis ({args.days}d)\n{'='*60}")
            for r in recs:
                emoji = {"critical": "🚨", "high": "⚠️", "medium": "📋", "low": "ℹ️"}.get(r.risk_level.value, "")
                print(f"  {emoji} [{r.rec_type.value}] {r.message} (confidence: {r.confidence:.0%})")

        elif args.command == "keywords":
            result = engine.keywords(args.store, args.campaign_id, args.days)
            print(json.dumps(result, indent=2, default=str))

        elif args.command == "anomalies":
            anoms = engine.anomalies(args.store, args.days)
            print(f"\n🔍 Anomalies ({args.days}d)\n{'='*60}")
            for a in anoms:
                severity_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(a.severity.value, "")
                print(f"  {severity_emoji} [{a.store_id}] {a.description}")

        elif args.command == "tacos":
            result = engine.tacos(args.store, args.days)
            print(json.dumps(result, indent=2))

        elif args.command == "benchmark":
            result = engine.benchmark(args.days)
            print(json.dumps(result, indent=2))

        elif args.command == "digest":
            output = engine.digest(args.store, args.days, format_for_feishu=args.feishu)
            if isinstance(output, str):
                print(output)
            else:
                for d in output:
                    print(f"\n{'='*60}")
                    print(f"  {d.store_id} ({d.period_days}d)")
                    print(f"{'='*60}")
                    print(f"  Spend: ${d.total_spend:,.0f} | Sales: ${d.total_sales:,.0f} | Orders: {d.total_orders}")
                    print(f"  ACoS: {d.acos*100:.1f}% | RoAS: {d.roas:.1f}x | TACoS: {d.tacos*100:.1f}%")
                    print(f"  Campaigns: {d.active_campaigns}/{d.campaign_count} active")
                    print(f"  Recommendations: {len(d.recommendations)} | Anomalies: {len(d.anomalies)}")

    finally:
        engine.close()


if __name__ == "__main__":
    main()
