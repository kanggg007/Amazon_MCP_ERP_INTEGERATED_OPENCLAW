#!/usr/bin/env python3
"""
TACoS Analysis for 斜边镜自動 - 9/22/2025 campaign (CUCZUUS US, May 2026)
Steps:
1. Get Ads API access token
2. Find campaign ID
3. Get ad groups → advertised products (ASINs)
4. Pull SP-API orders for those ASINs in May 2026
5. Calculate TACoS = Ad Spend / Total Sales
"""
import httpx, os, time, sys, json
from pathlib import Path
from datetime import datetime, timedelta

BASE_DIR = Path(__file__).parent.parent

def load_env():
    for line in (BASE_DIR / ".env").read_text().splitlines():
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            if k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()

load_env()

# ── Timers ──
_timings = {}
def tic(label):
    _timings[label] = time.time()
def toc(label):
    elapsed = time.time() - _timings.get(label, time.time())
    _timings[label] = elapsed
    return elapsed

# ═══════════════════════════════════════════════════════════════
# STEP 1: Ads API Auth
# ═══════════════════════════════════════════════════════════════
print("=" * 70)
print("TACoS Analysis — 斜边镜自動 (CUCZUUS US, May 2026)")
print("=" * 70)

STORE = "CUCZUUS"
PROFILE_ID = "3465892858543553"  # CUCZUUS US from ads_sync_engine.py
MARKETPLACE_ID = "ATVPDKIKX0DER"

ADS_CLIENT_ID = os.environ["STORE_02_ADS_CLIENT_ID"]
ADS_CLIENT_SECRET = os.environ["STORE_02_ADS_CLIENT_SECRET"]
ADS_REFRESH_TOKEN = os.environ["STORE_02_ADS_REFRESH_TOKEN"]

print("\n── Step 1: Ads API Authentication ──")
tic("ads_auth")
r = httpx.post("https://api.amazon.com/auth/o2/token", json={
    "grant_type": "refresh_token",
    "client_id": ADS_CLIENT_ID,
    "client_secret": ADS_CLIENT_SECRET,
    "refresh_token": ADS_REFRESH_TOKEN
}, timeout=15)

if r.status_code != 200:
    print(f"❌ Ads auth failed: {r.status_code} {r.text[:300]}")
    sys.exit(1)

ADS_TOKEN = r.json()["access_token"]
print(f"✅ Ads token obtained ({toc('ads_auth'):.1f}s)")

ADS_HEADERS = {
    "Authorization": f"Bearer {ADS_TOKEN}",
    "Amazon-Advertising-API-ClientId": ADS_CLIENT_ID,
    "Amazon-Advertising-API-Scope": PROFILE_ID,
    "Content-Type": "application/json"
}

# ═══════════════════════════════════════════════════════════════
# STEP 2: Find Campaign ID
# ═══════════════════════════════════════════════════════════════
print("\n── Step 2: Finding Campaign ID ──")
TARGET_CAMPAIGN = "斜边镜自動 - 9/22/2025"

tic("campaigns")
r = httpx.get("https://advertising-api.amazon.com/sp/campaigns",
              headers=ADS_HEADERS, timeout=15)

if r.status_code != 200:
    print(f"❌ Campaigns API failed: {r.status_code} {r.text[:300]}")
    sys.exit(1)

campaigns = r.json()
print(f"✅ Got {len(campaigns)} campaigns ({toc('campaigns'):.1f}s)")

campaign_id = None
for c in campaigns:
    if c.get("name") == TARGET_CAMPAIGN:
        campaign_id = c["campaignId"]
        print(f"🔍 Found: {c['name']}")
        print(f"   ID: {campaign_id}")
        print(f"   State: {c.get('state')}")
        print(f"   Budget: ${c.get('budget', {}).get('budget', 'N/A')}")
        print(f"   Targeting: {c.get('targetingType')}")
        print(f"   Start Date: {c.get('startDate')}")
        break

if not campaign_id:
    # Try fuzzy match
    print("⚠️  Exact match not found. Listing all campaigns:")
    for c in campaigns:
        name = c.get("name", "")
        if "斜边镜" in name or "斜邊鏡" in name:
            print(f"  → '{name}' (ID: {c['campaignId']})")
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════
# STEP 3: Get Ad Groups
# ═══════════════════════════════════════════════════════════════
print("\n── Step 3: Getting Ad Groups ──")
tic("adgroups")
r = httpx.get(
    f"https://advertising-api.amazon.com/sp/adGroups?campaignIdFilter={campaign_id}",
    headers=ADS_HEADERS, timeout=15
)
print(f"✅ Ad groups fetched ({toc('adgroups'):.1f}s)")

