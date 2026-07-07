"""
Live Profit Engine — real-time revenue from push notifications.
No v2026 pull needed. Uses DB caches for price, COGS, freight, FBA, ads.

Triggered by push consumer when ORDER_CHANGE fires with new order.
"""

import os, psycopg2, httpx, threading, time
from datetime import datetime
from collections import defaultdict

# FX rates (Sina Finance)
FX_USD = {'US': 1.0, 'CA': 0.7033, 'AU': 0.6892, 'JP': 0.00614, 'DE': 1.1403}
CNY_USD = 0.1477
REFERRAL_RATE = 0.15

def _get_dsn():
    dsn = os.environ.get('DATABASE_URL', '')
    if not dsn:
        try:
            BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            env_path = os.path.join(BASE, '.env')
            if os.path.exists(env_path):
                for line in open(env_path).read().splitlines():
                    if 'DATABASE_URL' in line:
                        dsn = line.split('=', 1)[1].strip()
        except: pass
    return dsn

def ensure_tables():
    dsn = _get_dsn()
    if not dsn: return
    conn = psycopg2.connect(dsn, connect_timeout=10)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS profit_live (
            id SERIAL PRIMARY KEY,
            order_id VARCHAR(32),
            order_item_id VARCHAR(32),
            store VARCHAR(10),
            marketplace VARCHAR(5),
            asin VARCHAR(15),
            sku VARCHAR(50),
            title VARCHAR(200),
            unit_price DECIMAL(10,2),
            quantity INT,
            revenue DECIMAL(10,3),
            currency VARCHAR(5),
            cogs DECIMAL(10,3),
            sea_freight DECIMAL(10,3),
            fba_fee DECIMAL(10,3),
            referral_fee DECIMAL(10,3),
            ads_spend DECIMAL(10,3),
            profit DECIMAL(10,3),
            margin DECIMAL(6,3),
            flags VARCHAR(100) DEFAULT 'OK',
            order_date DATE,
            order_time TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(order_id, order_item_id)
        )
    """)
    conn.commit()
    conn.close()

def compute_profit(order_id, store, marketplace, sku, quantity, order_date=None):
    """
    Compute profit for a single order item (called per item in order).
    
    Returns dict with all cost components, or None on failure.
    """
    dsn = _get_dsn()
    if not dsn:
        return None
    
    conn = psycopg2.connect(dsn, connect_timeout=5)
    cur = conn.cursor()
    
    # 1. SKU → ASIN lookup (from order_items_active cache)
    #    If SKU not in cache, try from product_cost_master
    cur.execute("""
        SELECT asin, unit_price, currency, title
        FROM order_items_active
        WHERE seller_sku = %s AND order_id = %s
        LIMIT 1
    """, (sku, order_id))
    
    row = cur.fetchone()
    if not row:
        # Try all SKUs for this order
        cur.execute("""
            SELECT asin, unit_price, currency, title
            FROM order_items_active
            WHERE order_id = %s
            LIMIT 1
        """, (order_id,))
        row = cur.fetchone()
    
    if not row:
        conn.close()
        return {"error": "SKU not found", "sku": sku, "order_id": order_id}
    
    asin, unit_price, currency, title = row
    
    if not asin:
        conn.close()
        return {"error": "No ASIN for SKU", "sku": sku}
    
    # 2. Revenue = price × qty (if no price from cache, estimate from catalog)
    if unit_price and unit_price > 0:
        revenue = float(unit_price) * quantity
    else:
        # Estimate from catalog
        cur.execute("""
            SELECT cogs_usd FROM product_cost_master
            WHERE asin = %s AND marketplace = %s
        """, (asin, marketplace))
        r = cur.fetchone()
        est_price = float(r[0]) * 3.5 if r and r[0] else 30  # rough: 3.5x COGS
        revenue = est_price * quantity
        unit_price = est_price
    
    # 3. COGS + Freight from product_cost_master
    cur.execute("""
        SELECT cogs_usd, sea_freight_usd, lead_time_days, transit_time_days
        FROM product_cost_master
        WHERE asin = %s AND marketplace = %s
    """, (asin, marketplace))
    
    r = cur.fetchone()
    
    missing_costs = []
    if not r:
        missing_costs.append('ASIN_NOT_IN_CATALOG')
        cogs = 0
        freight = 0
    else:
        cogs = (float(r[0]) if r[0] else 0) * quantity
        freight = (float(r[1]) if r[1] else 0) * quantity
        if not r[0]: missing_costs.append('COGS_MISSING')
        if not r[1]: missing_costs.append('FREIGHT_MISSING')
    
    # 4. FBA fee (lookup from fba_fees cache, or flag missing)
    try:
        cur.execute("""
            SELECT fba_fee FROM fba_fees
            WHERE asin = %s AND marketplace = %s
            ORDER BY retrieved_at DESC LIMIT 1
        """, (asin, marketplace))
        r = cur.fetchone()
        if r and r[0]:
            fba = float(r[0]) * quantity
        else:
            fba = 0
            missing_costs.append('FBA_MISSING')
    except:
        fba = 0
        missing_costs.append('FBA_MISSING')
    
    # 5. Referral = 15% of revenue
    referral = revenue * REFERRAL_RATE
    
    # 6. Ads (daily average per store/market, from ads_daily)
    cur.execute("""
        SELECT AVG(cost) FROM ads_daily
        WHERE store = %s AND market = %s AND date >= CURRENT_DATE - 7
    """, (store, marketplace))
    
    r = cur.fetchone()
    daily_ads = float(r[0]) if r and r[0] else 0
    
    # Allocate ads per order (simplified: divide by avg daily orders)
    cur.execute("""
        SELECT AVG(cnt) FROM (
            SELECT COUNT(*) as cnt FROM orders_active
            WHERE store = %s AND marketplace = %s AND purchase_date > CURRENT_DATE - 7
            GROUP BY DATE(purchase_date)
        ) sub
    """, (store, marketplace))
    r = cur.fetchone()
    avg_orders = float(r[0]) if r and r[0] else 10
    
    ads = daily_ads / avg_orders if avg_orders > 0 else 0
    
    # 7. Compute profit
    profit = revenue - cogs - freight - fba - referral - ads
    margin = (profit / revenue * 100) if revenue > 0 else 0
    
    flags = '|'.join(missing_costs) if missing_costs else 'OK'
    
    # 8. Store in profit_live (skip if table doesn't exist yet)
    try:
        cur.execute("""
            INSERT INTO profit_live (order_id, order_item_id, store, marketplace, asin, sku, title,
                unit_price, quantity, revenue, currency, cogs, sea_freight, fba_fee,
                referral_fee, ads_spend, profit, margin, flags, order_date, order_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (order_id, order_item_id) DO NOTHING
        """, (order_id, sku, store, marketplace, asin, sku, title,
              round(unit_price, 2), quantity, round(revenue, 3), currency,
              round(cogs, 3), round(freight, 3), round(fba, 3),
              round(referral, 3), round(ads, 3), round(profit, 3), round(margin, 3),
              flags,
              order_date or datetime.now().date()))
        conn.commit()
    except Exception as e:
        pass  # Table doesn't exist yet — profit not persisted
    
    conn.close()
    
    return {
        "order_id": order_id,
        "store": store,
        "marketplace": marketplace,
        "asin": asin,
        "sku": sku,
        "quantity": quantity,
        "revenue": round(revenue, 2),
        "cogs": round(cogs, 2),
        "freight": round(freight, 2),
        "fba": round(fba, 2),
        "referral": round(referral, 2),
        "ads": round(ads, 2),
        "profit": round(profit, 2),
        "margin": round(margin, 1),
        "flags": flags,
        "estimates": missing_costs,
    }


def daily_summary(date=None):
    """Get today's profit summary by store/market."""
    dsn = _get_dsn()
    if not dsn:
        return {"error": "no DB"}
    
    if date is None:
        date = datetime.now().strftime('%Y-%m-%d')
    
    conn = psycopg2.connect(dsn, connect_timeout=10)
    cur = conn.cursor()
    
    cur.execute("""
        SELECT store, marketplace,
               COUNT(*) as orders,
               SUM(quantity) as units,
               ROUND(SUM(revenue), 2) as revenue,
               ROUND(SUM(cogs), 2) as cogs,
               ROUND(SUM(sea_freight), 2) as freight,
               ROUND(SUM(fba_fee), 2) as fba,
               ROUND(SUM(referral_fee), 2) as referral,
               ROUND(SUM(ads_spend), 2) as ads,
               ROUND(SUM(profit), 2) as profit,
               ROUND(AVG(margin), 1) as avg_margin
        FROM profit_live
        WHERE order_date = %s
        GROUP BY store, marketplace
        ORDER BY store, marketplace
    """, (date,))
    
    rows = []
    to, tu, tr, tp = 0, 0, 0, 0
    for r in cur.fetchall():
        rows.append({
            'store': r[0], 'marketplace': r[1],
            'orders': r[2], 'units': r[3],
            'revenue': float(r[4]), 'cogs': float(r[5]),
            'freight': float(r[6]), 'fba': float(r[7]),
            'referral': float(r[8]), 'ads': float(r[9]),
            'profit': float(r[10]), 'margin': float(r[11]),
        })
        to += r[2]; tu += r[3]; tr += float(r[4]); tp += float(r[10])
    
    conn.close()
    
    return {
        "date": date,
        "summary": rows,
        "totals": {
            "orders": to, "units": tu,
            "revenue": round(tr, 2), "profit": round(tp, 2),
            "margin": round(tp/tr*100, 1) if tr > 0 else 0,
        }
    }


# Initialize on import
ensure_tables()


if __name__ == '__main__':
    import json
    # Test: compute for a known order
    result = compute_profit(
        order_id='113-6564244-4070642',
        store='CUCZUUS',
        marketplace='US',
        sku='CUC-US-FT19-1',
        quantity=1,
        order_date='2026-07-01'
    )
    print(json.dumps(result, indent=2, default=str))
