# AAIE Campaign Audit: 斜边镜自动 (Beveled Mirror Auto)
**Date:** 2026-06-24 | **Data:** May 2026, 14-day attribution | **Verdict:** 🔴 CRITICAL — PAUSE & RESTRUCTURE

---

## 1. EXECUTIVE SUMMARY

| Metric | Value | Verdict |
|--------|-------|---------|
| Spend | **$245.71** | High for a single SKU |
| Sales (14d) | **$415.87** | Below breakeven |
| Orders | **13** | 2.6/week — extremely thin |
| ACoS | **59.1%** | 🔴 Deep red zone |
| CPC | $0.64 | OK for category |
| CTR | 15.5% | Strong! People are clicking |
| CVR | 3.4% | Low — wrong traffic |

**The brutal truth:** 92.2% of every dollar spent ($226.54 / $245.71) goes to search queries that NEVER convert. Only 12 out of 283 targeting expressions produce sales — and those 12 are stellar at 4.6% ACoS.

---

## 2. DATA STRUCTURE NOTE

The search term report (`CUCZUUS_US_2026-05.json`) contains 283 entries for this campaign, all with `matchType: TARGETING_EXPRESSION_PREDEFINED` (Amazon auto-targeting: Close Match, Loose Match, Substitutes, Complements). **The actual search query text is NOT present** in this export — only the targeting expression metadata, cost, clicks, impressions, and attributed sales per entry.

This limits our ability to recommend specific negative keywords from this dataset, but we can still draw strong conclusions from the performance patterns.

---

## 3. FULL BREAKDOWN

### 3.1 Category Distribution

| Category | Count | Spend | Sales | Orders | ACoS |
|----------|-------|-------|-------|--------|------|
| 🏆 WINNERS (ACoS <25%, orders>0) | 12 | $19.17 | $415.87 | 13 | **4.6%** |
| 🩸 BLEEDERS (spend>$0, zero sales) | 271 | $226.54 | $0.00 | 0 | ∞ |
| ⚖️ BREAKEVEN (ACoS 25-50%) | 0 | $0.00 | $0.00 | 0 | — |
| 💀 LOSERS (ACoS >50%) | 0 | $0.00 | $0.00 | 0 | — |

**This is a binary campaign: pure winners vs. pure bleeders. Nothing in between.**

### 3.2 The 12 Winners (All-Star Performers)

Every winner converts at $31.99/sale (single SKU at fixed price). ACoS range: **1.8% – 12.0%** — every single one is profitable.

| # | Spend | Sales | Orders | Clicks | ACoS | ROAS |
|---|-------|-------|--------|--------|------|------|
| 1 | $6.23 | $63.98 | 2 | 8 | 9.7% | 10.3× |
| 2 | $3.83 | $31.99 | 1 | 7 | 12.0% | 8.4× |
| 3 | $2.40 | $31.99 | 1 | 3 | 7.5% | 13.3× |
| 4 | $1.60 | $31.99 | 1 | 2 | 5.0% | 20.0× |
| 5 | $0.80 | $31.99 | 1 | 1 | 2.5% | 40.0× |
| 6 | $0.77 | $31.99 | 1 | 1 | 2.4% | 41.5× |
| 7 | $0.60 | $31.99 | 1 | 1 | 1.9% | 53.3× |
| 8 | $0.60 | $31.99 | 1 | 1 | 1.9% | 53.3× |
| 9 | $0.60 | $31.99 | 1 | 1 | 1.9% | 53.3× |
| 10 | $0.60 | $31.99 | 1 | 1 | 1.9% | 53.3× |
| 11 | $0.57 | $31.99 | 1 | 1 | 1.8% | 56.1× |
| 12 | $0.57 | $31.99 | 1 | 1 | 1.8% | 56.1× |

**Pattern:** 8 of 12 winners required only 1 click to convert. The winners are high-intent buyers who know what they want.

### 3.3 The 271 Bleeders — Where the Money Dies

**Top 10 worst offenders:**

| # | Spend Wasted | Clicks | Impressions | CPC |
|---|-------------|--------|-------------|-----|
| 1 | **$9.60** | 12 | 233 | $0.80 |
| 2 | **$8.00** | 10 | 448 | $0.80 |
| 3 | $4.80 | 6 | 76 | $0.80 |
| 4 | $4.80 | 8 | 207 | $0.60 |
| 5 | $4.53 | 8 | 88 | $0.57 |
| 6 | $3.89 | 5 | 27 | $0.78 |
| 7 | $3.00 | 5 | 34 | $0.60 |
| 8 | $2.40 | 3 | 16 | $0.80 |
| 9 | $2.40 | 3 | 16 | $0.80 |
| 10 | $2.40 | 3 | 8 | $0.80 |

**Concentration of damage:**
- Top 10 bleeders (3.7% of bleeders): **$45.82 = 20.2%** of total bleed
- Top 27 bleeders (10%): **$71.67 = 31.6%**
- Top 67 bleeders (25%): **$106.40 = 47.0%**

**Key insight:** 236 out of 271 bleeders are single-click wastes at $0.57-$0.80 CPC. These are likely broad/loose match queries where Amazon is casting too wide a net.

---

## 4. TREND ANALYSIS: Getting Worse

| Month | Spend | Sales (30d) | Orders | ACoS (30d) | Trend |
|-------|-------|-------------|--------|------------|-------|
| Mar 2026 | $215.80 | $517.89 | 10 | 41.7% | ⚠️ |
| Apr 2026 | $226.62 | $389.90 | 10 | 58.1% | 🔴 |
| May 2026 | $245.71 | $850.78 | 21 | 28.9% | 🟡 |

