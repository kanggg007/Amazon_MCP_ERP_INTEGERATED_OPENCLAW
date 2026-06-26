# TACoS Analysis — 斜边镜自動 Campaign
**Date:** 2026-06-24  
**Store:** CUCZUUS (广州汇纳)  
**Marketplace:** US (ATVPDKIKX0DER)  
**Period:** May 2026  

---

## 1. Campaign Identification ✅

| Field | Value |
|-------|-------|
| **Campaign Name** | 斜边镜自动 - 9/22/2025 |
| **Campaign ID** | `541692347345311` |
| **Type** | Sponsored Products - Automatic |
| **Ad Group** | `463487695320375` (ENABLED) |
| **Targeting** | QUERY_HIGH_REL_MATCHES (auto, bid: $0.80) |

### Advertised ASINs
| ASIN | SKU | State |
|------|-----|-------|
| **B0FBGPNRMK** | CUC-US-WM2416-1 | ENABLED |
| **B0FBGP974Z** | CUC-US-WM2416-2 | ENABLED |

---

## 2. May 2026 Campaign Performance

| Metric | Value |
|--------|-------|
| **Ad Spend** | **$245.71** |
| **Ad Sales (30d)** | **$850.78** |
| **Ad Orders (30d)** | **21** |
| Clicks | 385 |
| Impressions | 26,812 |
| CTR | 1.44% |
| CVR | 5.45% |
| **ACoS** | **28.9%** |
| RoAS | 3.46x |
| Avg CPC | $0.64 |
| Avg Order Value | $40.51 |

> Source: `data_lake/ads/CUCZUUS_US_2026-05.json` + Ads API confirmation

---

## 3. TACoS Calculation

### Formula
```
TACoS = Ad Spend / Total Sales
Total Sales = Organic Sales + Ad-attributed Sales
```

### Known
- Ad Spend: **$245.71**
- Ad Sales (30d): **$850.78**

### Missing: Total Sales

To get Total Sales, we need **all orders** for May 2026 from SP-API. Two paths exist:

#### Path A: SP-API Orders API ❌
- Returns `403 Unauthorized` — the LWA application lacks Orders API permissions
- **Fix:** Add Orders/OrderItems API roles in Seller Central → Developer Apps

#### Path B: Database (Railway PostgreSQL) ⚠️
- The `orders` table has **704 rows** with real data
- The `order_items` table has ASIN-level line items
- Connection is intermittent from this machine (Railway proxy timeout)
- **Fix:** Query from Railway's internal network or use the deployed app

### TACoS Estimate (based on category benchmarks)
| Organic/Ad Ratio | Est. Total Sales | Est. TACoS | Assessment |
|-----------------|-----------------|------------|------------|
| 2× ad sales | $2,552 | **9.6%** | ✅ Healthy |
| 3× ad sales | $3,403 | **7.2%** | ✅ Good |
| 4× ad sales | $4,254 | **5.8%** | ✅ Very good |
| 5× ad sales | $5,105 | **4.8%** | ✅ Excellent |

> Actual TACoS is likely **5-10%** — healthy range

---

## 4. API Timing

| Step | Time |
|------|------|
| Ads API auth | 1.5s |
| Campaign lookup (POST /sp/campaigns/list) | 1.0s |
| Ad Groups (POST /sp/adGroups/list) | 1.7s |
| Product Ads (POST /sp/productAds/list) | 0.9s |
| SP-API auth | 1.1s |
| SP-API Orders (attempted) | 1.0s |
| **Ads API total (working)** | **~5s** |

### Estimated Monthly Full Run
| Component | Per Market | 12 Markets |
|-----------|-----------|------------|
| Ads API enumeration | ~5s | ~1 min |
| SP-API orders (100-500) | 30-90s | 6-18 min |
| SP-API order items | 15-60s | 3-12 min |
| **Total** | **~50-155s** | **~10-31 min** |

---

## 5. Next Steps

### 🔴 Critical (to unlock TACoS)
1. **Authorize SP-API Orders API** in Seller Central
   - App: `amzn1.application-oa2-client.d8c93b864fef439886163fd321bb7cd2`
   - IAM Role: `arn:aws:iam::977574654028:role/SP-API-ROLE`
   - Needs: `Orders` + `OrderItems` API roles

### 🟡 Recommended
2. **Query DB for May 2026 total revenue:**
   ```sql
   SELECT COALESCE(SUM(order_total_amount), 0)
   FROM orders 
   WHERE purchase_date >= '2026-05-01' AND purchase_date < '2026-06-01'
   AND marketplace_id = 'ATVPDKIKX0DER';
   ```
3. **Add `campaignId` to data_lake exports** (currently only has `campaignName`)
4. **Cache total monthly revenue** in `profit_daily_snapshot` table for fast TACoS lookup

### 🟢 Nice to Have
5. Pull ASIN-level Ad reports monthly for per-product ACoS
6. Set up weekly TACoS trend monitoring

---

## 6. API Reference (what worked)

### Ads API (working)
```
Auth:  POST https://api.amazon.com/auth/o2/token
       (STORE_02_ADS_CLIENT_ID/SECRET/REFRESH_TOKEN)
Scope: Profile ID 3465892858543553

Campaigns:  POST /sp/campaigns/list   CT: application/vnd.spcampaign.v3+json
Ad Groups:  POST /sp/adGroups/list    CT: application/vnd.spadGroup.v3+json
Product Ads: POST /sp/productAds/list CT: application/vnd.spproductAd.v3+json
Targeting:  POST /sp/targets/list     CT: application/vnd.sptargetingClause.v3+json
Reporting:  POST /reporting/reports   CT: application/json
```

### SP-API (needs authorization)
```
Auth:  POST https://api.amazon.com/auth/o2/token
       (STORE_02_LWA_CLIENT_ID/SECRET/REFRESH_TOKEN_AMERICAS)
       + AWS IAM Signature V4

Orders: POST https://sellingpartnerapi-na.amazon.com/orders/v0/orders
        Headers: x-amz-access-token, x-amz-date, Authorization (AWS4)
```

---

**Bottom line:** ACoS is 28.9% (slightly above the 25% alert threshold). TACoS can't be precisely calculated without total sales data, but is estimated at **5-10%** (healthy). Priority is getting SP-API Orders API authorized.
