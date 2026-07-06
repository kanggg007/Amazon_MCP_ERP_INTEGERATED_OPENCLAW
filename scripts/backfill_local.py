"""Fast local backfill with batch INSERTs."""
import os, sys, httpx, time, psycopg2
from psycopg2.extras import execute_values

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = {}
for line in open(os.path.join(BASE, '.env')).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

dsn = env['DATABASE_URL']
conn = psycopg2.connect(dsn)
cur = conn.cursor()

cur.execute('''CREATE TABLE IF NOT EXISTS orders_active (
    order_id VARCHAR(32) PRIMARY KEY, store VARCHAR(10), marketplace VARCHAR(5),
    marketplace_id VARCHAR(20), seller_id VARCHAR(20), status VARCHAR(20),
    fulfillment_type VARCHAR(10), purchase_date TIMESTAMP, last_updated TIMESTAMP DEFAULT NOW())''')
cur.execute('''CREATE TABLE IF NOT EXISTS order_items_active (
    id SERIAL PRIMARY KEY, order_id VARCHAR(32),
    order_item_id VARCHAR(32), seller_sku VARCHAR(50), asin VARCHAR(15),
    quantity INT, unit_price DECIMAL(10,2), currency VARCHAR(5), title VARCHAR(200),
    UNIQUE(order_id, order_item_id))''')
conn.commit()
print('Tables ready', flush=True)

HOSTS = {'NA': 'sellingpartnerapi-na.amazon.com', 'FE': 'sellingpartnerapi-fe.amazon.com', 'EU': 'sellingpartnerapi-eu.amazon.com'}
MID_TO_MKT = {'ATVPDKIKX0DER': 'US', 'A2EUQ1WTGCTBG2': 'CA', 'A39IBJ37TRP1C6': 'AU', 'A1VC38T7YXB528': 'JP', 'A1PA6795UKMFR9': 'DE'}

store_configs = [
    ('CUCZUUS', '02', 'AXW589ZKKDJNM'),
    ('BOOLUU', '03', 'A8P0GUSN1BWCC'),
    ('Heliumx', '04', 'AD4UKHMR5K35J'),
]
regions = [
    ('NA', 'ATVPDKIKX0DER,A2EUQ1WTGCTBG2'),
    ('FE', 'A39IBJ37TRP1C6,A1VC38T7YXB528'),
    ('EU', 'A1PA6795UKMFR9'),
]

after = '2026-06-01T07:00:00Z'
total_orders = 0
total_items = 0

for store_name, snum, seller_id in store_configs:
    cid = env.get(f'STORE_{snum}_LWA_CLIENT_ID', '')
    csec = env.get(f'STORE_{snum}_LWA_CLIENT_SECRET', '')
    if not cid:
        print(f'  ⚠ {store_name}: no creds', flush=True)
        continue
    
    for region, mids_str in regions:
        ref_key = {'NA': 'AMERICAS', 'FE': 'AU', 'EU': 'EU'}[region]
        ref = env.get(f'STORE_{snum}_REFRESH_TOKEN_{ref_key}', '')
        if not ref:
            continue
        
        print(f'{store_name}/{region}... ', end='', flush=True)
        t0 = time.time()
        
        r = httpx.post('https://api.amazon.com/auth/o2/token',
            json={'grant_type': 'refresh_token', 'client_id': cid,
                  'client_secret': csec, 'refresh_token': ref}, timeout=15)
        if r.status_code != 200:
            print(f'LWA fail {r.status_code}', flush=True)
            continue
        tk = r.json()['access_token']
        host = HOSTS[region]
        
        # Collect all orders and items across all pages
        all_orders = []
        all_items = []
        nt = None
        pages = 0
        
        while True:
            pages += 1
            u = f'https://{host}/orders/2026-01-01/orders?marketplaceIds={mids_str}&createdAfter={after}&MaxResultsPerPage=100'
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
            
            for o in orders:
                oid = o['orderId']
                mkt_id = o.get('salesChannel', {}).get('marketplaceId', '')
                mkt = MID_TO_MKT.get(mkt_id, 'US')
                pd = o.get('createdTime', o.get('purchaseDate', '')) or '2026-01-01T00:00:00Z'
                all_orders.append((oid, store_name, mkt, mkt_id, seller_id, 'FOUND', 'FBA', pd))
                
                for it in o.get('orderItems', []):
                    p = it.get('product', {})
                    asin = p.get('asin', '')
                    if not asin:
                        continue
                    price = float(p.get('price', {}).get('unitPrice', {}).get('amount', 0) or 0)
                    if price <= 0:
                        continue
                    all_items.append((oid, it.get('orderItemId', ''), p.get('sellerSku', ''),
                        asin, int(it.get('quantityOrdered', 1) or 1), price,
                        p.get('price', {}).get('unitPrice', {}).get('currencyCode', 'USD'),
                        (p.get('title', '') or '')[:200]))
            
            nt = data.get('pagination', {}).get('nextToken')
            if not nt:
                break
            time.sleep(0.3)
        
        # Batch INSERT orders
        if all_orders:
            execute_values(cur, """INSERT INTO orders_active (order_id, store, marketplace, marketplace_id, seller_id, status, fulfillment_type, purchase_date, last_updated)
                VALUES %s ON CONFLICT (order_id) DO NOTHING""",
                all_orders,
                template='(%s, %s, %s, %s, %s, %s, %s, %s, NOW())', page_size=500)
        
        if all_items:
            execute_values(cur, """INSERT INTO order_items_active (order_id, order_item_id, seller_sku, asin, quantity, unit_price, currency, title)
                VALUES %s ON CONFLICT (order_id, order_item_id) DO NOTHING""",
                all_items, page_size=500)
        
        conn.commit()
        elapsed = time.time() - t0
        print(f'{len(all_orders)} orders, {len(all_items)} items ({pages} pages, {elapsed:.1f}s)', flush=True)
        total_orders += len(all_orders)
        total_items += len(all_items)
        time.sleep(1)

conn.close()
print(f'\n✅ Done: {total_orders} orders, {total_items} items', flush=True)