if r.status_code != 200:
    print(f"❌ Ad groups API failed: {r.status_code} {r.text[:300]}")
    sys.exit(1)

ad_groups = r.json()
print(f"   {len(ad_groups)} ad group(s) found:")

ad_group_ids = []
for ag in ad_groups:
    ag_id = ag["adGroupId"]
    ad_group_ids.append(ag_id)
    print(f"   • {ag.get('name')} (ID: {ag_id}, State: {ag.get('state')})")

# ═══════════════════════════════════════════════════════════════
# STEP 4: Get Advertised Products (ASINs)
# ═══════════════════════════════════════════════════════════════
print("\n── Step 4: Getting Advertised Products (ASINs) ──")
tic("products")
r = httpx.get(
    f"https://advertising-api.amazon.com/sp/advertisedProducts?campaignIdFilter={campaign_id}",
    headers=ADS_HEADERS, timeout=15
)
print(f"✅ Products fetched ({toc('products'):.1f}s)")

if r.status_code != 200:
    print(f"❌ Products API failed: {r.status_code} {r.text[:300]}")
    sys.exit(1)

products = r.json()
asins = []
for p in products:
    asin = p.get("asin", p.get("sku", "UNKNOWN"))
    if asin and asin != "UNKNOWN":
        asins.append(asin)
    print(f"   • ASIN: {asin} | SKU: {p.get('sku','?')} | "
          f"State: {p.get('state','?')} | Ad Group: {p.get('adGroupId','?')}")

print(f"\n   📦 {len(asins)} ASIN(s) to query in SP-API:")
for a in asins:
    print(f"      - {a}")

# Also try keyword-level for each ad group
print("\n── Step 4b: Getting Keywords ──")
keyword_asins = set(asins)
tic("kw_targets")
for ag_id in ad_group_ids:
    try:
        r = httpx.get(
            f"https://advertising-api.amazon.com/sp/targets?adGroupIdFilter={ag_id}",
            headers=ADS_HEADERS, timeout=15
        )
        if r.status_code == 200:
            targets = r.json()
            for t in targets:
                if t.get("expressionType") == "asinSameAs":
                    val = t.get("expression", [{}])[0].get("value", "")
                    if val:
                        keyword_asins.add(val)
    except Exception as e:
        print(f"   ⚠️  Keyword targets error for AG {ag_id}: {e}")
print(f"   Keyword ASINs found ({toc('kw_targets'):.1f}s)")

all_asins = list(keyword_asins)
print(f"   Total unique ASINs (products + keyword targets): {len(all_asins)}")

# ═══════════════════════════════════════════════════════════════
# STEP 5: SP-API Orders
# ═══════════════════════════════════════════════════════════════
print("\n── Step 5: SP-API Orders for May 2026 ──")

# Get SP-API token
tic("sp_auth")
SP_CLIENT_ID = os.environ["STORE_02_LWA_CLIENT_ID"]
SP_CLIENT_SECRET = os.environ["STORE_02_LWA_CLIENT_SECRET"]
SP_REFRESH_TOKEN = os.environ["STORE_02_REFRESH_TOKEN_AMERICAS"]

r = httpx.post("https://api.amazon.com/auth/o2/token", json={
    "grant_type": "refresh_token",
    "client_id": SP_CLIENT_ID,
    "client_secret": SP_CLIENT_SECRET,
    "refresh_token": SP_REFRESH_TOKEN
}, timeout=15)

if r.status_code != 200:
    print(f"❌ SP-API auth failed: {r.status_code} {r.text[:300]}")
    SP_TOKEN = None
else:
    SP_TOKEN = r.json()["access_token"]
    print(f"✅ SP-API token obtained ({toc('sp_auth'):.1f}s)")

SP_HEADERS = {
    "x-amz-access-token": SP_TOKEN,
    "Content-Type": "application/json",
    "User-Agent": "AmazonOpsIntel/1.0"
}

# Try pulling orders for May 2026
tic("orders_call")
START_DATE = "2026-05-01T00:00:00Z"
END_DATE = "2026-06-01T00:00:00Z"

all_orders = []

