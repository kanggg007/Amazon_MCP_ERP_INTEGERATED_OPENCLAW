#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  DYNAMIC WEIGHT DERIVATION ENGINE — CCM (Category Conversion    ║
║  Multiplier) based structural weights from live search data     ║
║                                                                ║
║  Formula:                                                       ║
║    CCM[Category] = Avg CR of terms in Category                  ║
║                    ───────────────────────────                   ║
║                    Global Account Baseline CR                    ║
║                                                                ║
║  If global CR = 10% and Size terms average 14%:                ║
║    → Size Weight = 1.40 (40% above baseline)                   ║
║                                                                ║
║  Self-correcting: new product launches automatically shift      ║
║  weight hierarchy as search term data accumulates.              ║
╚══════════════════════════════════════════════════════════════════╝
"""

import re, json, logging
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Dict, List, Tuple, Optional
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from enum import Enum

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from sqlalchemy import func

BASE_DIR = Path(__file__).parent.parent
logger = logging.getLogger("ccm_engine")

# ═══════════════════════════════════════════════════════
# ATTRIBUTE CATEGORY DEFINITIONS
# ═══════════════════════════════════════════════════════

class AttributeCategory(Enum):
    """Structural attribute categories extracted from search terms."""
    SIZE = "size"               # Dimensions: 24x36, 48 inch, large
    SPATIAL = "spatial"         # Room/placement: bathroom, wall, vanity, gym
    MATERIAL = "material"       # Material: tempered glass, aluminum, wood
    COLOR = "color"             # Color: gold, black, silver, white
    STYLE = "style"             # Style: frameless, beveled, round, square
    QUALITY = "quality"         # Quality signals: HD, premium, professional, heavy duty
    FEATURE = "feature"         # Features: LED, lighted, magnifying, anti-fog
    FUNCTION = "function"       # Function: replacement, cover, lid, storage
    PRICE = "price"             # Price intent: cheap, affordable, luxury, premium
    BRAND = "brand"             # Brand names (ours + competitors)
    QUANTITY = "quantity"       # Quantity: 2 pack, set of 3, multi
    COMPATIBILITY = "compatibility"  # "fits X", "compatible with"
    CONDITION = "condition"     # new, used, refurbished
    SEASONAL = "seasonal"       # christmas, summer, holiday
    SHAPE = "shape"             # round, oval, rectangular, square

# ── Regex patterns for each category ──
CATEGORY_PATTERNS: Dict[AttributeCategory, re.Pattern] = {
    AttributeCategory.SIZE: re.compile(
        r'\b\d{1,2}\s*(?:inch|in|"|\'\'|cm|mm|feet|ft)\b|'
        r'\b\d{1,2}\s*x\s*\d{1,2}\b|'              # 24x36
        r'\b(?:small|medium|large|xl|xxl|mini|tiny|huge|big|'
        r'full\s*(?:length|size)|oversized|compact)\b',
        re.IGNORECASE
    ),
    AttributeCategory.SPATIAL: re.compile(
        r'\b(?:bathroom|vanity|wall\s*(?:mount|mounted)|kitchen|bedroom|'
        r'living\s*room|office|garage|gym|fitness|exercise|door|'
        r'ceiling|floor|corner|over\s*door|shower|powder\s*room)\b',
        re.IGNORECASE
    ),
    AttributeCategory.MATERIAL: re.compile(
        r'\b(?:glass|tempered\s*glass|stainless\s*steel|aluminum|'
        r'wood|wooden|plastic|acrylic|metal|copper|brass|iron|'
        r'silicone|rubber|ceramic|marble|bamboo|carbon\s*fiber)\b',
        re.IGNORECASE
    ),
    AttributeCategory.COLOR: re.compile(
        r'\b(?:black|white|silver|gold|golden|bronze|chrome|'
        r'rose\s*gold|nickel|brushed\s*nickel|oil\s*rubbed\s*bronze|'
        r'copper|red|blue|green|grey|gray|pink|beige|clear|'
        r'frosted|transparent|matte\s*(?:black|white)|glossy)\b',
        re.IGNORECASE
    ),
    AttributeCategory.STYLE: re.compile(
        r'\b(?:frameless|framed|beveled|round|circular|rectangle|'
        r'square|oval|arched|modern|contemporary|traditional|vintage|'
        r'rustic|industrial|minimalist|elegant|classic|retro|'
        r'farmhouse|mid\s*century|boho|scandinavian)\b',
        re.IGNORECASE
    ),
    AttributeCategory.QUALITY: re.compile(
        r'\b(?:hd|high\s*definition|premium|professional|commercial\s*grade|'
        r'heavy\s*duty|industrial\s*strength|pro\s*grade|luxury|'
        r'deluxe|superior|ultra|elite|premium\s*quality|best\s*quality)\b',
        re.IGNORECASE
    ),
    AttributeCategory.FEATURE: re.compile(
        r'\b(?:led|lighted|illuminated|backlit|magnifying|magnification|'
        r'anti\s*fog|fogless|waterproof|water\s*resistant|shatterproof|'
        r'breakproof|scratch\s*resistant|rust\s*proof|easy\s*clean|'
        r'self\s*cleaning|heated|dimmable|adjustable|extendable|foldable|'
        r'portable|rechargeable|wireless|bluetooth|smart)\b',
        re.IGNORECASE
    ),
    AttributeCategory.FUNCTION: re.compile(
        r'\b(?:replacement|cover|lid|top|cap|storage|organizer|'
        r'holder|stand|mount|hanger|hook|shelf|rack|tray|'
        r'divider|partition|insert|filter|pump|heater|thermostat|'
        r'feeder|cleaner|scrubber|polisher)\b',
        re.IGNORECASE
    ),
    AttributeCategory.PRICE: re.compile(
        r'\b(?:cheap|affordable|budget|inexpensive|value|'
        r'discount|sale|clearance|deal|best\s*price|'
        r'luxury|premium|expensive|high\s*end|designer)\b',
        re.IGNORECASE
    ),
    AttributeCategory.QUANTITY: re.compile(
        r'\b(?:\d+\s*pack|\d+\s*piece|\d+\s*set|pack\s*of\s*\d+|'
        r'set\s*of\s*\d+|bundle|multi\s*pack|twin\s*pack|'
        r'\d+\s*pcs|single|double|triple)\b',
        re.IGNORECASE
    ),
    AttributeCategory.COMPATIBILITY: re.compile(
        r'\b(?:fits|compatible\s*with|for\s*use\s*with|designed\s*for|'
        r'suitable\s*for|works\s*with|replaces|replacement\s*for|'
        r'attaches\s*to|connects\s*to)\b',
        re.IGNORECASE
    ),
    AttributeCategory.SHAPE: re.compile(
        r'\b(?:round|circle|circular|square|rectangle|rectangular|'
        r'oval|elliptical|triangle|triangular|hexagon|hexagonal|'
        r'octagon|arched|curve|curved|flat|concave|convex|'
        r'dome|domed|slanted|angled)\b',
        re.IGNORECASE
    ),
    AttributeCategory.SEASONAL: re.compile(
        r'\b(?:christmas|xmas|holiday|summer|winter|spring|fall|'
        r'autumn|thanksgiving|halloween|easter|valentine|'
        r'seasonal|festive|decoration|gift)\b',
        re.IGNORECASE
    ),
}


def classify_search_term(query: str) -> Dict[AttributeCategory, bool]:
    """
    Split a search term into its attribute categories.
    Returns dict with True for each category matched.

    Example:
      "round led bathroom mirror 24 inch gold frameless"
      → {SIZE: True, SPATIAL: True, COLOR: True, STYLE: True, FEATURE: True, SHAPE: True}
    """
    result = {}
    for category, pattern in CATEGORY_PATTERNS.items():
        result[category] = bool(pattern.search(query))
    return result


# ═══════════════════════════════════════════════════════
# CCM — Category Conversion Multiplier Engine
# ═══════════════════════════════════════════════════════

@dataclass
class CategoryStats:
    """Performance metrics for a single attribute category."""
    category: AttributeCategory
    term_count: int = 0
    total_impressions: int = 0
    total_clicks: int = 0
    total_spend: float = 0.0
    total_sales: float = 0.0
    total_orders: int = 0
    avg_cr: float = 0.0          # Average conversion rate
    avg_ctr: float = 0.0
    avg_acos: float = 0.0
    ccm: float = 1.0             # Category Conversion Multiplier
    weight_impact: float = 0.0   # How much this category shifts the score
    confidence: float = 0.0      # Based on sample size

@dataclass
class WeightSnapshot:
    """Complete weight state at a point in time."""
    store_id: str
    derived_at: date
    global_baseline_cr: float
    global_baseline_acos: float
    total_terms_analyzed: int
    categories: Dict[str, CategoryStats] = field(default_factory=dict)

    def get_weight(self, category: AttributeCategory) -> float:
        """Get the CCM weight for a category (defaults to 1.0)."""
        key = category.value
        if key in self.categories:
            return self.categories[key].ccm
        return 1.0

    def to_dict(self) -> dict:
        return {
            "store_id": self.store_id,
            "derived_at": self.derived_at.isoformat(),
            "global_baseline_cr": round(self.global_baseline_cr, 4),
            "global_baseline_acos": round(self.global_baseline_acos, 4),
            "total_terms_analyzed": self.total_terms_analyzed,
            "categories": {
                k: {
                    "category": v.category.value,
                    "term_count": v.term_count,
                    "avg_cr": round(v.avg_cr, 4),
                    "avg_acos": round(v.avg_acos, 4),
                    "ccm": round(v.ccm, 2),
                    "weight_impact": round(v.weight_impact, 2),
                    "confidence": round(v.confidence, 2),
                    "total_spend": round(v.total_spend, 2),
                    "total_sales": round(v.total_sales, 2),
                    "total_orders": v.total_orders,
                }
                for k, v in self.categories.items()
            }
        }


class DynamicWeightEngine:
    """
    Calculates Category Conversion Multipliers from live search term data.

    Pipeline:
      1. Load all search terms from search_terms_history
      2. Classify each term into attribute categories (SIZE, COLOR, etc.)
      3. Calculate avg CR per category
      4. Calculate global baseline CR
      5. CCM = Category CR / Global Baseline CR
      6. Save weights for use in attribute scoring

    Runs every 14 days (configurable).
    """

    MIN_TERM_COUNT = 10  # Minimum terms needed for reliable CCM

    def __init__(self, db_session):
        self.db = db_session
        from db.models import SearchTermHistory
        self.SearchTermHistory = SearchTermHistory

    def calculate_global_baseline(self, store_id: str,
                                   days: int = 90) -> Tuple[float, float, int]:
        """
        Calculate global account baseline conversion rate and ACoS.

        Returns: (baseline_cr, baseline_acos, total_terms)
        """
        rows = self.db.query(
            func.sum(self.SearchTermHistory.clicks).label("total_clicks"),
            func.sum(self.SearchTermHistory.orders).label("total_orders"),
            func.sum(self.SearchTermHistory.spend).label("total_spend"),
            func.sum(self.SearchTermHistory.sales).label("total_sales"),
            func.count(self.SearchTermHistory.id).label("term_count"),
        ).filter(
            self.SearchTermHistory.store_id == store_id,
            self.SearchTermHistory.date >= date.today() - timedelta(days=days),
            self.SearchTermHistory.clicks > 0,
        ).first()

        if not rows or not rows.total_clicks:
            return 0.10, 0.15, 0  # Defaults

        baseline_cr = float(rows.total_orders or 0) / float(rows.total_clicks or 1)
        baseline_acos = float(rows.total_spend or 0) / float(rows.total_sales or 1)
        return baseline_cr, baseline_acos, int(rows.term_count or 0)

    def calculate_category_stats(self, store_id: str,
                                  days: int = 90) -> Dict[AttributeCategory, CategoryStats]:
        """
        For each attribute category, calculate avg CR, CTR, ACoS.

        Groups search terms by detected attribute categories and computes
        aggregate performance per category.
        """
        # Load all search terms
        rows = self.db.query(
            self.SearchTermHistory.search_term,
            self.SearchTermHistory.clicks,
            self.SearchTermHistory.orders,
            self.SearchTermHistory.impressions,
            self.SearchTermHistory.spend,
            self.SearchTermHistory.sales,
        ).filter(
            self.SearchTermHistory.store_id == store_id,
            self.SearchTermHistory.date >= date.today() - timedelta(days=days),
            self.SearchTermHistory.clicks > 0,
        ).all()

        if not rows:
            return {}

        # Accumulate per category
        cat_data: Dict[AttributeCategory, dict] = defaultdict(lambda: {
            "terms": set(),
            "clicks": 0, "orders": 0, "impressions": 0,
            "spend": 0.0, "sales": 0.0,
        })

        for row in rows:
            query = row.search_term or ""
            classifications = classify_search_term(query)

            for cat, matched in classifications.items():
                if matched:
                    d = cat_data[cat]
                    d["terms"].add(query)
                    d["clicks"] += int(row.clicks or 0)
                    d["orders"] += int(row.orders or 0)
                    d["impressions"] += int(row.impressions or 0)
                    d["spend"] += float(row.spend or 0)
                    d["sales"] += float(row.sales or 0)

        # Build stats per category
        stats = {}
        for cat, d in cat_data.items():
            cl = d["clicks"]
            ors = d["orders"]
            sp = d["spend"]
            sl = d["sales"]
            imp = d["impressions"]

            avg_cr = ors / cl if cl > 0 else 0
            avg_ctr = cl / imp if imp > 0 else 0
            avg_acos = sp / sl if sl > 0 else (float("inf") if sp > 0 else 0)

            stats[cat] = CategoryStats(
                category=cat,
                term_count=len(d["terms"]),
                total_impressions=imp,
                total_clicks=cl,
                total_spend=round(sp, 2),
                total_sales=round(sl, 2),
                total_orders=ors,
                avg_cr=round(avg_cr, 4),
                avg_ctr=round(avg_ctr, 4),
                avg_acos=round(avg_acos, 4) if avg_acos != float("inf") else 0,
                ccm=1.0,  # Will be calculated next
            )

        return stats

    def derive_weights(self, store_id: str, days: int = 90) -> WeightSnapshot:
        """
        THE CORE ALGORITHM — Derive structural weights from live data.

        For each attribute category:
          CCM[Category] = Avg CR of terms in Category
                          ───────────────────────────
                          Global Account Baseline CR

        Example:
          Global CR = 10%
          Size terms CR = 14%  → CCM[Size] = 1.40
          Color terms CR = 4%  → CCM[Color] = 0.40
          The engine automatically weights Size higher than Color.

        Self-correction:
          If a new product launches where Color matters more,
          the incoming search data will shift the CR ratios,
          and the next 14-day cycle recalculates automatically.
        """
        logger.info("Deriving dynamic weights for %s (%d days)", store_id, days)

        # Step 1: Global baseline
        baseline_cr, baseline_acos, total_terms = self.calculate_global_baseline(store_id, days)
        logger.info("  Global baseline: CR=%.2f%%, ACoS=%.2f%%, %d terms",
                    baseline_cr * 100, baseline_acos * 100, total_terms)

        # Step 2: Category performance
        cat_stats = self.calculate_category_stats(store_id, days)
        logger.info("  Categories found: %d", len(cat_stats))

        # Step 3: Calculate CCM for each category
        for cat, stats in cat_stats.items():
            if baseline_cr > 0 and stats.avg_cr > 0:
                stats.ccm = round(stats.avg_cr / baseline_cr, 2)
            else:
                stats.ccm = 1.0  # Default: no impact if insufficient data

            # Confidence based on term count
            stats.confidence = round(min(1.0, stats.term_count / 50), 2)

            # Weight impact: how much this category moves the needle
            stats.weight_impact = round(stats.ccm - 1.0, 2)

            logger.info("    %-15s: CR=%.2f%%  CCM=%.2f  confidence=%.0f%%  %d terms",
                        cat.value, stats.avg_cr * 100, stats.ccm,
                        stats.confidence * 100, stats.term_count)

        # Step 4: Build snapshot
        return WeightSnapshot(
            store_id=store_id,
            derived_at=date.today(),
            global_baseline_cr=round(baseline_cr, 4),
            global_baseline_acos=round(baseline_acos, 4),
            total_terms_analyzed=total_terms,
            categories={cat.value: stats for cat, stats in cat_stats.items()},
        )

    def save_weights(self, snapshot: WeightSnapshot):
        """Save derived weights to attribute_weights table."""
        from db.models import AttributeWeight

        for cat_key, stats in snapshot.categories.items():
            # Map each category CCM to the scoring dimensions
            # Categories influence multiple scoring dimensions

            # Size/Spatial → Volume score
            # Quality/Material/Feature → Efficiency score
            # Color/Style → Intent score
            # Function/Compatibility → Conversion score

            existing = self.db.query(AttributeWeight).filter_by(
                store_id=snapshot.store_id,
                dimension=f"category_{cat_key}",
                derived_at=snapshot.derived_at,
            ).first()

            if existing:
                existing.weight = stats.ccm
                existing.baseline_value = snapshot.global_baseline_cr
                existing.confidence = stats.confidence
            else:
                self.db.add(AttributeWeight(
                    store_id=snapshot.store_id,
                    dimension=f"category_{cat_key}",
                    weight=stats.ccm,
                    baseline_value=snapshot.global_baseline_cr,
                    confidence=stats.confidence,
                    derived_at=snapshot.derived_at,
                ))

        self.db.commit()
        logger.info("  Saved %d category weights for %s",
                    len(snapshot.categories), snapshot.store_id)

    def apply_weights_to_scoring(self, snapshot: WeightSnapshot,
                                  score_dimensions: Dict[str, float]) -> Dict[str, float]:
        """
        Apply derived category weights to adjust scoring dimension weights.

        The base scoring dimensions (efficiency, conversion, etc.) are
        ADJUSTED by the category CCMs based on what categories appear
        in the search term.

        For example:
          Base: efficiency=0.30, conversion=0.20, volume=0.10
          Search term contains SIZE attributes:
            → volume_score *= CCM[Size] (e.g., 1.40)
          Search term contains QUALITY attributes:
            → efficiency_score *= CCM[Quality] (e.g., 0.50)

        This means the engine automatically knows:
          "For this product, size matters more than stated quality"
        """
        adjusted = dict(score_dimensions)

        # Category → scoring dimension mapping
        category_dimension_map = {
            "size": "volume",
            "spatial": "volume",
            "material": "efficiency",
            "color": "intent",
            "style": "intent",
            "quality": "efficiency",
            "feature": "conversion",
            "function": "conversion",
            "price": "efficiency",
            "quantity": "volume",
            "compatibility": "conversion",
            "shape": "intent",
            "seasonal": "momentum",
        }

        for cat_key, stats in snapshot.categories.items():
            if stats.confidence < 0.3:  # Skip low-confidence categories
                continue
            dim = category_dimension_map.get(cat_key)
            if dim and dim in adjusted:
                # Apply CCM as a multiplier, clamped to reasonable range
                multiplier = min(2.0, max(0.3, stats.ccm))
                adjusted[dim] = round(adjusted[dim] * multiplier, 4)

        # Re-normalize to sum to 1.0
        total = sum(adjusted.values())
        if total > 0:
            adjusted = {k: round(v / total, 4) for k, v in adjusted.items()}

        return adjusted


# ═══════════════════════════════════════════════════════
# DEMO: Run with synthetic data to show the algorithm
# ═══════════════════════════════════════════════════════

def demo():
    """Demonstrate the CCM engine with realistic mirror product data."""
    print("""