**Note:** 30-day attribution shows May at 28.9% ACoS (looks OK) vs. 14-day at 59.1% (bleeding). The 30d figure captures delayed conversions. But the campaign has been deteriorating since March, and spending keeps rising while order volume barely grew.

---

## 5. ROOT CAUSE DIAGNOSIS

1. **Auto-targeting gone wild:** `TARGETING_EXPRESSION_PREDEFINED` means Amazon's algorithm is testing Close Match + Loose Match + Substitutes + Complements. The vast majority of tested queries are irrelevant.

2. **No negative keyword defense:** Without negatives, Amazon keeps spending on the same non-converting queries. The 96 entries at $0.57 CPC and 91 at $0.60 CPC suggest repeated spend on the same bad search terms.

3. **Winner signal is clear but thin:** 12 winning queries convert at 4.6% ACoS. These are likely exact brand/product matches. The auto campaign is failing to find more of these.

4. **High CTR + Low CVR = Wrong traffic:** 15.5% CTR is strong for auto campaigns, but 3.4% CVR means the clicks come from browsers, not buyers.

---

## 6. ACTION PLAN

### ⚡ IMMEDIATE (This Week)

#### A. Download Proper Search Term Report
**The current export lacks search query text.** You MUST download the Amazon Search Term Report with the `query`/`keyword` column enabled. Without it, we cannot add precise negative keywords. In Seller Central: Reports → Advertising Reports → Search Term Report → include "Customer Search Term" column.

#### B. Reduce Daily Budget by 70%
Current spend: ~$8.19/day. Cut to **$2.50/day**. This still allows Amazon to find winning queries while capping bleed.

```
Old daily budget: $10.00 (estimated)
New daily budget: $3.00
Expected monthly spend: ~$90
Expected sales (proportional): ~$152
Expected ACoS: ~59% (unchanged without structural fixes)
```

**But:** a random budget cut may also cut winners. Better approach below.

### 🔧 SHORT-TERM (1-2 Weeks)

#### C. Extract Winning Queries → Create Manual Campaign
Once you have the search term report with actual query text:
1. Extract the 12 converting search queries
2. Create a **Manual Targeting campaign** using these as **Exact Match** keywords
3. Set bids at $0.60-$0.80 (validated by winner CPCs)
4. Set daily budget at $5.00
5. Expected: $19 spend → $416 sales at 4.6% ACoS

#### D. Add Negative Keywords to Auto Campaign
From the search term report, identify the 271 non-converting queries:
- **Exact Negative:** Any query that spent >$2 with 0 sales (top bleeders — ~27 terms)
- **Phrase Negative:** Generic/irrelevant root words that appear across multiple bleeders (e.g., if "cheap ___ mirror" keeps appearing)
- Add these as campaign-level negatives to 斜边镜自动

#### E. Disable Loose Match & Substitutes (if auto campaign structure allows)
In the auto campaign settings, turn off:
- **Loose Match** — highest bleed candidate
- **Substitutes** — showing on competitor detail pages without conversion

Keep:
- **Close Match** — likely where winners come from
- Optionally test **Complements**

### 📊 MEDIUM-TERM (2-4 Weeks)

#### F. Restructure to Manual-First Architecture
Based on the CUCZUUS CA market (which has 斜边镜手动広告), migrate to:

```
斜边镜 Manual — Exact (NEW)
├── Keyword Group 1: Winning search terms (exact match)
├── Keyword Group 2: Product ASIN targeting (top competitors)
└── Budget: $5/day

斜边镜 Auto — Discovery (RESTRUCTURED)
├── Close Match ONLY (disable loose/substitutes/complements)
├── Budget: $3/day (down from current)
├── Weekly search term mining → promote winners to Manual
└── Weekly negative keyword refresh
```

#### G. Consider Pausing Entirely
If after 2 weeks of restructuring the ACoS doesn't drop below 35%, **pause the campaign entirely** and redirect budget to:
- 鱼缸盖自动 ($177 → $4,038, 4.4% effective ACoS across campaign-level)
- 491商品定位 ($113 → $2,052, 5.5%)

The beveled mirror product may simply have poor unit economics for paid search vs. the fish tank lid products.

---

## 7. KEY METRICS TO TRACK

| KPI | Current | Target | How to Measure |
|-----|---------|--------|----------------|
| Campaign ACoS (14d) | 59.1% | <35% | Weekly search term pull |
| Bleeder % of spend | 92.2% | <50% | Search term report |
| Orders/week | 2.6 | >5 | Campaign dashboard |
| Negative keyword count | 0 | 50+ | Campaign settings |
| CPC | $0.64 | $0.55-$0.65 | Keep steady — CPC isn't the problem |

---

## 8. BOTTOM LINE

> **Kang, the auto campaign is burning $226/month on noise to find $19 worth of signal.** The winning queries are excellent (4.6% ACoS) but they're drowning in wasted spend.
>
> **Immediate action:** Cut the auto campaign budget to $3/day. Download the full search term report. This week: build a Manual Exact campaign from the 12 proven winners and neg out the top 27 bleeders.
>
> **If that doesn't work in 2 weeks:** Kill it. The fish tank lid campaigns are printing money. Feed the winners, starve the losers.

---

*AAIE Audit ID: BEVELED-MIRROR-2026-05 | Generated: 2026-06-24 16:00 CST*