if SP_TOKEN:
    try:
        # First try with ASIN filter
        payload = {
            "MarketplaceIds": [MARKETPLACE_ID],
            "CreatedAfter": START_DATE,
            "CreatedBefore": END_DATE,
            "OrderStatuses": ["Shipped", "Unshipped", "PartiallyShipped"]
        }

        r = httpx.post(
            "https://sellingpartnerapi-na.amazon.com/orders/v0/orders",
            headers=SP_HEADERS,
            json=payload,
            timeout=30
        )

        print(f"   Orders API status: {r.status_code} ({toc('orders_call'):.1f}s)")

        if r.status_code == 200:
            data = r.json()
            orders_payload = data.get("payload", data)
            order_list = orders_payload.get("Orders", [])
            next_token = orders_payload.get("NextToken")

            print(f"   ✅ Got {len(order_list)} orders (page 1)")

            # Paginate
            page = 1
            while next_token:
                page += 1
                time.sleep(0.5)
                r2 = httpx.get(
                    f"https://sellingpartnerapi-na.amazon.com/orders/v0/orders",
                    headers=SP_HEADERS,
                    params={"NextToken": next_token, "MarketplaceIds": MARKETPLACE_ID},
                    timeout=30
                )
                if r2.status_code == 200:
                    pdata = r2.json().get("payload", r2.json())
                    more = pdata.get("Orders", [])
                    order_list.extend(more)
                    next_token = pdata.get("NextToken")
                    print(f"   ✅ Page {page}: {len(more)} orders (total: {len(order_list)})")
                else:
                    print(f"   ⚠️  Page {page} failed: {r2.status_code}")
                    break

            # Now get Order Items for each order to match ASINs
            print(f"\n   Fetching order items for {len(order_list)} orders...")
            tic("order_items")

            total_revenue = 0.0
            order_count = 0
            asin_revenue = {}
            asin_orders = {}

            for i, order in enumerate(order_list):
                amazon_order_id = order["AmazonOrderId"]
                order_status = order.get("OrderStatus", "?")
                purchase_date = order.get("PurchaseDate", "?")[:10]

                # Get order items
                try:
                    ir = httpx.get(
                        f"https://sellingpartnerapi-na.amazon.com/orders/v0/orders/{amazon_order_id}/orderItems",
                        headers=SP_HEADERS,
                        timeout=15
                    )
                    if ir.status_code == 200:
                        items = ir.json().get("payload", {}).get("OrderItems", [])
                    elif ir.status_code == 429:
                        time.sleep(2)
                        ir = httpx.get(
                            f"https://sellingpartnerapi-na.amazon.com/orders/v0/orders/{amazon_order_id}/orderItems",
                            headers=SP_HEADERS,
                            timeout=15
                        )
                        items = ir.json().get("payload", {}).get("OrderItems", []) if ir.status_code == 200 else []
                    else:
                        items = []
                except Exception as e:
                    items = []

                for item in items:
                    item_asin = item.get("ASIN", "")
                    item_price = float(item.get("ItemPrice", {}).get("Amount", 0) or 0)
                    shipping = float(item.get("ShippingPrice", {}).get("Amount", 0) or 0)
                    promo = float(item.get("PromotionDiscount", {}).get("Amount", 0) or 0)
                    qty = int(item.get("QuantityOrdered", 0) or 0)
                    revenue = item_price + shipping - promo

                    total_revenue += revenue
                    if revenue > 0:
                        order_count += 1

                    if item_asin in all_asins:
                        asin_revenue[item_asin] = asin_revenue.get(item_asin, 0) + revenue
                        asin_orders[item_asin] = asin_orders.get(item_asin, 0) + qty

                if (i + 1) % 50 == 0:
                    print(f"   Processed {i+1}/{len(order_list)} orders...")

            elapsed_items = toc("order_items")
            print(f"   ✅ Order items done ({elapsed_items:.1f}s)")
            print(f"\n   📊 Order Summary:")
            print(f"      Total orders (revenue > 0): {order_count}")
            print(f"      Total revenue: ${total_revenue:,.2f}")
            print(f"      Average per order: ${total_revenue/order_count:,.2f}" if order_count > 0 else "      N/A")

            if asin_revenue:
                print(f"\n   📦 Advertised ASIN breakdown:")
                for asin, rev in sorted(asin_revenue.items(), key=lambda x: -x[1]):
                    print(f"      {asin}: ${rev:,.2f} ({asin_orders.get(asin, 0)} units)")

        elif r.status_code == 403:
            print(f"   ⚠️  HTTP 403 Forbidden — likely IAM role or marketplace restrictions")
            print(f"   Response: {r.text[:500]}")
        else:
            print(f"   Response: {r.text[:500]}")

    except Exception as e:
        print(f"   ❌ Orders API error: {e}")
        import traceback; traceback.print_exc()
