"""Batch profit compute: single DB connection for all orders on a date."""
import os, sys, psycopg2

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = {}
for line in open(os.path.join(BASE, '.env')).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

dsn = env.get('DATABASE_URL', os.environ.get('DATABASE_URL', ''))
if not dsn:
    print('No DATABASE_URL')
    exit(1)

CNY_USD = 0.1477
REFERRAL_RATE = 0.15

def batch_compute(date_str):
    conn = psycopg2.connect(dsn, connect_timeout=15)
    cur = conn.cursor()
    
    # Ensure tables exist
    cur.execute("""
        CREATE TABLE IF NOT EXISTS profit_live (
            id SERIAL PRIMARY KEY, order_id VARCHAR(32), order_item_id VARCHAR(32),
            store VARCHAR(10), marketplace VARCHAR(5), asin VARCHAR(15), sku VARCHAR(50),
            title VARCHAR(200), unit_price DECIMAL(10,2), quantity INT,
            revenue DECIMAL(10,3), currency VARCHAR(5),
            cogs DECIMAL(10,3), sea_freight DECIMAL(10,3), fba_fee DECIMAL(10,3),
            referral_fee DECIMAL(10,3), ads_spend DECIMAL(10,3),
            profit DECIMAL(10,3), margin DECIMAL(6,3),
            flags VARCHAR(100) DEFAULT 'OK',
            order_date DATE, order_time TIMESTAMP, created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(order_id, order_item_id)
        )
    """)
    conn.commit()
    
    # Get all unprocessed orders for this date
    cur.execute("""
        SELECT oa.order_id, oi.order_item_id, oa.store, oa.marketplace,
               oi.seller_sku, oi.asin, oi.quantity, oi.unit_price, oi.title,
               oi.currency, DATE(oa.purchase_date)
        FROM orders_active oa
        JOIN order_items_active oi ON oa.order_id = oi.order_id
        WHERE DATE(oa.purchase_date) = %s
          AND oa.purchase_date > '2026-06-01'
          AND NOT EXISTS (
              SELECT 1 FROM profit_live pl
              WHERE pl.order_id = oa.order_id AND pl.order_item_id = oi.order_item_id
          )
    """, (date_str,))
    
    items = [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], float(r[7] or 0), str(r[8] or ''), r[9] or 'USD', str(r[10])) for r in cur.fetchall()]
    total = len(items)
    print(f'{date_str}: {total} items to compute')
    
    if total == 0:
        conn.close()
        return {'date': date_str, 'computed': 0}
    
    # Pre-load all product costs in one query
    asins = list(set(it[5] for it in items))
    markets = list(set(it[3] for it in items))
    
    cur.execute("""
        SELECT asin, marketplace, cogs_usd, sea_freight_usd
        FROM product_cost_master
        WHERE asin = ANY(%s) AND marketplace = ANY(%s)
    """, (asins, markets))
    costs = {}
    for r in cur.fetchall():
        costs[(r[0], r[1])] = {'cogs': float(r[2] or 0), 'freight': float(r[3] or 0)}
    cost_exists = set((r[0], r[1]) for r in cur.fetchall() if r)  # re-fetch
    # Actually redo properly
    cur.execute("""
        SELECT asin, marketplace, cogs_usd, sea_freight_usd
        FROM product_cost_master
        WHERE asin = ANY(%s) AND marketplace = ANY(%s)
    """, (asins, markets))
    costs = {(r[0], r[1]): {'cogs': float(r[2] or 0), 'freight': float(r[3] or 0)} for r in cur.fetchall()}
    
    # Pre-load ads (daily avg per store/market)
    cur.execute("""
        SELECT store, market, AVG(cost)
        FROM ads_daily
        WHERE date >= %s::date - 7
        GROUP BY store, market
    """, (date_str,))
    ads_avg = {(r[0], r[1]): float(r[2] or 0) for r in cur.fetchall()}
    
    # Pre-load daily order counts
    cur.execute("""
        SELECT store, marketplace, COUNT(*) / 7.0
        FROM orders_active
        WHERE purchase_date > %s::date - 7
        GROUP BY store, marketplace
    """, (date_str,))
    daily_orders = {(r[0], r[1]): float(r[2] or 1) for r in cur.fetchall()}
    
    # Batch compute
    ok = 0; flagged = 0
    insert_rows = []
    for oid, oiid, store, mkt, sku, asin, qty, price, title, currency, od in items:
        if price <= 0 or qty <= 0:
            continue
        
        revenue = price * qty
        
        cost = costs.get((asin, mkt), {})
        cogs = cost.get('cogs', 0) * qty
        freight = cost.get('freight', 0) * qty
        fba_fee_value = 0
        
        # Lookup FBA from fba_fees table (cached)
        fba_result = fba_cache.get((asin, mkt))
        if fba_result:
            fba_fee_value = fba_result * qty
        
        flags_list = []
        if (asin, mkt) not in costs:
            flags_list.append('ASIN_NOT_IN_CATALOG')
        if not cost.get('cogs'):
            flags_list.append('COGS_MISSING')
        if not cost.get('freight'):
            flags_list.append('FREIGHT_MISSING')
        if not fba_result:
            flags_list.append('FBA_MISSING')
        
        referral = revenue * REFERRAL_RATE
        
        key = (store, mkt)
        daily_ad = ads_avg.get(key, 0)
        avg_orders = daily_orders.get(key, 1) or 1
        ads = daily_ad / avg_orders if avg_orders > 0 else 0
        
        profit = revenue - cogs - freight - fba_fee_value - referral - ads
        margin = (profit / revenue * 100) if revenue > 0 else 0
        flags = '|'.join(flags_list)
        
        insert_rows.append((oid, oiid, store, mkt, asin, sku, title, price, qty,
            round(revenue, 3), currency, round(cogs, 3), round(freight, 3),
            round(fba_fee_value, 3), round(referral, 3), round(ads, 3),
            round(profit, 3), round(margin, 3), flags, od))
        ok += 1
        if 'ASIN_NOT_IN_CATALOG' in flags:
            flagged += 1
    
    # Batch INSERT all at once
    if insert_rows:
        from psycopg2.extras import execute_values
        execute_values(cur, """
            INSERT INTO profit_live (order_id, order_item_id, store, marketplace, asin, sku, title,
                unit_price, quantity, revenue, currency, cogs, sea_freight, fba_fee,
                referral_fee, ads_spend, profit, margin, flags, order_date, order_time)
            VALUES %s
            ON CONFLICT (order_id, order_item_id) DO NOTHING
        """, insert_rows,
        template='(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())', page_size=500)
    
    conn.commit()
    conn.close()
    print(f'  {ok} computed, {flagged} flagged as missing catalog')
    return {'date': date_str, 'computed': ok, 'flagged': flagged}


if __name__ == '__main__':
    for d in sys.argv[1:] if len(sys.argv) > 1 else ['2026-07-06']:
        batch_compute(d)
