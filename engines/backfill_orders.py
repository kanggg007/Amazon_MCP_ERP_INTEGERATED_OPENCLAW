"""
Backfill orders from SP-API v2026 into orders_active + order_items_active.
Run by /admin/backfill-orders?date_start=2026-06-01&date_end=2026-07-02
"""

import os, httpx, threading, time, urllib.parse, hashlib, hmac, json
from datetime import datetime, timezone, timedelta
import psycopg2

def backfill_orders(date_start_str, date_end_str=None):
    """Pull v2026 orders for date range and insert into orders_active + order_items_active."""
    BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dsn = os.environ.get('DATABASE_URL', '')
    if not dsn:
        # Try reading from .env
        env_path = os.path.join(BASE, '.env')
        if os.path.exists(env_path):
            for line in open(env_path).read().splitlines():
                if 'DATABASE_URL' in line:
                    dsn = line.split('=', 1)[1].strip()
    if not dsn:
        return {"error": "no DATABASE_URL"}
    
    # Load env
    env = {}
    env_path = os.path.join(BASE, '.env')
    if os.path.exists(env_path):
        for line in open(env_path).read().splitlines():
            if line and '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip()
    
    AWSAK = env.get('STORE_02_AWS_ACCESS_KEY_ID', os.environ.get('STORE_02_AWS_ACCESS_KEY_ID', ''))
    AWSSK = env.get('STORE_02_AWS_SECRET_ACCESS_KEY', os.environ.get('STORE_02_AWS_SECRET_ACCESS_KEY', ''))
    
    STORES = {
        'CUCZUUS': {'num': '02', 'seller_id': 'AXW589ZKKDJNM'},
        'BOOLUU': {'num': '03', 'seller_id': 'A8P0GUSN1BWCC'},
        'Heliumx': {'num': '04', 'seller_id': 'AD4UKHMR5K35J'},
    }
    
    SELLER_TO_STORE = {v['seller_id']: k for k, v in STORES.items()}
    # Add additional seller IDs
    for sid, s in [
        ('A3T7S7ZJU0L4UW', 'CUCZUUS'), ('A1OQTH56GU88CL', 'CUCZUUS'), ('A5O26V5UGR14D', 'CUCZUUS'),
        ('AUNV8PFX5IF0V', 'BOOLUU'), ('A1D6RZ93LW6YJ0', 'BOOLUU'),
        ('A1J6BBZMNHT0JZ', 'Heliumx'), ('A3K1UQY1P92DWZ', 'Heliumx'),
    ]:
        SELLER_TO_STORE[sid] = s
    
    HOSTS = {'NA': 'sellingpartnerapi-na.amazon.com',
             'FE': 'sellingpartnerapi-fe.amazon.com',
             'EU': 'sellingpartnerapi-eu.amazon.com'}
    
    REGION_MARKETPLACES = {
        'NA': ('ATVPDKIKX0DER,A2EUQ1WTGCTBG2', ['US', 'CA']),
        'FE': ('A39IBJ37TRP1C6,A1VC38T7YXB528', ['AU', 'JP']),
        'EU': ('A1PA6795UKMFR9', ['DE']),
    }
    
    MID_TO_MKT = {
        'ATVPDKIKX0DER': 'US', 'A2EUQ1WTGCTBG2': 'CA',
        'A39IBJ37TRP1C6': 'AU', 'A1VC38T7YXB528': 'JP',
        'A1PA6795UKMFR9': 'DE'
    }
    
    # Parse dates
    date_start = datetime.strptime(date_start_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    if date_end_str:
        date_end = datetime.strptime(date_end_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    else:
        date_end = date_start
    
    # Gen date list
    dates = []
    d = date_start
    while d <= date_end:
        dates.append(d)
        d += timedelta(days=1)
    
    print(f"[BACKFILL] {date_start_str} to {date_end_str} ({len(dates)} days)", flush=True)
    
    total_orders = 0
    total_items = 0
    
    for store_name, store_cfg in STORES.items():
        snum = store_cfg['num']
        CID = env.get(f'STORE_{snum}_LWA_CLIENT_ID', '')
        CSEC = env.get(f'STORE_{snum}_LWA_CLIENT_SECRET', '')
        if not CID:
            continue
        
        for region, (mids_str, _) in REGION_MARKETPLACES.items():
            ref_key = {'NA': 'AMERICAS', 'FE': 'AU', 'EU': 'EU'}[region]
            ref = env.get(f'STORE_{snum}_REFRESH_TOKEN_{ref_key}', '')
            if not ref:
                continue
            
            # Get LWA token
            r = httpx.post('https://api.amazon.com/auth/o2/token',
                json={'grant_type': 'refresh_token', 'client_id': CID,
                      'client_secret': CSEC, 'refresh_token': ref}, timeout=15)
            if r.status_code != 200:
                print(f"  LWA fail: {store_name}/{region}", flush=True)
                continue
            tk = r.json()['access_token']
            host = HOSTS[region]
            
            for day in dates:
                # LA time: 07:00Z day to 07:00Z next day
                after = day.strftime('%Y-%m-%dT07:00:00Z')
                before = (day + timedelta(days=1)).strftime('%Y-%m-%dT07:00:00Z')
                
                day_orders = 0
                nt = None
                page = 0
                while True:
                    page += 1
                    u = f'https://{host}/orders/2026-01-01/orders?marketplaceIds={mids_str}&createdAfter={after}&createdBefore={before}&MaxResultsPerPage=100'
                    if nt:
                        u += '&nextToken=' + nt
                    try:
                        r2 = httpx.get(u, headers={'x-amz-access-token': tk}, timeout=30)
                    except:
                        break
                    if r2.status_code != 200:
                        break
                    
                    data = r2.json()
                    orders = data.get('orders', [])
                    if not orders:
                        break
                    
                    # Insert batch
                    conn = psycopg2.connect(dsn)
                    cur = conn.cursor()
                    for o in orders:
                        oid = o['orderId']
                        mkt_id = o.get('salesChannel', {}).get('marketplaceId', '')
                        mkt = MID_TO_MKT.get(mkt_id, 'US')
                        seller_id = ''  # v2026 doesn't expose sellerId in the order directly
                        purchase_date = o.get('purchaseDate', '')
                        
                        # UPSERT order
                        cur.execute("""
                            INSERT INTO orders_active (order_id, store, marketplace, marketplace_id, seller_id, status, fulfillment_type, purchase_date, last_updated)
                            VALUES (%s, %s, %s, %s, %s, 'FOUND', 'FBA', %s, NOW())
                            ON CONFLICT (order_id) DO NOTHING
                        """, (oid, store_name, mkt, mkt_id, '', purchase_date))
                        
                        for it in o.get('orderItems', []):
                            p = it.get('product', {})
                            asin = p.get('asin', '')
                            sku = p.get('sellerSku', '')
                            title = (p.get('title', '') or '')[:200]
                            price = float(p.get('price', {}).get('unitPrice', {}).get('amount', 0) or 0)
                            currency = p.get('price', {}).get('unitPrice', {}).get('currencyCode', 'USD')
                            qty = int(it.get('quantityOrdered', 1) or 1)
                            oiid = it.get('orderItemId', '')
                            
                            if asin and price > 0:
                                cur.execute("""
                                    INSERT INTO order_items_active (order_id, order_item_id, seller_sku, asin, quantity, unit_price, currency, title)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                                    ON CONFLICT (order_id, order_item_id) DO NOTHING
                                """, (oid, oiid, sku, asin, qty, price, currency, title))
                                total_items += 1
                        
                        day_orders += 1
                    
                    conn.commit()
                    conn.close()
                    total_orders += day_orders
                    
                    nt = data.get('nextToken')
                    if not nt:
                        break
                    time.sleep(0.5)  # Rate limit
                
                if day_orders:
                    print(f"  {store_name}/{region} {day.strftime('%Y-%m-%d')}: {day_orders} orders ({page} pages)", flush=True)
                time.sleep(1)  # Between days
    
    print(f"[BACKFILL] Done: {total_orders} orders, {total_items} items", flush=True)
    return {"status": "ok", "orders": total_orders, "items": total_items, "dates": len(dates)}