else:
    print("   ❌ No SP-API token available")

# ═══════════════════════════════════════════════════════════════
# STEP 6: Fallback — Check cached order files
# ═══════════════════════════════════════════════════════════════
print("\n── Step 6: Fallback — Checking data_lake for order data ──")
data_lake = BASE_DIR / "data_lake"
order_files = list(data_lake.rglob("*.json")) + list(data_lake.rglob("*.csv"))
print(f"   Found {len(order_files)} files in data_lake/")

# Check for any order-related files
order_related = [f for f in order_files if "order" in f.name.lower() or "revenue" in f.name.lower()]
print(f"   Order-related files: {len(order_related)}")
for f in order_related[:10]:
    print(f"      {f.relative_to(BASE_DIR)}")

# ═══════════════════════════════════════════════════════════════
# STEP 7: TACoS Calculation
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("TACoS CALCULATION")
print("=" * 70)

AD_SPEND = 245.71  # From data_lake
AD_SALES_30D = 850.78   # 30-day attribution window
AD_SALES_14D = 415.87   # 14-day attribution window (estimated ~49% of 30d)
AD_ORDERS = 21

# Use SP-API total if available
if 'total_revenue' in dir() and total_revenue > 0:
    total_sales = total_revenue
    organic_sales = total_sales - AD_SALES_30D
else:
    # Estimate: if ACoS is 28.9% (245.71/850.78), and assuming
    # organic is typically 3-5x ad sales for this category...
    # We can't estimate without real data
    total_sales = None

print(f"\n   Campaign: 斜边镜自動 - 9/22/2025")
print(f"   Period: May 2026")
print(f"   Profile ID: {PROFILE_ID}")
print(f"   Campaign ID: {campaign_id}")
print(f"   ASINs: {', '.join(all_asins) if all_asins else 'N/A'}")
print(f"\n   Ad Spend: ${AD_SPEND:,.2f}")
print(f"   Ad Sales (30d attribution): ${AD_SALES_30D:,.2f}")
print(f"   Ad Sales (14d attribution): ${AD_SALES_14D:,.2f}")
print(f"   Ad Orders (30d): {AD_ORDERS}")
print(f"   ACoS (30d): {AD_SPEND/AD_SALES_30D*100:.1f}%")
print(f"   ACoS (14d): {AD_SPEND/AD_SALES_14D*100:.1f}%")

if total_sales is not None:
    organic = total_sales - AD_SALES_30D
    tacos_30d = AD_SPEND / total_sales
    tacos_14d = AD_SPEND / (organic + AD_SALES_14D) if organic + AD_SALES_14D > 0 else float('inf')

    print(f"\n   Total Sales (SP-API): ${total_sales:,.2f}")
    print(f"   Organic Sales (30d attribution): ${organic:,.2f}")
    print(f"   ─────────────────────────────────────")
    print(f"   TACoS (30d attr): {tacos_30d*100:.1f}%")
    print(f"   TACoS (14d attr): {tacos_14d*100:.1f}%")
else:
    print(f"\n   ⚠️  Total sales from SP-API unavailable.")
    print(f"   TACoS requires total (organic + ad) sales from SP-API orders.")

# ═══════════════════════════════════════════════════════════════
# STEP 8: Timing Summary
# ═══════════════════════════════════════════════════════════════
print("\n── Timing Summary ──")
total_time = 0
for label, elapsed in sorted(_timings.items(), key=lambda x: -x[1]):
    if isinstance(elapsed, (int, float)):
        print(f"   {label}: {elapsed:.1f}s")
        total_time += elapsed
print(f"   ═════════════════════")
print(f"   TOTAL API TIME: {total_time:.1f}s")

# Monthly feasibility estimate
print(f"\n── Monthly Run Estimate ──")
print(f"   Campaigns lookup: ~1s")
print(f"   Ad groups: ~1s")
print(f"   Products: ~1s")
print(f"   SP-API orders (per store): ~30-120s (depends on order volume)")
print(f"   Total per store per month: ~35-125s")
print(f"   With 3 stores × 4 markets: ~7-25 min/month")
