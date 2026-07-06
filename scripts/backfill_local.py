"""Local backfill: pull SP-API v2026 → insert into Railway DB."""
import os, sys, httpx, time, psycopg2
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = {}
for line in open(os.path.join(BASE, '.env')).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

dsn = env['DATABASE_URL']

HOSTS = {'NA': 'sellingpartnerapi-na.amazon.com',
         'FE': 'sellingpartnerapi-fe.amazon.com',
         'EU': 'sellingpartnerapi-eu.amazon.com'}

STORES = [
    ('CUCZUUS', '02', 'AXW589ZKKDJNM'),
    ('BOOLUU', '03', 'A8P0GUSN1BWCC'),
    ('Heliumx', '04', 'AD4UKHMR5K35J'),
]

REGIONS = [
    ('NA', 'ATVPDKIKX0DER,A2EUQ1WTGCTBG2'),
    ('FE', 'A39IBJ37TRP1C6,A1VC38T7YXB528'),
    ('EU', 'A1PA6795UKMFR9'),
]

MID_TO_MKT = {
    'ATVPDKIKX0DER': 'US', 'A2EUQ1WTGCTBG2': 'CA',
    'A39IBJ37TRP1C6': 'AU', 'A1VC38T7YXB528': 'JP',
    'A1PA6795UKMFR9': 'DE'
}

date_start = datetime(2026, 6, 1)
date_end = datetime(2026, 7, 2)
after = date_start.strftime('%Y-%m-%dT07:00:00Z')

total_orders = 0
total_items = 0

for store_name, snum, seller_id in STORES:
    CID = env.get(f'STORE_{snum}_LWA_CLIENT_ID', '')
    CSEC = env.get(f'STORE_{snum}_LWA_CLIENT_SECRET', '')
    if not CID:
        print(f'  ⚠ {store_name}: no creds')
        continue
    
    for region, mids_str in REGIONS:
        ref_key = {'NA': 'AMERICAS', 'FE': 'AU', 'EU': 'EU'}[region]
        ref = env.get(f'STORE_{snum}_REFRESH_TOKEN_{ref_key}', '')
        if not ref:
            continue
        
        print(f'\n{store_name}/{region}...', end=' ', flush=True)
        
        # LWA
        r = httpx.post('https://api.amazon.com/auth/o2/token',
            json={'grant_type': 'refresh_token', 'client_id': CID,
                  'client_secret': CSEC, 'refresh_token': ref}, timeout=15)
        if r.status_code != 200:
            print(f'LWA fail {r.status_code}')
            continue
        tk = r.json()['access_token']
        
        host = HOSTS[region]
        store_orders = 0
        store_items = 0
        nt = None
        pages = 0
        
        while True:
            pages += 1
            u = f'https://{host}/orders/2026-01-01/orders?marketplaceIds={mids_str}&createdAfter={after}&MaxResultsPerPage=100'
            if nt:
                u += '&nextToken=' + nt
            r2 = httpx.get(u, headers={'x-amz-access-token': tk}, timeout=30)
            if r2.status_code != 200:
                print(f'v2026 fail {r2.status_code}')
                break
            
            orders = r2.json().get('orders', [])
            if not orders:
                break
            
            conn = psycopg2.connect(dsn)
            cur = conn.cursor()
            for o in orders:
                oid = o['orderId']
                mkt_id = o.get('salesChannel', {}).get('marketplaceId', '')
                mkt = MID_TO_MKT.get(mkt_id, 'US')
                pd = o.get('purchaseDate', '')
                if not pd:
                    pd = '2026-01-01T00:00:00Z'
                
                cur.execute("""
                    INSERT INTO orders_active (order_id, store, marketplace, marketplace_id, seller_id, status, fulfillment_type, purchase_date, last_updated)
                    VALUES (%s, %s, %s, %s, %s, 'FOUND', 'FBA', %s, NOW())
                    ON CONFLICT (order_id) DO NOTHING
                """, (oid, store_name, mkt, mkt_id, seller_id, pd))
                
                for it in o.get('orderItems', []):
                    p = it.get('product', {})
                    asin = p.get('asin', '')
                    if not asin:
                        continue
                    price = float(p.get('price', {}).get('unitPrice', {}).get('amount', 0) or 0)
                    if price <= 0:
                        continue
                    cur.execute("""
                        INSERT INTO order_items_active (order_id, order_item_id, seller_sku, asin, quantity, unit_price, currency, title)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (order_id, order_item_id) DO NOTHING
                    """, (oid, it.get('orderItemId', ''), p.get('sellerSku', ''),
                           asin, int(it.get('quantityOrdered', 1) or 1), price,
                           p.get('price', {}).get('unitPrice', {}).get('currencyCode', 'USD'),
                           (p.get('title', '') or '')[:200]))
                    store_items += 1
                store_orders += 1
            
            conn.commit()
            conn.close()
            
            nt = r2.json().get('pagination', {}).get('nextToken')
            if not nt:
                break
            time.sleep(0.3)
        
        print(f'{store_orders} orders, {store_items} items ({pages} pages)')
        total_orders += store_orders
        total_items += store_items
        time.sleep(1)

print(f'\n✅ Backfill complete: {total_orders} orders, {total_items} items')
