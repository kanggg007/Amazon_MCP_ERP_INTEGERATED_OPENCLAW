"""
Backfill orders from SP-API v2026 into orders_active + order_items_active.
"""

import os, httpx, time
from datetime import datetime, timezone, timedelta
import psycopg2

def backfill_orders(date_start_str, date_end_str=None):
    # Resolve DSN (Railway uses os.environ)
    dsn = os.environ.get('DATABASE_URL', '')
    if not dsn:
        return {"error": "no DATABASE_URL"}

    # Load .env file as fallback (deployed alongside)
    env_file = {}
    import sys
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    if os.path.exists(env_path):
        for line in open(env_path).read().splitlines():
            if line and '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                env_file[k.strip()] = v.strip()

    def _env(key):
        return os.environ.get(key, env_file.get(key, ''))

    STORES = {
        'CUCZUUS': {'nums': ['02', '05']},
        'BOOLUU': {'nums': ['03', '05']},
        'Heliumx': {'nums': ['04', '05']},
    }

    HOSTS = {'NA': 'sellingpartnerapi-na.amazon.com',
             'FE': 'sellingpartnerapi-fe.amazon.com',
             'EU': 'sellingpartnerapi-eu.amazon.com'}

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

    date_start = datetime.strptime(date_start_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    if date_end_str:
        date_end = datetime.strptime(date_end_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    else:
        date_end = date_start

    after = date_start.strftime('%Y-%m-%dT07:00:00Z')

    total_orders = 0
    total_items = 0

    for store_name, store_cfg in STORES.items():
        snums = store_cfg['nums']
        
        for region, mids_str in REGIONS:
            ref_key = {'NA': 'AMERICAS', 'FE': 'AU', 'EU': 'EU'}[region]
            
            # Try each credential number for this store
            tk = None
            for snum in snums:
                CID = _env(f'STORE_{snum}_LWA_CLIENT_ID')
                CSEC = _env(f'STORE_{snum}_LWA_CLIENT_SECRET')
                ref = _env(f'STORE_{snum}_REFRESH_TOKEN_{ref_key}')
                if not CID or not ref:
                    continue
                try:
                    r = httpx.post('https://api.amazon.com/auth/o2/token',
                        json={'grant_type': 'refresh_token', 'client_id': CID,
                              'client_secret': CSEC, 'refresh_token': ref}, timeout=15)
                    if r.status_code == 200:
                        tk = r.json()['access_token']
                        break
                except:
                    continue
            
            if not tk:
                continue

            host = HOSTS[region]
            day_orders = 0
            nt = None
            page = 0

            while True:
                page += 1
                u = f'https://{host}/orders/2026-01-01/orders?marketplaceIds={mids_str}&createdAfter={after}&MaxResultsPerPage=100'
                if nt:
                    u += '&nextToken=' + nt
                try:
                    r2 = httpx.get(u, headers={'x-amz-access-token': tk}, timeout=30)
                except Exception as e:
                    print(f"  ⚠ v2026 error {store_name}/{region} p{page}: {e}", flush=True)
                    break
                if r2.status_code != 200:
                    print(f"  ⚠ v2026 {store_name}/{region} p{page}: {r2.status_code}", flush=True)
                    break

                data = r2.json()
                orders = data.get('orders', [])
                if not orders:
                    break

                conn = psycopg2.connect(dsn)
                cur = conn.cursor()
                for o in orders:
                    oid = o['orderId']
                    mkt_id = o.get('salesChannel', {}).get('marketplaceId', '')
                    mkt = MID_TO_MKT.get(mkt_id, 'US')
                    purchase_date = o.get('purchaseDate', '')
                    if not purchase_date:
                        continue

                    # Filter by date range (LA time)
                    try:
                        pdt = datetime.fromisoformat(str(purchase_date).replace('Z', '+00:00'))
                        pdt_date = (pdt - timedelta(hours=7)).date()
                    except:
                        continue
                    if pdt_date < date_start.date() or pdt_date > date_end.date():
                        continue

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

                nt = data.get('pagination', {}).get('nextToken')
                if not nt:
                    break
                time.sleep(0.3)

            if day_orders:
                print(f"  ✓ {store_name}/{region}: {day_orders} orders ({page} pages)", flush=True)
            time.sleep(1)

    print(f"[BACKFILL] Done: {total_orders} orders, {total_items} items", flush=True)
    return {"status": "ok", "orders": total_orders, "items": total_items}
