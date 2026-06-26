#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║     AMAZON ADS INTELLIGENT ENGINE v2.0 — MAX AUTOMATION             ║
║  Search terms → Attributes → Scoring → Monitoring → Automation      ║
║  CUCZUUS · BOOLUU · Heliumx · All Markets                          ║
╚══════════════════════════════════════════════════════════════════════╝

How it works:
  1. SEARCH TERM MINING     — Extract attributes from raw search terms
  2. ATTRIBUTE SCORING      — Multi-dimensional performance scores
  3. CPC & METRIC MONITOR   — Track every keyword's CPC/impression/CVR
  4. ANOMALY DETECTION      — Spot performance shifts immediately
  5. AUTOMATION PIPELINE    — Auto-negatives, bid recs, budget shifts

Design principle: NEVER auto-adjust bids. Always recommend → human approves.
"""

import json, os, sys, re, argparse, logging, time
from pathlib import Path
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple, Set, Union
from collections import defaultdict, Counter
from enum import Enum

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from sqlalchemy import func

# ═══════════════════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data_lake" / "ads"
DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ads_intelligent_v2")

def load_env():
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
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

# ═══════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════

class AttrSource(Enum):
    SEARCH_TERM = "search_term"       # From customer search queries
    AUTO_CAMPAIGN = "auto_campaign"   # Discovered by Amazon auto-targeting
    MANUAL_KEYWORD = "manual_keyword" # Explicitly bid keyword
    PRODUCT_TARGET = "product_target" # ASIN targeting
    PLACEMENT = "placement"           # Top of search, product page, etc.

class ScoreDimension(Enum):
    EFFICIENCY = "efficiency"     # ACoS, ROAS — is it profitable?
    VOLUME = "volume"             # Impressions, reach, spend scale
    CONVERSION = "conversion"     # CVR, orders per click
    MOMENTUM = "momentum"         # Trending up or down?
    INTENT = "intent"             # Search intent → product match quality
    DISCOVERY = "discovery"       # New keyword potential (low spend, high signal)

class ActionType(Enum):
    NEGATIVE_EXACT = "negative_exact"
    NEGATIVE_PHRASE = "negative_phrase"
    BID_INCREASE = "bid_increase"
    BID_DECREASE = "bid_decrease"
    PROMOTE_TO_MANUAL = "promote_to_manual"  # Auto → Manual
    PAUSE = "pause"
    SCALE_BUDGET = "scale_budget"
    MONITOR = "monitor"

@dataclass
class SearchTermAttribute:
    """A parsed attribute from a search term query."""
    raw_query: str
    root_keyword: str          # e.g., "round bathroom mirror 24 inch" → "round bathroom mirror"
    modifier: str              # e.g., "24 inch", "black", "with lights"
    intent_signal: str         # "purchase", "research", "comparison"
    match_type: str            # broad, phrase, exact (from campaign)
    campaign_type: str         # auto, manual
    campaign_id: str
    campaign_name: str
    impressions: int = 0
    clicks: int = 0
    spend: float = 0.0
    sales: float = 0.0
    orders: int = 0
    ctr: float = 0.0
    cvr: float = 0.0
    acos: float = 0.0
    roas: float = 0.0
    cpc: float = 0.0

@dataclass
class AttributeScore:
    """Multi-dimensional score for a search attribute."""
    attribute: str                # The keyword/ASIN/placement
    source: AttrSource
    campaign_id: str
    campaign_name: str
    total_impressions: int = 0
    total_clicks: int = 0
    total_spend: float = 0.0
    total_sales: float = 0.0
    total_orders: int = 0
    avg_cpc: float = 0.0
    avg_ctr: float = 0.0
    avg_cvr: float = 0.0
    acos: float = 0.0
    roas: float = 0.0
    # Scores (0-100 each)
    efficiency_score: float = 0.0
    volume_score: float = 0.0
    conversion_score: float = 0.0
    momentum_score: float = 0.0
    intent_score: float = 0.0
    discovery_score: float = 0.0
    # Composite
    composite_score: float = 0.0
    # Actions
    recommended_actions: List[ActionType] = field(default_factory=list)
    action_confidence: float = 0.0
    action_rationale: str = ""

@dataclass
class KeywordMonitor:
    """Real-time monitoring state for a keyword."""
    keyword: str
    campaign_id: str
    current_cpc: float = 0.0
    avg_cpc_7d: float = 0.0
    avg_cpc_30d: float = 0.0
    cpc_trend: str = "stable"       # rising, falling, stable
    current_impressions: int = 0
    avg_impressions_7d: int = 0
    impression_trend: str = "stable"
    current_ctr: float = 0.0
    avg_ctr_7d: float = 0.0
    current_cvr: float = 0.0
    avg_cvr_7d: float = 0.0
    conversion_trend: str = "stable"
    budget_pacing_pct: float = 0.0  # % of daily budget used
    alerts: List[str] = field(default_factory=list)

@dataclass
class AutoDiscovery:
    """Keyword discovered from auto campaigns worth promoting to manual."""
    keyword: str
    campaign_name: str
    market: str
    spend: float = 0.0
    sales: float = 0.0
    orders: int = 0
    acos: float = 0.0
    ctr: float = 0.0
    cvr: float = 0.0
    confidence: float = 0.0
    suggestion: str = ""

# ═══════════════════════════════════════════════════════════
# 1. SEARCH TERM MINING ENGINE
# ═══════════════════════════════════════════════════════════

class SearchTermMiner:
    """
    Extracts attributes from raw search term data.

    Capabilities:
    - Parse search queries into root keywords + modifiers
    - Detect shopping intent signals
    - Mine auto campaigns for undiscovered keywords
    - Cross-reference with manual campaigns to find gaps
    """

    # Intent signal words
    PURCHASE_SIGNALS = {"buy", "cheap", "best price", "sale", "discount", "deal",
                        "for sale", "amazon", "prime", "order", "price", "cost"}
    RESEARCH_SIGNALS = {"review", "vs", "versus", "compare", "best", "top",
                        "recommended", "rating", "size", "dimension", "how to"}
    COMPARISON_SIGNALS = {"alternative", "similar", "like", "or", "instead of"}

    # Common stop words for keyword extraction
    STOP_WORDS = {"a", "an", "the", "and", "or", "for", "of", "in", "on", "to",
                  "with", "is", "it", "at", "as", "be", "by", "no", "not",
                  "this", "that", "from", "but", "so", "if", "can", "all",
                  "just", "about", "also", "get", "up", "out", "when", "which"}

    # Known product categories for the stores
    CATEGORY_PATTERNS = {
        "mirror": re.compile(r"(mirror|镜子|鏡)", re.IGNORECASE),
        "aquarium": re.compile(r"(aquarium|fish tank|鱼缸|魚缸|fish bowl)", re.IGNORECASE),
        "fitness": re.compile(r"(fitness|gym|exercise|workout|健身|运动|瑜伽)", re.IGNORECASE),
        "frame": re.compile(r"(frame|框|边框|frame mirror)", re.IGNORECASE),
        "glass": re.compile(r"(glass|玻璃|tempered|钢化)", re.IGNORECASE),
        "cloth": re.compile(r"(cloth|fabric|布|microfiber|magic cloth|魔法布)", re.IGNORECASE),
        "led": re.compile(r"(led|light|灯|lighted|illuminated|backlit)", re.IGNORECASE),
        "bathroom": re.compile(r"(bathroom|vanity|浴室|卫生间|洗手间)", re.IGNORECASE),
    }

    # Size patterns
    SIZE_PATTERN = re.compile(r"(\d+)\s*(inch|in|\"|''|cm|mm|feet|ft)", re.IGNORECASE)

    def extract_root_keyword(self, query: str) -> Tuple[str, str, str]:
        """
        Extract root keyword + modifier + category from a search query.

        Example:
          "round bathroom mirror 24 inch led" →
            root: "bathroom mirror led"
            modifier: "round 24 inch"
            category: "mirror"
        """
        if not query:
            return "", "", ""

        query_lower = query.lower().strip()
        words = query_lower.split()

        # Find size modifiers
        size_matches = self.SIZE_PATTERN.findall(query)
        size_words = set()
        for num, unit in size_matches:
            size_words.add(num)
            size_words.add(unit)

        # Find category
        category = "other"
        for cat_name, pattern in self.CATEGORY_PATTERNS.items():
            if pattern.search(query):
                category = cat_name
                break

        # Separate root keywords from modifiers
        root_words = []
        modifier_words = []

        for w in words:
            w_clean = w.strip('.,!?\'"()-')
            if w_clean in self.STOP_WORDS:
                continue
            if w_clean in size_words or w_clean.isdigit():
                modifier_words.append(w)
            # Color modifiers
            elif w_clean in {"black", "white", "silver", "gold", "bronze", "chrome",
                             "round", "square", "rectangular", "oval", "frameless",
                             "beveled", "flat", "curved", "round", "circle"}:
                modifier_words.append(w)
            else:
                root_words.append(w)

        root = " ".join(root_words)
        modifier = " ".join(modifier_words) if modifier_words else ""

        # Detect intent
        intent = self._detect_intent(query_lower)

        return root, modifier, category

    def _detect_intent(self, query: str) -> str:
        """Detect shopping intent from query."""
        purchase_count = sum(1 for s in self.PURCHASE_SIGNALS if s in query)
        research_count = sum(1 for s in self.RESEARCH_SIGNALS if s in query)

        if purchase_count > research_count:
            return "purchase"
        elif research_count > purchase_count:
            return "research"
        elif self.COMPARISON_SIGNALS.intersection(set(query.split())):
            return "comparison"
        return "browse"

    def mine_search_terms(self, store_id: str, days: int = 30) -> List[SearchTermAttribute]:
        """
        Mine search term reports for attribute extraction.

        How it works:
        1. Pull all keyword-level data from DB
        2. Parse each search query into attributes
        3. Cross-reference auto vs manual campaigns
        """
        from db.models import AdPerformance

        db = get_db()
        try:
            start = date.today() - timedelta(days=days)
            rows = db.query(AdPerformance).filter(
                AdPerformance.store_id == store_id,
                AdPerformance.date >= start,
                AdPerformance.keyword.isnot(None),
                AdPerformance.keyword != "",
            ).all()

            attributes = []
            for row in rows:
                root, modifier, category = self.extract_root_keyword(row.keyword)

                imp = int(row.impressions or 0)
                clk = int(row.clicks or 0)
                sp = float(row.spend or 0)
                sl = float(row.sales or 0)
                ords = int(row.orders or 0)

                attr = SearchTermAttribute(
                    raw_query=row.keyword,
                    root_keyword=root,
                    modifier=modifier,
                    intent_signal=self._detect_intent(row.keyword or ""),
                    match_type=row.match_type or "broad",
                    campaign_type="manual" if "手动" in (row.raw_data or {}).get("campaignName", "") or "manual" in str(row.match_type).lower() else "auto",
                    campaign_id=row.campaign_id,
                    campaign_name=(row.raw_data or {}).get("campaignName", row.campaign_id),
                    impressions=imp, clicks=clk, spend=sp, sales=sl, orders=ords,
                    ctr=clk/imp if imp > 0 else 0,
                    cvr=ords/clk if clk > 0 else 0,
                    acos=sp/sl if sl > 0 else (float("inf") if sp > 0 else 0),
                    roas=sl/sp if sp > 0 else 0,
                    cpc=sp/clk if clk > 0 else 0,
                )
                attributes.append(attr)

            return attributes
        finally:
            db.close()

    def mine_auto_campaigns(self, store_id: str, days: int = 30) -> List[AutoDiscovery]:
        """
        Mine auto campaigns for keywords worth promoting to manual.

        Auto campaigns are Amazon's "fishing net" — they discover keywords
        we didn't think to bid on. This method finds the winners.
        """
        from db.models import AdPerformance

        db = get_db()
        try:
            start = date.today() - timedelta(days=days)
            rows = db.query(AdPerformance).filter(
                AdPerformance.store_id == store_id,
                AdPerformance.date >= start,
                AdPerformance.keyword.isnot(None),
                AdPerformance.keyword != "",
                AdPerformance.match_type == "auto",
            ).all()

            discoveries = []
            for row in rows:
                kw = row.keyword
                imp = int(row.impressions or 0)
                clk = int(row.clicks or 0)
                sp = float(row.spend or 0)
                sl = float(row.sales or 0)
                ords = int(row.orders or 0)

                # Only promote keywords with proven conversion
                if ords >= 3 and sl > 50 and sp > 0:
                    acos = sp / sl if sl > 0 else float("inf")
                    ctr = clk / imp if imp > 0 else 0
                    cvr = ords / clk if clk > 0 else 0

                    if acos < 0.25:  # Profitable enough to promote
                        confidence = min(0.95, (ords / 10) * 0.5 + (1 - acos) * 0.5)
                        discoveries.append(AutoDiscovery(
                            keyword=kw,
                            campaign_name=(row.raw_data or {}).get("campaignName", row.campaign_id),
                            market=row.marketplace_id,
                            spend=round(sp, 2),
                            sales=round(sl, 2),
                            orders=ords,
                            acos=round(acos, 4),
                            ctr=round(ctr, 4),
                            cvr=round(cvr, 4),
                            confidence=round(confidence, 2),
                            suggestion=f"Promote '{kw}' to manual exact match. "
                                      f"{ords} orders, {acos*100:.0f}% ACoS, "
                                      f"${sl:.0f} sales from auto campaign.",
                        ))

            return sorted(discoveries, key=lambda d: d.sales, reverse=True)
        finally:
            db.close()

# ═══════════════════════════════════════════════════════════
# 2. ATTRIBUTE SCORING ENGINE
# ═══════════════════════════════════════════════════════════

class AttributeScorer:
    """
    Multi-dimensional scoring for every search attribute.

    Scoring dimensions (each 0-100):
      - Efficiency: How profitable? (ACoS + ROAS based)
      - Volume: How much reach/spend? (scale indicator)
      - Conversion: How well does it convert? (CVR based)
      - Momentum: Trending up or down? (vs historical)
      - Intent: Search intent → purchase match quality
      - Discovery: Potential for new high-performer
    """

    def score_attributes(self, attributes: List[SearchTermAttribute]) -> List[AttributeScore]:
        """Score a list of search term attributes."""
        if not attributes:
            return []

        # Aggregate by root keyword (combine same keywords across campaigns)
        grouped = defaultdict(lambda: {
            "attributes": [],
            "impressions": 0, "clicks": 0, "spend": 0.0,
            "sales": 0.0, "orders": 0, "ctr_sum": 0.0, "cvr_sum": 0.0,
            "cpc_sum": 0.0, "count": 0, "sources": set(),
            "campaign_ids": set(), "campaign_names": set(),
        })

        for attr in attributes:
            key = attr.root_keyword or attr.raw_query
            g = grouped[key]
            g["attributes"].append(attr)
            g["impressions"] += attr.impressions
            g["clicks"] += attr.clicks
            g["spend"] += attr.spend
            g["sales"] += attr.sales
            g["orders"] += attr.orders
            g["ctr_sum"] += attr.ctr
            g["cvr_sum"] += attr.cvr
            g["cpc_sum"] += attr.cpc
            g["count"] += 1
            g["campaign_ids"].add(attr.campaign_id)
            g["campaign_names"].add(attr.campaign_name)
            # Determine source
            if attr.campaign_type == "auto":
                g["sources"].add(AttrSource.AUTO_CAMPAIGN)
            else:
                g["sources"].add(AttrSource.MANUAL_KEYWORD)

        scores = []
        for root_kw, g in grouped.items():
            n = g["count"]
            total_sp = g["spend"]
            total_sl = g["sales"]
            total_im = g["impressions"]
            total_cl = g["clicks"]
            total_or = g["orders"]

            acos = total_sp / total_sl if total_sl > 0 else (float("inf") if total_sp > 0 else 0)
            roas = total_sl / total_sp if total_sp > 0 else 0
            avg_ctr = g["ctr_sum"] / n if n > 0 else 0
            avg_cvr = g["cvr_sum"] / n if n > 0 else 0
            avg_cpc = g["cpc_sum"] / n if n > 0 else 0

            # ── EFFICIENCY SCORE ──
            if acos <= 0.10: eff = 90 + (0.10 - acos) * 200
            elif acos <= 0.20: eff = 60 + (0.20 - acos) * 300
            elif acos <= 0.30: eff = 20 + (0.30 - acos) * 400
            else: eff = max(0, 20 - (acos - 0.30) * 200)
            eff = min(100, max(0, eff))

            # ── VOLUME SCORE ──
            # Based on impressions and spend scale relative to dataset
            max_im = max(1, max(g2["impressions"] for g2 in grouped.values()))
            vol_im = min(100, (total_im / max_im) * 70)
            vol_sp = min(30, (total_sp / max(1, total_sp)) * 30)
            vol = min(100, vol_im + vol_sp)

            # ── CONVERSION SCORE ──
            if avg_cvr >= 0.20: conv = 90 + (avg_cvr - 0.20) * 100
            elif avg_cvr >= 0.10: conv = 60 + (avg_cvr - 0.10) * 300
            elif avg_cvr >= 0.05: conv = 30 + (avg_cvr - 0.05) * 600
            else: conv = max(0, avg_cvr * 600)
            conv = min(100, max(0, conv))

            # ── MOMENTUM SCORE ──
            # Based on recent order activity vs spend
            order_efficiency = total_or / max(1, total_sp / 10)  # orders per $10 spend
            if order_efficiency > 2: mom = 80 + min(20, order_efficiency * 5)
            elif order_efficiency > 1: mom = 50 + (order_efficiency - 1) * 30
            elif order_efficiency > 0.5: mom = 20 + (order_efficiency - 0.5) * 60
            else: mom = max(0, order_efficiency * 40)
            mom = min(100, max(0, mom))

            # ── INTENT SCORE ──
            intent_signals = defaultdict(int)
            for attr in g["attributes"]:
                intent_signals[attr.intent_signal] += 1
            purchase_ratio = intent_signals.get("purchase", 0) / max(1, n)
            intent = min(100, purchase_ratio * 100 + 20)
            intent = min(100, max(0, intent))

            # ── DISCOVERY SCORE ──
            # Keywords from auto campaigns with good metrics = high discovery
            has_auto = AttrSource.AUTO_CAMPAIGN in g["sources"]
            is_new = total_im < 5000 and total_or > 0
            disc = 0
            if has_auto and is_new:
                disc = min(100, (total_or * 15) + eff * 0.4)
            elif has_auto:
                disc = min(60, eff * 0.5)

            # ── COMPOSITE ──
            composite = (eff * 0.30 + vol * 0.10 + conv * 0.20 +
                        mom * 0.15 + intent * 0.10 + disc * 0.15)
            composite = min(100, max(0, composite))

            # ── RECOMMENDED ACTIONS ──
            actions = self._determine_actions(
                acos, roas, total_sp, total_sl, total_or,
                avg_cvr, avg_ctr, has_auto, composite
            )

            # Best source
            primary_source = (AttrSource.MANUAL_KEYWORD if AttrSource.MANUAL_KEYWORD in g["sources"]
                            else list(g["sources"])[0] if g["sources"] else AttrSource.SEARCH_TERM)

            scores.append(AttributeScore(
                attribute=root_kw,
                source=primary_source,
                campaign_id=",".join(list(g["campaign_ids"])[:3]),
                campaign_name=", ".join(list(g["campaign_names"])[:2]),
                total_impressions=total_im,
                total_clicks=total_cl,
                total_spend=round(total_sp, 2),
                total_sales=round(total_sl, 2),
                total_orders=total_or,
                avg_cpc=round(avg_cpc, 2),
                avg_ctr=round(avg_ctr, 4),
                avg_cvr=round(avg_cvr, 4),
                acos=round(acos, 4),
                roas=round(roas, 2),
                efficiency_score=round(eff, 1),
                volume_score=round(vol, 1),
                conversion_score=round(conv, 1),
                momentum_score=round(mom, 1),
                intent_score=round(intent, 1),
                discovery_score=round(disc, 1),
                composite_score=round(composite, 1),
                recommended_actions=actions,
                action_confidence=round(min(0.95, composite / 100), 2),
                action_rationale=self._rationale(actions, acos, roas, total_sp),
            ))

        return sorted(scores, key=lambda s: s.composite_score, reverse=True)

    def _determine_actions(self, acos: float, roas: float, spend: float,
                           sales: float, orders: int, cvr: float, ctr: float,
                           from_auto: bool, composite: float) -> List[ActionType]:
        """Determine recommended actions for an attribute."""
        actions = []

        if sales == 0 and spend > 20:
            actions.append(ActionType.NEGATIVE_EXACT)
        elif acos > 0.50 and orders == 0 and spend > 15:
            actions.append(ActionType.NEGATIVE_PHRASE)
        elif acos > 0.40 and spend > 50:
            actions.append(ActionType.BID_DECREASE)
        elif acos > 0.25 and spend > 100:
            actions.append(ActionType.BID_DECREASE)

        if acos < 0.10 and roas > 10 and cvr > 0.05:
            actions.append(ActionType.BID_INCREASE)
        elif acos < 0.15 and roas > 5 and orders > 5:
            actions.append(ActionType.SCALE_BUDGET)

        if from_auto and acos < 0.20 and orders >= 3 and sales > 50:
            actions.append(ActionType.PROMOTE_TO_MANUAL)

        if not actions:
            actions.append(ActionType.MONITOR)

        return actions

    def _rationale(self, actions: List[ActionType], acos: float,
                   roas: float, spend: float) -> str:
        """Generate human-readable rationale."""
        parts = []
        for a in actions:
            if a == ActionType.NEGATIVE_EXACT:
                parts.append(f"Zero sales, ${spend:.0f} wasted")
            elif a == ActionType.NEGATIVE_PHRASE:
                parts.append(f"High spend (${spend:.0f}), zero conversions")
            elif a == ActionType.BID_DECREASE:
                parts.append(f"ACoS {acos*100:.0f}% too high at ${spend:.0f} spend")
            elif a == ActionType.BID_INCREASE:
                parts.append(f"ACoS only {acos*100:.0f}%, RoAS {roas:.1f}x — room to scale")
            elif a == ActionType.SCALE_BUDGET:
                parts.append(f"Strong performer: ACoS {acos*100:.0f}%, RoAS {roas:.1f}x")
            elif a == ActionType.PROMOTE_TO_MANUAL:
                parts.append(f"Auto campaign winner — promote to manual")
            elif a == ActionType.MONITOR:
                parts.append("Healthy — monitor")
        return " | ".join(parts)

# ═══════════════════════════════════════════════════════════
# 3. PERFORMANCE MONITOR
# ═══════════════════════════════════════════════════════════

class PerformanceMonitor:
    """
    Real-time CPC, impression, and conversion monitoring.

    Tracks every keyword's metrics and flags when:
    - CPC rises above baseline
    - Impressions drop significantly
    - Conversion rate shifts
    - Budget pacing is off
    """

    CPC_SPIKE_THRESHOLD = 1.3     # 30% above 30d avg
    IMPRESSION_DROP_THRESHOLD = 0.5  # 50% below 7d avg
    CVR_DROP_THRESHOLD = 0.3      # 70% below 7d avg
    BUDGET_OVER_PACE = 0.80       # 80% of daily budget spent early

    def monitor_keywords(self, store_id: str, days: int = 30) -> List[KeywordMonitor]:
        """Monitor all keywords for anomalies."""
        from db.models import AdPerformance

        db = get_db()
        try:
            today = date.today()
            start_30d = today - timedelta(days=days)
            start_7d = today - timedelta(days=7)

            # Get current (7d) and baseline (30d) metrics
            rows = db.query(
                AdPerformance.keyword,
                AdPerformance.campaign_id,
                func.sum(AdPerformance.spend).label("spend"),
                func.sum(AdPerformance.sales).label("sales"),
                func.sum(AdPerformance.impressions).label("impressions"),
                func.sum(AdPerformance.clicks).label("clicks"),
                func.sum(AdPerformance.orders).label("orders"),
            ).filter(
                AdPerformance.store_id == store_id,
                AdPerformance.date >= start_30d,
                AdPerformance.keyword.isnot(None),
            ).group_by(
                AdPerformance.keyword, AdPerformance.campaign_id,
            ).all()

            # Also get 7d data separately
            rows_7d = db.query(
                AdPerformance.keyword,
                AdPerformance.campaign_id,
                func.sum(AdPerformance.spend).label("spend"),
                func.sum(AdPerformance.impressions).label("impressions"),
                func.sum(AdPerformance.clicks).label("clicks"),
                func.sum(AdPerformance.orders).label("orders"),
            ).filter(
                AdPerformance.store_id == store_id,
                AdPerformance.date >= start_7d,
                AdPerformance.keyword.isnot(None),
            ).group_by(
                AdPerformance.keyword, AdPerformance.campaign_id,
            ).all()

            # Build lookup
            recent = {}
            for r in rows_7d:
                recent[(r.keyword, r.campaign_id)] = r

            monitors = []
            for r in rows:
                kw, cid = r.keyword, r.campaign_id
                r7 = recent.get((kw, cid))

                sp_30d = float(r.spend or 0)
                cl_30d = int(r.clicks or 0)
                im_30d = int(r.impressions or 0)
                or_30d = int(r.orders or 0)

                cpc_30d = sp_30d / cl_30d if cl_30d > 0 else 0
                avg_im_7d = im_30d / 4.3  # 30d / ~4.3 weeks
                avg_cvr_30d = or_30d / cl_30d if cl_30d > 0 else 0
                avg_ctr_30d = cl_30d / im_30d if im_30d > 0 else 0

                # Current (7d) metrics
                cur_cpc = cpc_30d
                cur_im = int(r7.impressions or 0) if r7 else 0
                cur_cl = int(r7.clicks or 0) if r7 else 0
                cur_or = int(r7.orders or 0) if r7 else 0
                cur_ctr = cur_cl / cur_im if cur_im > 0 else 0
                cur_cvr = cur_or / cur_cl if cur_cl > 0 else 0

                if r7:
                    cur_sp = float(r7.spend or 0)
                    cur_cpc = cur_sp / cur_cl if cur_cl > 0 else 0

                # Trend detection
                cpc_trend = "stable"
                if cur_cpc > cpc_30d * self.CPC_SPIKE_THRESHOLD:
                    cpc_trend = "rising"
                elif cur_cpc < cpc_30d * 0.7:
                    cpc_trend = "falling"

                imp_trend = "stable"
                if r7 and cur_im < avg_im_7d * self.IMPRESSION_DROP_THRESHOLD:
                    imp_trend = "falling"
                elif r7 and cur_im > avg_im_7d * 1.5:
                    imp_trend = "rising"

                conv_trend = "stable"
                if cur_cvr < avg_cvr_30d * self.CVR_DROP_THRESHOLD and cur_cl >= 10:
                    conv_trend = "falling"
                elif cur_cvr > avg_cvr_30d * 1.5:
                    conv_trend = "rising"

                # Alerts
                alerts = []
                if cpc_trend == "rising" and cl_30d > 20:
                    alerts.append(f"CPC rising: ${cur_cpc:.2f} vs 30d avg ${cpc_30d:.2f}")
                if imp_trend == "falling" and im_30d > 500:
                    alerts.append(f"Impression drop: {cur_im} this week vs {avg_im_7d:.0f} avg")
                if conv_trend == "falling" and cl_30d > 20:
                    alerts.append(f"CVR falling: {cur_cvr:.1%} vs avg {avg_cvr_30d:.1%}")

                if sp_30d > 5:  # Only monitor keywords with meaningful spend
                    monitors.append(KeywordMonitor(
                        keyword=kw,
                        campaign_id=cid,
                        current_cpc=round(cur_cpc, 2),
                        avg_cpc_7d=round(cur_cpc, 2),
                        avg_cpc_30d=round(cpc_30d, 2),
                        cpc_trend=cpc_trend,
                        current_impressions=cur_im,
                        avg_impressions_7d=int(avg_im_7d),
                        impression_trend=imp_trend,
                        current_ctr=round(cur_ctr, 4),
                        avg_ctr_7d=round(avg_ctr_30d, 4),
                        current_cvr=round(cur_cvr, 4),
                        avg_cvr_7d=round(avg_cvr_30d, 4),
                        conversion_trend=conv_trend,
                        alerts=alerts,
                    ))

            return sorted(monitors, key=lambda m: len(m.alerts), reverse=True)
        finally:
            db.close()

# ═══════════════════════════════════════════════════════════
# 4. AUTOMATION PIPELINE
# ═══════════════════════════════════════════════════════════

class AutomationPipeline:
    """
    Generates an actionable automation plan from scored attributes.

    Output: A prioritized list of actions with:
    - What to do (negative keyword, bid change, promote to manual)
    - Which campaign
    - Expected impact
    - Risk level
    """

    def __init__(self, miner: SearchTermMiner, scorer: AttributeScorer,
                 monitor: PerformanceMonitor):
        self.miner = miner
        self.scorer = scorer
        self.monitor = monitor

    def generate_plan(self, store_id: str, days: int = 30) -> Dict:
        """
        Generate a complete automation plan for a store.

        Returns:
          {
            "negatives": [...],       // Keywords to negate
            "bid_changes": [...],     // Bid adjustments
            "promotions": [...],      // Auto → manual promotions
            "monitor_alerts": [...],  // Active alerts
            "summary": str,           // Executive summary
          }
        """
        # 1. Mine search terms
        attributes = self.miner.mine_search_terms(store_id, days)

        # 2. Score them
        scored = self.scorer.score_attributes(attributes)

        # 3. Get auto discoveries
        discoveries = self.miner.mine_auto_campaigns(store_id, days)

        # 4. Monitor
        monitors = self.monitor.monitor_keywords(store_id, days)

        # 5. Categorize actions
        negatives = [s for s in scored if ActionType.NEGATIVE_EXACT in s.recommended_actions
                     or ActionType.NEGATIVE_PHRASE in s.recommended_actions]
        bid_changes = [s for s in scored if ActionType.BID_INCREASE in s.recommended_actions
                       or ActionType.BID_DECREASE in s.recommended_actions]
        scales = [s for s in scored if ActionType.SCALE_BUDGET in s.recommended_actions]
        active_alerts = [m for m in monitors if m.alerts]

        # 6. Summary
        total_wasted = sum(s.total_spend for s in negatives if s.total_sales == 0)
        total_potential_savings = sum(s.total_spend * 0.3 for s in scored
                                      if ActionType.BID_DECREASE in s.recommended_actions)
        total_scale_opportunity = sum(s.total_sales for s in scales)

        summary_parts = []
        if negatives:
            summary_parts.append(
                f"🛑 {len(negatives)} negative keyword candidates "
                f"(${total_wasted:.0f} wasted on zero-sales terms)"
            )
        if bid_changes:
            bids_up = len([s for s in bid_changes if ActionType.BID_INCREASE in s.recommended_actions])
            bids_down = len([s for s in bid_changes if ActionType.BID_DECREASE in s.recommended_actions])
            summary_parts.append(
                f"📊 {bids_down} bid decreases, {bids_up} bid increases recommended "
                f"(~${total_potential_savings:.0f} potential savings)"
            )
        if discoveries:
            summary_parts.append(
                f"🔍 {len(discoveries)} auto-campaign keywords ready to promote to manual"
            )
        if active_alerts:
            summary_parts.append(
                f"⚠️ {len(active_alerts)} keywords with active monitoring alerts"
            )

        return {
            "store_id": store_id,
            "period_days": days,
            "total_attributes_scored": len(scored),
            "negatives": [{
                "keyword": s.attribute,
                "spend_wasted": round(s.total_spend, 2),
                "campaigns": s.campaign_name,
                "confidence": s.action_confidence,
                "rationale": s.action_rationale,
            } for s in negatives[:20]],
            "bid_changes": [{
                "keyword": s.attribute,
                "current_acos": round(s.acos * 100, 1),
                "current_roas": s.roas,
                "current_spend": s.total_spend,
                "current_sales": s.total_sales,
                "actions": [a.value for a in s.recommended_actions],
                "rationale": s.action_rationale,
            } for s in bid_changes[:15]],
            "promotions": [asdict(d) for d in discoveries[:20]],
            "monitor_alerts": [{
                "keyword": m.keyword,
                "alerts": m.alerts,
                "cpc_trend": m.cpc_trend,
                "cvr_trend": m.conversion_trend,
            } for m in active_alerts[:15]],
            "summary": " | ".join(summary_parts) if summary_parts else "No actions needed — all campaigns healthy",
            "scored_attributes": [{
                "attribute": s.attribute,
                "composite": s.composite_score,
                "efficiency": s.efficiency_score,
                "volume": s.volume_score,
                "conversion": s.conversion_score,
                "momentum": s.momentum_score,
                "intent": s.intent_score,
                "discovery": s.discovery_score,
                "acos": round(s.acos * 100, 1),
                "roas": s.roas,
                "spend": s.total_spend,
                "sales": s.total_sales,
                "orders": s.total_orders,
                "actions": [a.value for a in s.recommended_actions],
            } for s in scored[:30]],
        }

# ═══════════════════════════════════════════════════════════
# 5. MAIN ENGINE ORCHESTRATOR
# ═══════════════════════════════════════════════════════════

class AdsIntelligentEngineV2:
    """
    AAIE v2 — The full automation pipeline.

    Usage:
        engine = AdsIntelligentEngineV2()
        plan = engine.run("CUCZUUS", days=30)
        engine.print_report(plan)
    """

    def __init__(self):
        load_env()
        self.miner = SearchTermMiner()
        self.scorer = AttributeScorer()
        self.monitor = PerformanceMonitor()
        self.pipeline = AutomationPipeline(self.miner, self.scorer, self.monitor)

    def run(self, store_id: str, days: int = 30) -> Dict:
        """Run the full intelligence pipeline."""
        logger.info("Running AAIE v2 for %s (%d days)", store_id, days)
        return self.pipeline.generate_plan(store_id, days)

    def run_all_stores(self, days: int = 30) -> Dict[str, Dict]:
        """Run across all stores."""
        stores = ["CUCZUUS", "BOOLUU", "Heliumx"]
        results = {}
        for s in stores:
            try:
                results[s] = self.run(s, days)
            except Exception as e:
                logger.error("Failed for %s: %s", s, e)
                results[s] = {"error": str(e)}
        return results

    def print_report(self, plan: Dict):
        """Print a human-readable report."""
        print(f"\n{'='*65}")
        print(f"  🧠 AAIE v2 — {plan.get('store_id', '')} ({plan.get('period_days', '')}d)")
        print(f"{'='*65}")
        print(f"\n{plan.get('summary', '')}\n")

        # Negative keywords
        negs = plan.get("negatives", [])
        if negs:
            print("🛑 NEGATIVE KEYWORD CANDIDATES")
            print("-" * 50)
            for n in negs[:10]:
                print(f"  • '{n['keyword']}' — ${n['spend_wasted']:.0f} wasted")
                print(f"    {n['rationale']}")
            print()

        # Bid changes
        bids = plan.get("bid_changes", [])
        if bids:
            print("📊 BID ADJUSTMENTS")
            print("-" * 50)
            for b in bids[:10]:
                dir_symbol = "↑" if "increase" in str(b.get("actions", [])) else "↓"
                print(f"  {dir_symbol} '{b['keyword']}' — ACoS {b['current_acos']:.0f}% | "
                      f"${b['current_spend']:.0f} → ${b['current_sales']:.0f}")
            print()

        # Promotions
        promos = plan.get("promotions", [])
        if promos:
            print("🔍 AUTO → MANUAL PROMOTIONS")
            print("-" * 50)
            for p in promos[:10]:
                print(f"  ⬆ '{p['keyword']}' — {p['orders']} orders, "
                      f"ACoS {p['acos']*100:.0f}%, ${p['sales']:.0f} sales")
            print()

        # Top scored attributes
        print("⭐ TOP SCORED ATTRIBUTES")
        print("-" * 65)
        attrs = plan.get("scored_attributes", [])[:15]
        print(f"  {'Attribute':<30} {'Cmpst':>6} {'Eff':>5} {'Vol':>5} {'Cnv':>5} {'Mom':>5} {'ACoS':>6}")
        print(f"  {'-'*30} {'-'*6} {'-'*5} {'-'*5} {'-'*5} {'-'*5} {'-'*6}")
        for a in attrs:
            name = a['attribute'][:28]
            print(f"  {name:<30} {a['composite']:>6.0f} {a['efficiency']:>5.0f} "
                  f"{a['volume']:>5.0f} {a['conversion']:>5.0f} {a['momentum']:>5.0f} "
                  f"{a['acos']:>5.0f}%")

# ═══════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="AAIE v2 — Max Automation Ads Engine")
    parser.add_argument("command", choices=["run", "plan", "mine", "score", "monitor", "promote"])
    parser.add_argument("--store", default="CUCZUUS", help="Store ID or ALL")
    parser.add_argument("--days", type=int, default=30, help="Analysis window")
    parser.add_argument("--json", action="store_true", help="Output JSON")

    args = parser.parse_args()
    engine = AdsIntelligentEngineV2()

    if args.command == "run" or args.command == "plan":
        if args.store == "ALL":
            results = engine.run_all_stores(args.days)
            if args.json:
                print(json.dumps(results, indent=2, default=str))
            else:
                for s, plan in results.items():
                    if "error" in plan:
                        print(f"\n❌ {s}: {plan['error']}")
                    else:
                        engine.print_report(plan)
        else:
            plan = engine.run(args.store, args.days)
            if args.json:
                print(json.dumps(plan, indent=2, default=str))
            else:
                engine.print_report(plan)

    elif args.command == "mine":
        attributes = engine.miner.mine_search_terms(args.store, args.days)
        print(f"Mined {len(attributes)} attributes from search terms")
        for a in attributes[:20]:
            print(f"  '{a.raw_query}' → root='{a.root_keyword}' "
                  f"mod='{a.modifier}' intent={a.intent_signal} "
                  f"campaign={a.campaign_type}")

    elif args.command == "score":
        attributes = engine.miner.mine_search_terms(args.store, args.days)
        scored = engine.scorer.score_attributes(attributes)
        print(f"Scored {len(scored)} attributes")
        for s in scored[:15]:
            print(f"  {s.attribute:<30} composite={s.composite_score:.0f} "
                  f"eff={s.efficiency_score:.0f} conv={s.conversion_score:.0f} "
                  f"actions={[a.value for a in s.recommended_actions]}")

    elif args.command == "monitor":
        monitors = engine.monitor.monitor_keywords(args.store, args.days)
        alerts = [m for m in monitors if m.alerts]
        print(f"Monitored {len(monitors)} keywords, {len(alerts)} with alerts")
        for m in alerts[:15]:
            print(f"  ⚠️ '{m.keyword}' — {m.alerts}")

    elif args.command == "promote":
        discoveries = engine.miner.mine_auto_campaigns(args.store, args.days)
        print(f"Found {len(discoveries)} keywords to promote from auto campaigns")
        for d in discoveries[:15]:
            print(f"  ⬆ '{d.keyword}' — {d.suggestion}")

if __name__ == "__main__":
    main()
