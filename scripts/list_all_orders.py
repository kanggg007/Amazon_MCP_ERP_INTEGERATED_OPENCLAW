"""List all orders July 1 across all stores+markets via v2026."""
import httpx, os, json, threading

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = {}
for line in open(os.path.join(BASE, '.env')).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

STORES = {
    'CUCZUUS': {'CID': env['STORE_02_LWA_CLIENT_ID'], 'CSEC': env['STORE_02_LWA_CLIENT_SECRET'],
                'NA': env['STORE_02_REFRESH_TOKEN_AMERICAS'], 'FE': env.get('STORE_02_REFRESH_TOKEN_AU'),
                'EU': env['STORE_02_REFRESH_TOKEN_EU']},
    'BOOLUU': {'CID': env['STORE_03_LWA_CLIENT_ID'], 'CSEC': env['STORE_03_LWA_CLIENT_SECRET'],
               'NA': env['STORE_03_REFRESH_TOKEN_AMERICAS'], 'FE': env.get('STORE_03_REFRESH_TOKEN_AU')},
    'Heliumx': {'CID': env['STORE_04_LWA_CLIENT_ID'], 'CSEC': env['STORE_04_LWA_CLIENT_SECRET'],
                'NA': env['STORE_04_REFRESH_TOKEN_AMERICAS'], 'FE': env.get('STORE_04_REFRESH_TOKEN_AU'),
                'EU': env['STORE_04_REFRESH_TOKEN_EU']},
}

HOSTS = {'NA': 'sellingpartnerapi-na.amazon.com', 'FE': 'sellingpartnerapi-fe.amazon.com',
         'EU': 'sellingpartnerapi-eu.amazon.com'}

lwa_cache = {}
lwa_lock = threading.Lock()

def get_lwa(store, region):
    key = (store, region)
    with lwa_lock:
        if key in lwa_cache:
            return lwa_cache[key]
    ref = STORES[store].get(region)
    if not ref:
        return None
    r = httpx.post('https://api.amazon.com/auth/o2/token',
        json={'grant_type': 'refresh_token', 'client_id': STORES[store]['CID'],
              'client_secret': STORES[store]['CSEC'], 'refresh_token': ref}, timeout=15)
    if r.status_code != 200:
        return None
    tk = r.json()['access_token']
    with lwa_lock:
        lwa_cache[key] = tk
    return tk

for store in ['CUCZUUS', 'BOOLUU', 'Heliumx']:
    for region in ['NA', 'FE', 'EU']:
        tk = get_lwa(store, region)
        if not tk:
            continue
        host = HOSTS[region]
        if region == 'NA':
            mids = 'ATVPDKIKX0DER,A2EUQ1WTGCTBG2'
        elif region == 'FE':
            mids = 'A39IBJ37TRP1C6,A1VC38T7YXB528'
        else:
            mids = 'A1PA6795UKMFR9'

        nt = None
        orders = []
        while True:
            u = 'https://{}/orders/2026-01-01/orders?marketplaceIds={}&createdAfter=2026-07-01T07:00:00Z&MaxResultsPerPage=100'.format(host, mids)
            if nt:
                u += '&nextToken=' + nt
            r2 = httpx.get(u, headers={'x-amz-access-token': tk}, timeout=30)
            if r2.status_code != 200:
                break
            d = r2.json()
            orders.extend(d.get('orders', []))
            nt = d.get('nextToken')
            if not nt:
                break

        if not orders:
            continue

        print('\n' + '=' * 80)
        print('{} {} — {} orders'.format(store, region, len(orders)))
        print('OrderID               Market  ASIN          SKU                      Qty   Price')
        print('-' * 80)

        for o in orders:
            for it in o.get('orderItems', []):
                p = it.get('product', {})
                asin = p.get('asin', '?')
                sku = p.get('sellerSku', '?')
                pr = p.get('price', {}).get('unitPrice', {})
                amt = float(pr.get('amount', 0) or 0)
                qty = int(it.get('quantityOrdered', 1) or 1)
                mkt_id = o.get('salesChannel', {}).get('marketplaceId', '')
                mkt = 'CA' if 'A2EUQ1WTGCTBG2' in mkt_id else (
                    'AU' if 'A39IBJ37TRP1C6' in mkt_id else (
                    'JP' if 'A1VC38T7YXB528' in mkt_id else (
                    'DE' if 'A1PA6795UKMFR9' in mkt_id else 'US')))
                print('{}  {:>6}  {}  {:25}  {:>2}  ${:>7.2f}'.format(
                    o['orderId'], mkt, asin, sku[:25], qty, amt))
