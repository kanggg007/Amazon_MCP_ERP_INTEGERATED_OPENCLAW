# Session Summary — June 10, 2026

## Major Fixes Applied

### 1. FBA Returns — Market-Specific Splitting
- NA returns report covers US+CA combined → split by FC location
- CA FCs: YYZ,YVR,YEG,YOO,YOW,YGK,YYC,YXU,YHZ,YQB
- AU/JP returns need SEPARATE reports via FE endpoint
- Returns counted by return date (May), NOT original order date
- Return deduction: return_count × avg_price × 0.85

### 2. CA Fulfillment Rate Card — CORRECTED
- Old code had wrong thresholds (lng≤61cm for Standard)
- CORRECT CA rate card:
  - Envelope: lng≤38, snd≤27, sht≤2, kg≤0.5
  - Standard: lng≤45, snd≤35, sht≤20, kg≤9
  - Small Oversize: lng≤152, snd≤76, kg≤32, L+G≤330 → 15.43 + per 500g×0.46
  - Medium Oversize: lng≤270, kg≤68, L+G≤330 → 37.78 + per 500g×0.52
  - Large Oversize → 82.20 + per 500g×0.58

### 3. Revenue Method — ORDER LINE PRICE
- Revenue = sum of item-price per order line (NOT price×qty)
- COGS/freight units = sum of qty (physical units)
- Referral = 15% of order line price (NOT per-unit avg)
- Mode price ABANDONED — use actual order data

### 4. FX Rates — Sina Finance (China-accessible)
- Primary source: Sina Finance (hq.sinajs.cn)
- CNY→CAD: 0.2059 (spot) | May avg: TBD
- CNY→USD: 0.1477 (spot)
- CNY→AUD: 0.2102 (spot)
- CNY→JPY: 23.67 (spot)
- Module: amazon-ops-intel/fx_rates.py
- ONLY Sina rates, no derived/USD cross rates

### 5. Sea Freight Formulas (CNY)
- US: weight(kg) × 5 CNY per unit
- CA: CBM × 1200 CNY per unit  
- AU: CBM × 1200 CNY per unit
- JP: CBM × 900 CNY per unit
- DE: CBM × 1500 CNY per unit
All converted to market currency via Sina FX rate

### 6. Store Name
- CUCZUUS (was TeaFruitix) — updated in spreadsheet

### 7. FBA Returns Report Columns
- return-date, order-id, sku, asin, fnsku, product-name, quantity
- fulfillment-center-id, detailed-disposition, reason, status
- license-plate-number, customer-comments
- NO refund amount — units only

## CUCZUUS CA Final P&L (May 2026)
- Revenue: CAD$29,134 (USD$22,142) | 451 units | 424 orders | 28 returns
- Fulfillment: -USD$7,670 (34.6%)
- Referral: -USD$3,321 (15.0%)
- COGS: -USD$5,487 (24.8%)
- Sea freight: -USD$1,611 (7.3%)
- Returns: -USD$1,819 (8.2%)
- Ads: -USD$1,352 (6.1%)
- Storage: -USD$423 (1.9%)
- TRUE PROFIT: $459 (2.1%)

## Improvement Actions for CA
1. Remove 3 loser ASINs (B0FB2KHP6V, B0FB2LFB3T, B0FB8LNHT3) → saves $711/mo
2. Fix B0FB2LFB3T quality (9 QUALITY_UNACCEPTABLE returns on 16 units)
3. Price bumps: B0FBG5YSPG $93→$110, B0FDGDHBBR $48→$55, B0GCZS1SSN $40→$47
4. Shrink packaging below 45cm to drop fulfillment tier
5. CA ads at 6.1% ACOS — highest of all markets

## Blocked: Ads API Campaign Data
- Profiles endpoint works (Bearer token) — shows total spend
- Campaign/list endpoint returns 401/403 — needs SP-API auth + IAM role fix
- Kang added IAM inline policy but still failing
- Need: link advertising role to SP-API app in Seller Central

## Next Session
- Fix Ads API campaign access (IAM/SP-API role linking)
- Pull campaign-level ad data (spend, impressions, clicks, ACOS by ASIN)
- Apply corrections to ALL stores × markets
- Complete BOOLUU P&L with same methodology
- Pull AU/JP FBA returns reports (FE endpoint)
- JP COGS still needs updating for B0DNJWRDP3
- Disposal fees — to be added as expense category
- Heliumx store (not started)
