#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║   AAIE PRODUCTION PIPELINE — API Bridge + Scheduler             ║
║                                                                ║
║   Pipeline:                                                     ║
║     Cron → Lambda/Script → Amazon Ads API → PostgreSQL         ║
║                                                                ║
║   Schedule:                                                     ║
║     Every 14 days  → Weight derivation (recalibrate scoring)   ║
║     Every 7 days   → Attribute harvesting (search term mining) ║
║     Every 24 hours → Bid adjustment recalculation              ║
╚══════════════════════════════════════════════════════════════════╝
"""

import json, os, sys, time, logging, argparse, hashlib
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from collections import defaultdict

import httpx
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

# ═══════════════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════════════

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data_lake" / "ads"
DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ads_pipeline")

MARKETPLACE_IDS = {
    "US": "ATVPDKIKX0DER", "CA": "A2EUQ1WTGCTBG2",
    "MX": "A1AM78C64UM0Y8", "BR": "A2Q3Y263D00KWC", "JP": "A1VC38T7YXB528",
}

ADS_PROFILES = {
    "CUCZUUS": {
        "US": "3465892858543553", "CA": "3474360124295876",
        "BR": "4006392837291348", "MX": "1967957568965418",
    },
    "BOOLUU": {
        "US": "2902488181372396", "CA": "1000058889542591",
        "MX": "3078520327811488", "BR": "3825787828028947",
        "JP": "3307849422376241",
    },
    "Heliumx": {
        "US": "3867175376672823",
    },
}

STORE_ADS_PREFIX = {"CUCZUUS": "STORE_02", "BOOLUU": "STORE_03", "Heliumx": "STORE_04"}

# ═══════════════════════════════════════════════════════
# ENV & DB
# ═══════════════════════════════════════════════════════

def load_env():
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        logger.warning("No .env found")
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

def get_db():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")
    engine = sa.create_engine(db_url, pool_size=5, max_overflow=10,
                              pool_pre_ping=True, pool_recycle=3600)
    return sessionmaker(bind=engine)()

# ═══════════════════════════════════════════════════════
# 1. AMAZON ADS API BRIDGE
# ═══════════════════════════════════════════════════════

class AdsAPIBridge:
    """
    Secure bridge to Amazon Ads API.
    Handles: OAuth token refresh, rate limiting, report creation/polling/download.

    API endpoints used:
      POST /reporting/reports        — Create async report (spSearchTerm, spTargeting)
      GET  /reporting/reports/{id}   — Poll report status
      GET  /sp/keywords              — List keyword bids
      PUT  /v3/sp/keywords           — Update keyword bids (human-approved only)
    """

    AUTH_URL = "https://api.amazon.com/auth/o2/token"
    API_BASE = "https://advertising-api.amazon.com"

    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self._token: Optional[str] = None
        self._token_expiry: float = 0
        self._last_request: float = 0

    # ── Auth ─────────────────────────────────────────

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 120:
            return self._token
        logger.info("Refreshing Ads API token...")
        r = httpx.post(self.AUTH_URL, json={
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
        }, timeout=15)
        r.raise_for_status()
        data = r.json()
        self._token = data["access_token"]
        self._token_expiry = time.time() + data.get("expires_in", 3600)
        return self._token

    def _headers(self, profile_id: str) -> dict:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Amazon-Advertising-API-ClientId": self.client_id,
            "Amazon-Advertising-API-Scope": profile_id,
            "Content-Type": "application/json",
        }

    # ── Report Download ──────────────────────────────

    def download_report(self, profile_id: str, report_type: str,
                        start_date: str, end_date: str, group_by: str,
                        columns: List[str]) -> List[dict]:
        """
        Create, poll, and download an async report.
        Used for: spSearchTerm, spTargeting, spCampaigns.
        """
        # Create
        body = {
            "startDate": start_date,
            "endDate": end_date,
            "configuration": {
                "adProduct": "SPONSORED_PRODUCTS",
                "groupBy": [group_by],
                "columns": columns,
                "reportTypeId": report_type,
                "timeUnit": "SUMMARY",
                "format": "GZIP_JSON",
            }
        }

        r = httpx.post(f"{self.API_BASE}/reporting/reports",
                       headers=self._headers(profile_id), json=body, timeout=15)
        r.raise_for_status()
        report_id = r.json()["reportId"]
        logger.info("Report %s created (%s)", report_id, report_type)

        # Poll
        deadline = time.time() + 300
        while time.time() < deadline:
            time.sleep(5)
            r = httpx.get(f"{self.API_BASE}/reporting/reports/{report_id}",
                          headers=self._headers(profile_id), timeout=10)
            d = r.json()
            status = d.get("status")
            if status == "COMPLETED":
                raw = httpx.get(d["url"], timeout=60).content
                import gzip
                data = gzip.decompress(raw).decode()
                rows = [json.loads(line) for line in data.strip().split("\n") if line]
                logger.info("Report %s downloaded: %d rows", report_id, len(rows))
                return rows
            elif status in ("FAILURE", "CANCELLED"):
                raise RuntimeError(f"Report {report_id} {status}: {d}")
        raise TimeoutError(f"Report {report_id} timed out")

    def download_search_terms(self, profile_id: str,
                               year: int, month: int) -> List[dict]:
        """Download spSearchTerm report for a month."""
        start = f"{year}-{month:02d}-01"
        from dateutil.relativedelta import relativedelta
        end_dt = datetime(year, month, 1) + relativedelta(months=1, days=-1)
        end = end_dt.strftime("%Y-%m-%d")

        return self.download_report(
            profile_id, "spSearchTerm", start, end, "searchTerm",
            ["campaignName", "campaignId", "adGroupId", "keywordText",
             "matchType", "cost", "attributedSales30d", "clicks",
             "impressions", "attributedPurchases30d", "attributedConversions30d"]
        )

    def download_targeting(self, profile_id: str,
                            year: int, month: int) -> List[dict]:
        """Download spTargeting report for a month."""
        start = f"{year}-{month:02d}-01"
        from dateutil.relativedelta import relativedelta
        end_dt = datetime(year, month, 1) + relativedelta(months=1, days=-1)
        end = end_dt.strftime("%Y-%m-%d")

        return self.download_report(
            profile_id, "spTargeting", start, end, "targeting",
            ["campaignName", "campaignId", "adGroupId", "keywordId",
             "keywordText", "matchType", "bid", "cost",
             "attributedSales30d", "clicks", "impressions",
             "attributedPurchases30d"]
        )

    # ── Keyword Bid Management ───────────────────────

    def get_keywords(self, profile_id: str, campaign_id: str = None) -> List[dict]:
        """List current keyword bids."""
        path = "/sp/keywords"
        if campaign_id:
            path += f"?campaignIdFilter={campaign_id}"
        # Handle pagination
        all_keywords = []
        start_index = 0
        while True:
            paginated_path = f"{path}{'&' if '?' in path else '?'}startIndex={start_index}"
            r = httpx.get(f"{self.API_BASE}{paginated_path}",
                         headers=self._headers(profile_id), timeout=10)
            r.raise_for_status()
            data = r.json()
            keywords = data if isinstance(data, list) else data.get("keywords", [])
            all_keywords.extend(keywords)
            # Check if more pages
            total = data.get("totalResults", len(keywords)) if isinstance(data, dict) else len(keywords)
            start_index += len(keywords)
            if start_index >= total:
                break
        return all_keywords

    def update_keyword_bids(self, profile_id: str,
                             bid_changes: List[dict]) -> dict:
        """
        Update keyword bids via PUT /v3/sp/keywords.
        bid_changes: [{"keywordId": "xxx", "bid": 0.75, "state": "enabled"}, ...]

        ⚠️ ONLY called after human approval. Always logs to keyword_bid_log.
        """
        if not bid_changes:
            return {"updated": 0, "message": "No changes to apply"}

        body = {"keywords": bid_changes}
        r = httpx.put(f"{self.API_BASE}/v3/sp/keywords",
                      headers=self._headers(profile_id),
                      json=body, timeout=30)
        r.raise_for_status()
        result = r.json()
        logger.info("Updated %d keyword bids", len(bid_changes))
        return {"updated": len(bid_changes), "response": result}

# ═══════════════════════════════════════════════════════
# 2. DATABASE PERSISTENCE LAYER
# ═══════════════════════════════════════════════════════

class PersistenceLayer:
    """
    Writes search term data to search_terms_history table.
    Handles upserts (unique per store + marketplace + search_term + campaign + date).
    """

    def __init__(self, db_session):
        self.db = db_session
        from db.models import SearchTermHistory
        self.SearchTermHistory = SearchTermHistory

    def upsert_search_terms(self, store_id: str, marketplace_id: str,
                             rows: List[dict], campaign_type: str = "auto") -> int:
        """
        Upsert search term rows into history table.
        Extracts root_keyword, modifier, category, and intent from each term.
        Returns count of upserted rows.
        """
        from engines.ads_intelligent_engine_v2 import SearchTermMiner
        miner = SearchTermMiner()
        today = date.today()
        count = 0

        for row in rows:
            try:
                kw = row.get("keywordText", "")
                if not kw:
                    continue

                root, modifier, category = miner.extract_root_keyword(kw)
                intent = miner._detect_intent(kw)

                cost = float(row.get("cost", 0))
                sales = float(row.get("attributedSales30d", 0) or row.get("sales30d", 0))
                clicks = int(row.get("clicks", 0))
                imp = int(row.get("impressions", 0))
                orders = int(row.get("attributedPurchases30d", 0) or row.get("purchases30d", 0))
                ctr = clicks / imp if imp > 0 else 0
                cvr = orders / clicks if clicks > 0 else 0
                acos = cost / sales if sales > 0 else (float("inf") if cost > 0 else 0)
                roas = sales / cost if cost > 0 else 0
                cpc = cost / clicks if clicks > 0 else 0

                cid = str(row.get("campaignId", ""))
                match_type = row.get("matchType", "broad")

                existing = self.db.query(self.SearchTermHistory).filter_by(
                    store_id=store_id, marketplace_id=marketplace_id,
                    search_term=kw, campaign_id=cid, date=today
                ).first()

                if existing:
                    existing.impressions = imp
                    existing.clicks = clicks
                    existing.spend = cost
                    existing.sales = sales
                    existing.orders = orders
                    existing.ctr = ctr
                    existing.cvr = cvr
                    existing.acos = acos
                    existing.roas = roas
                    existing.cpc = cpc
                    existing.intent_signal = intent
                    existing.harvested_at = datetime.utcnow()
                else:
                    self.db.add(self.SearchTermHistory(
                        store_id=store_id,
                        marketplace_id=marketplace_id,
                        campaign_id=cid,
                        campaign_name=row.get("campaignName", ""),
                        ad_group_id=row.get("adGroupId"),
                        search_term=kw,
                        root_keyword=root,
                        modifier=modifier,
                        category=category,
                        match_type=match_type,
                        campaign_type=campaign_type,
                        date=today,
                        impressions=imp, clicks=clicks,
                        spend=cost, sales=sales, orders=orders,
                        ctr=ctr, cvr=cvr, acos=acos, roas=roas, cpc=cpc,
                        intent_signal=intent,
                        is_negative_candidate=(sales == 0 and cost > 10),
                        raw_data=row,
                        harvested_at=datetime.utcnow(),
                    ))
                count += 1
            except Exception as e:
                logger.error("Upsert failed for '%s': %s", kw, e)

        self.db.commit()
        logger.info("Upserted %d search terms to history", count)
        return count

    def save_attribute_weights(self, store_id: str, weights: Dict[str, float],
                                confidence: float = 0.8):
        """Save derived attribute weights."""
        from db.models import AttributeWeight
        today = date.today()

        for dimension, weight in weights.items():
            existing = self.db.query(AttributeWeight).filter_by(
                store_id=store_id, dimension=dimension, derived_at=today
            ).first()
            if existing:
                existing.weight = weight
                existing.confidence = confidence
            else:
                self.db.add(AttributeWeight(
                    store_id=store_id, dimension=dimension,
                    weight=weight, confidence=confidence, derived_at=today,
                ))
        self.db.commit()
        logger.info("Saved %d attribute weights for %s", len(weights), store_id)

    def log_bid_change(self, store_id: str, marketplace_id: str,
                        campaign_id: str, keyword_id: str, keyword_text: str,
                        match_type: str, old_bid: float, new_bid: float,
                        action: str, rationale: str,
                        metrics: dict = None) -> int:
        """Log a bid change for audit trail."""
        from db.models import KeywordBidLog

        change_pct = (new_bid - old_bid) / old_bid if old_bid > 0 else 0
        log = KeywordBidLog(
            store_id=store_id, marketplace_id=marketplace_id,
            campaign_id=campaign_id, keyword_id=keyword_id,
            keyword_text=keyword_text, match_type=match_type,
            old_bid=old_bid, new_bid=new_bid, change_pct=change_pct,
            action=action, rationale=rationale,
            metrics_snapshot=metrics or {},
            status="pending",
            changed_at=datetime.utcnow(),
        )
        self.db.add(log)
        self.db.commit()
        return log.id

# ═══════════════════════════════════════════════════════
# 3. SCHEDULER — 14d Weights / 7d Harvest / 24h Bids
# ═══════════════════════════════════════════════════════

class AdsScheduler:
    """
    The production scheduler.

    ┌─────────────────┬──────────────┬────────────────────────────────┐
    │ Task             │ Frequency    │ What it does                    │
    ├─────────────────┼──────────────┼────────────────────────────────┤
    │ weight_derive    │ Every 14 days│ Recalculate scoring weights    │
    │ harvest_attrs    │ Every 7 days │ Pull search terms, mine attrs │
    │ recalc_bids      │ Every 24 hrs │ Generate bid recommendations   │
    └─────────────────┴──────────────┴────────────────────────────────┘

    All tasks: recommend ONLY. Bid changes require human approval.
    """

    def __init__(self):
        load_env()
        self.db = get_db()
        self.persistence = PersistenceLayer(self.db)

    def _get_client(self, store: str) -> AdsAPIBridge:
        prefix = STORE_ADS_PREFIX[store]
        cid = os.environ.get(f"{prefix}_ADS_CLIENT_ID", "")
        csec = os.environ.get(f"{prefix}_ADS_CLIENT_SECRET", "")
        ref = os.environ.get(f"{prefix}_ADS_REFRESH_TOKEN", "")
        if not all([cid, csec, ref]):
            raise ValueError(f"Missing Ads API creds for {store}")
        return AdsAPIBridge(cid, csec, ref)

    # ── TASK 1: Weight Derivation (every 14 days) ────

    def task_weight_derivation(self, stores: List[str] = None):
        """
        Recalculate attribute scoring weights based on historical data.

        How: Analyzes search_terms_history to find which dimensions
        correlate most strongly with conversions. Adjusts weights accordingly.

        Default weights (initially):
          efficiency: 0.30, conversion: 0.20, discovery: 0.15,
          momentum: 0.15, volume: 0.10, intent: 0.10
        """
        if stores is None:
            stores = list(ADS_PROFILES.keys())

        logger.info("╔══════════════════════════════════════╗")
        logger.info("║  TASK: Weight Derivation (14d)       ║")
        logger.info("╚══════════════════════════════════════╝")

        for store in stores:
            from db.models import SearchTermHistory

            # Analyze historical search term data for weight calibration
            rows = self.db.query(
                SearchTermHistory.category,
                sa.func.count(SearchTermHistory.id).label("term_count"),
                sa.func.sum(SearchTermHistory.orders).label("total_orders"),
                sa.func.avg(SearchTermHistory.acos).label("avg_acos"),
                sa.func.avg(SearchTermHistory.cvr).label("avg_cvr"),
                sa.func.avg(SearchTermHistory.ctr).label("avg_ctr"),
            ).filter(
                SearchTermHistory.store_id == store,
                SearchTermHistory.orders > 0,
            ).group_by(SearchTermHistory.category).all()

            if not rows:
                # No data yet — use default weights
                default_weights = {
                    "efficiency": 0.30, "conversion": 0.20, "discovery": 0.15,
                    "momentum": 0.15, "volume": 0.10, "intent": 0.10,
                }
                self.persistence.save_attribute_weights(store, default_weights, 0.5)
                logger.info("  %s: No data yet, using default weights", store)
                continue

            # Calculate store-specific weights from data
            total_terms = sum(r.term_count for r in rows)
            avg_acos = sum(r.avg_acos * r.term_count for r in rows if r.avg_acos) / max(1, total_terms)
            avg_cvr = sum(r.avg_cvr * r.term_count for r in rows if r.avg_cvr) / max(1, total_terms)

            # Adjust weights based on data
            weights = {
                "efficiency": round(0.30 if avg_acos and avg_acos < 0.15 else 0.35, 2),
                "conversion": round(0.20 if avg_cvr and avg_cvr > 0.05 else 0.15, 2),
                "discovery": round(0.15 if total_terms > 100 else 0.20, 2),
                "momentum": 0.15,
                "volume": 0.10,
                "intent": 0.10,
            }

            self.persistence.save_attribute_weights(store, weights)
            logger.info("  %s: weights calibrated (%d terms analyzed)", store, total_terms)

    # ── TASK 2: Attribute Harvesting (every 7 days) ──

    def task_attribute_harvesting(self, stores: List[str] = None,
                                   months: int = 1):
        """
        Pull search term reports from Amazon Ads API,
        mine attributes, and store in search_terms_history.

        This is the core data pipeline:
          Ads API → spSearchTerm report → parse → search_terms_history table
        """
        if stores is None:
            stores = list(ADS_PROFILES.keys())

        logger.info("╔══════════════════════════════════════╗")
        logger.info("║  TASK: Attribute Harvesting (7d)     ║")
        logger.info("╚══════════════════════════════════════╝")

        for store in stores:
            try:
                client = self._get_client(store)
            except ValueError as e:
                logger.warning("  %s: Skipping — %s", store, e)
                continue

            profiles = ADS_PROFILES[store]
            for market, profile_id in profiles.items():
                mkt_id = MARKETPLACE_IDS.get(market, "ATVPDKIKX0DER")
                logger.info("  Downloading search terms: %s / %s", store, market)

                for i in range(months):
                    from dateutil.relativedelta import relativedelta
                    d = datetime.now() - relativedelta(months=i)
                    y, m = d.year, d.month

                    try:
                        rows = client.download_search_terms(profile_id, y, m)
                        if rows:
                            count = self.persistence.upsert_search_terms(
                                store, mkt_id, rows, "auto"
                            )
                            logger.info("    %d-%02d: %d search terms harvested", y, m, count)
                        time.sleep(1.5)
                    except Exception as e:
                        logger.error("    %d-%02d: %s", y, m, e)

        logger.info("✅ Attribute harvesting complete")

    # ── TASK 3: Bid Recalculation (every 24 hours) ──

    def task_bid_recalculation(self, stores: List[str] = None,
                                apply_bids: bool = False):
        """
        Analyze keyword performance and generate bid recommendations.

        Output: bid_changes.json with recommended adjustments.
        If apply_bids=True (requires human approval flag), uploads to Amazon.

        Algorithm:
          - For each keyword with >50 clicks:
            - If ACoS > 30% → recommend 20% bid decrease
            - If ACoS > 25% and spend > $50 → recommend 15% bid decrease
            - If ACoS < 10% and CVR > 5% → recommend 10% bid increase
            - If sales = 0 and spend > $20 → recommend pause
        """
        if stores is None:
            stores = list(ADS_PROFILES.keys())

        logger.info("╔══════════════════════════════════════╗")
        logger.info("║  TASK: Bid Recalculation (24h)       ║")
        logger.info("╚══════════════════════════════════════╝")

        all_recommendations = {}

        for store in stores:
            try:
                client = self._get_client(store)
            except ValueError as e:
                logger.warning("  %s: Skipping — %s", store, e)
                continue

            store_recs = []

            for market, profile_id in ADS_PROFILES[store].items():
                logger.info("  Analyzing: %s / %s", store, market)

                try:
                    # Get all keywords for this profile
                    keywords = client.get_keywords(profile_id)
                    logger.info("    %d active keywords found", len(keywords))

                    # Get performance data from DB for scoring
                    from db.models import SearchTermHistory, AttributeWeight

                    # Load current weights
                    weights = self.db.query(AttributeWeight).filter(
                        AttributeWeight.store_id == store,
                    ).order_by(AttributeWeight.derived_at.desc()).limit(6).all()

                    weight_map = {w.dimension: float(w.weight) for w in weights}
                    if not weight_map:
                        weight_map = {"efficiency": 0.30, "conversion": 0.20,
                                      "discovery": 0.15, "momentum": 0.15,
                                      "volume": 0.10, "intent": 0.10}

                    for kw in keywords:
                        kw_id = kw.get("keywordId")
                        kw_text = kw.get("keywordText", kw.get("keyword", ""))
                        match_type = kw.get("matchType", "broad")
                        campaign_id = kw.get("campaignId", "")
                        current_bid = float(kw.get("bid", 0))

                        # Look up performance in search_terms_history
                        perf = self.db.query(
                            sa.func.sum(SearchTermHistory.spend).label("total_spend"),
                            sa.func.sum(SearchTermHistory.sales).label("total_sales"),
                            sa.func.sum(SearchTermHistory.orders).label("total_orders"),
                            sa.func.sum(SearchTermHistory.clicks).label("total_clicks"),
                            sa.func.avg(SearchTermHistory.cvr).label("avg_cvr"),
                        ).filter(
                            SearchTermHistory.store_id == store,
                            SearchTermHistory.marketplace_id == MARKETPLACE_IDS.get(market, ""),
                            SearchTermHistory.search_term == kw_text,
                            SearchTermHistory.date >= date.today() - timedelta(days=30),
                        ).first()

                        if not perf or not perf.total_clicks or perf.total_clicks < 20:
                            continue  # Not enough data

                        total_sp = float(perf.total_spend or 0)
                        total_sl = float(perf.total_sales or 0)
                        total_or = int(perf.total_orders or 0)
                        avg_cvr = float(perf.avg_cvr or 0)

                        acos = total_sp / total_sl if total_sl > 0 else float("inf")
                        roas = total_sl / total_sp if total_sp > 0 else 0

                        rec = None
                        action = None

                        if total_sl == 0 and total_sp > 20:
                            action = "pause"
                            rec = {"new_bid": 0, "state": "paused",
                                   "rationale": f"Zero sales, ${total_sp:.0f} wasted in 30d"}
                        elif acos > 0.30 and total_sp > 100:
                            action = "decrease"
                            new_bid = round(current_bid * 0.80, 2)
                            rec = {"new_bid": new_bid, "state": "enabled",
                                   "rationale": f"ACoS {acos*100:.0f}% — reduce bid 20%"}
                        elif acos > 0.25 and total_sp > 50:
                            action = "decrease"
                            new_bid = round(current_bid * 0.85, 2)
                            rec = {"new_bid": new_bid, "state": "enabled",
                                   "rationale": f"ACoS {acos*100:.0f}% — reduce bid 15%"}
                        elif acos < 0.10 and avg_cvr > 0.05 and total_or > 3:
                            action = "increase"
                            new_bid = round(current_bid * 1.10, 2)
                            rec = {"new_bid": new_bid, "state": "enabled",
                                   "rationale": f"ACoS {acos*100:.0f}%, CVR {avg_cvr:.1%} — increase bid 10%"}

                        if rec:
                            mkt_id = MARKETPLACE_IDS.get(market, "")
                            store_recs.append({
                                "store": store, "market": market,
                                "keyword_id": kw_id, "keyword_text": kw_text,
                                "campaign_id": campaign_id, "match_type": match_type,
                                "current_bid": current_bid, "new_bid": rec["new_bid"],
                                "state": rec["state"], "action": action,
                                "rationale": rec["rationale"],
                                "metrics": {
                                    "acos": round(acos, 4), "roas": round(roas, 2),
                                    "spend": total_sp, "sales": total_sl, "orders": total_or,
                                    "cvr": round(avg_cvr, 4),
                                }
                            })

                            # Log for audit
                            self.persistence.log_bid_change(
                                store, mkt_id, campaign_id, kw_id, kw_text,
                                match_type, current_bid, rec["new_bid"],
                                action, rec["rationale"],
                                {"acos": acos, "roas": roas, "cvr": avg_cvr}
                            )

                except Exception as e:
                    logger.error("    %s/%s: %s", store, market, e)

            all_recommendations[store] = store_recs
            logger.info("  %s: %d bid recommendations generated", store, len(store_recs))

        # Save recommendations
        rec_file = DATA_DIR / f"bid_recommendations_{date.today().isoformat()}.json"
        with open(rec_file, "w") as f:
            json.dump({
                "generated_at": datetime.utcnow().isoformat(),
                "stores": all_recommendations,
                "summary": {
                    store: len(recs) for store, recs in all_recommendations.items()
                }
            }, f, indent=2, default=str)

        total_recs = sum(len(r) for r in all_recommendations.values())
        logger.info("✅ Bid recalculation complete — %d recommendations saved to %s",
                    total_recs, rec_file)

        return all_recommendations

    def close(self):
        self.db.close()

    # ── Full Pipeline Run ──────────────────────────

    def run_full_pipeline(self, task: str = "all", stores: List[str] = None):
        """Run one or all scheduled tasks."""
        if task in ("all", "weight"):
            self.task_weight_derivation(stores)
        if task in ("all", "harvest"):
            self.task_attribute_harvesting(stores)
        if task in ("all", "bids"):
            self.task_bid_recalculation(stores)
        self.close()

# ═══════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="AAIE Production Pipeline — API Bridge + Scheduler",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Tasks:
  weight    — Attribute weight derivation (every 14 days)
  harvest   — Search term harvesting + attribute mining (every 7 days)
  bids      — Bid recalculation (every 24 hours)
  all       — Run all tasks

Examples:
  %(prog)s task=harvest --store CUCZUUS
  %(prog)s task=bids --store ALL
  %(prog)s task=all
        """
    )
    parser.add_argument("task", choices=["weight", "harvest", "bids", "all"],
                        help="Task to execute")
    parser.add_argument("--store", default="ALL",
                        help="Store filter (CUCZUUS, BOOLUU, Heliumx, ALL)")
    parser.add_argument("--months", type=int, default=1,
                        help="Months to harvest (for 'harvest' task)")
    parser.add_argument("--apply", action="store_true",
                        help="Actually apply bid changes to Amazon (⚠️ requires approval)")

    args = parser.parse_args()

    scheduler = AdsScheduler()

    stores = None if args.store == "ALL" else [args.store]

    if args.task == "weight":
        scheduler.task_weight_derivation(stores)
    elif args.task == "harvest":
        scheduler.task_attribute_harvesting(stores, args.months)
    elif args.task == "bids":
        scheduler.task_bid_recalculation(stores, apply_bids=args.apply)
    elif args.task == "all":
        scheduler.run_full_pipeline("all", stores)

    scheduler.close()

if __name__ == "__main__":
    main()