╔══════════════════════════════════════════════════════════════════╗
║  CCM WEIGHT DERIVATION — Live Demonstration                      ║
╚══════════════════════════════════════════════════════════════════╝
""")

    # Simulated search term data
    sample_terms = [
        # SIZE terms (high CR)
        ("round bathroom mirror 24 inch", 200, 30, 200, 4000),     # 15% CR
        ("36 x 48 frameless gym mirror", 150, 24, 180, 3200),      # 16% CR
        ("large vanity mirror 30 inch", 180, 25, 220, 3600),       # 14% CR
        ("full length mirror 48 inch", 300, 42, 350, 5000),        # 14% CR

        # COLOR terms (lower CR)
        ("gold bathroom mirror", 200, 12, 180, 2800),              # 6% CR
        ("black framed mirror round", 180, 9, 160, 2000),          # 5% CR
        ("silver mirror modern", 150, 6, 140, 1500),               # 4% CR
        ("bronze vanity mirror", 120, 6, 100, 1200),               # 5% CR

        # QUALITY terms (mixed)
        ("hd glass mirror premium", 80, 4, 90, 1200),              # 5% CR
        ("professional grade mirror", 60, 2, 70, 800),             # 3% CR

        # FEATURE terms (high CR for LED)
        ("led bathroom mirror 24 inch", 250, 45, 280, 6000),       # 18% CR
        ("lighted wall mirror gold", 200, 32, 240, 4800),          # 16% CR
        ("anti fog bathroom mirror", 150, 20, 180, 3000),          # 13% CR

        # SPATIAL terms (high volume)
        ("bathroom mirror with lights", 400, 48, 350, 5500),       # 12% CR
        ("vanity mirror wall mounted", 350, 38, 300, 4500),        # 11% CR
        ("gym mirror for home", 250, 30, 220, 3800),               # 12% CR
    ]

    # Calculate per-category stats
    cat_stats = defaultdict(lambda: {"clicks": 0, "orders": 0, "terms": 0})

    for query, clicks, orders, spend, sales in sample_terms:
        classifications = classify_search_term(query)
        for cat, matched in classifications.items():
            if matched:
                cat_stats[cat]["clicks"] += clicks
                cat_stats[cat]["orders"] += orders
                cat_stats[cat]["terms"] += 1

    # Global baseline
    total_clicks = sum(c for _, c, _, _, _ in sample_terms)
    total_orders = sum(o for _, _, o, _, _ in sample_terms)
    global_cr = total_orders / total_clicks  # ~12.3%

    print(f"  Global Baseline CR: {global_cr*100:.1f}% ({total_clicks} clicks, {total_orders} orders)")
    print()
    print(f"  {'Category':<15} {'Terms':>6} {'CR':>8} {'CCM':>8} {'Impact':>10} {'Interpretation'}")
    print(f"  {'─'*15} {'─'*6} {'─'*8} {'─'*8} {'─'*10} {'─'*30}")

    results = []
    for cat, d in sorted(cat_stats.items(), key=lambda x: x[1]["orders"]/max(1,x[1]["clicks"]), reverse=True):
        if d["terms"] < 3:
            continue
        cr = d["orders"] / d["clicks"]
        ccm = cr / global_cr
        impact = ccm - 1.0

        if ccm > 1.30:
            interp = "↑ STRONG — boost this attribute"
        elif ccm > 1.10:
            interp = "↗ POSITIVE — moderate boost"
        elif ccm > 0.90:
            interp = "→ NEUTRAL — baseline"
        elif ccm > 0.50:
            interp = "↘ WEAK — deprioritize"
        else:
            interp = "↓ VERY WEAK — may be negative signal"

        results.append((cat.value, d["terms"], cr, ccm, impact, interp))
        print(f"  {cat.value:<15} {d['terms']:>6} {cr*100:>7.1f}% {ccm:>7.2f}x {impact:>+9.2f}  {interp}")

    print()
    print("  ── Application ──")
    top = sorted(results, key=lambda x: x[3], reverse=True)
    print(f"  1. Top attribute: '{top[0][0]}' (CCM={top[0][3]:.2f}x) → scale bids on terms with this attribute")
    print(f"  2. Bottom attribute: '{top[-1][0]}' (CCM={top[-1][3]:.2f}x) → deprioritize, reduce bids")
    print(f"  3. Self-correcting: if new LED mirror product shifts COLOR performance up,")
    print(f"     next 14-day cycle auto-reweights COLOR higher")
    print()
    print(f"  ✅ Engine ready. Runs every 14 days via: python3 engines/ads_pipeline.py weight")


if __name__ == "__main__":
    demo()
