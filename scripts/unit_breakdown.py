"""Show all units from July 1 with ASIN, COGS, Freight."""
import httpx, os, openpyxl, threading, time, urllib.parse, hashlib, hmac, json

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = {}
for line in open(os.path.join(BASE, '.env')).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

CNY = 0.1477
FREIGHT = {'US': ('kg', 5.0), 'CA': ('cbm', 1200), 'AU': ('cbm', 1200),
           'JP': ('cbm', 1200), 'DE': ('cbm', 1500)}

wb = openpyxl.load_workbook(os.path.join(BASE, 'data', 'Master_Product_Catalog_v2.xlsx'), data_only=True)
catalog = {}
for row in wb.active.iter_rows(min_row=2, values_only=True):
    if row[0] and row[2]:
        s, m, a = str(row[0]), str(row[1]), str(row[2])
        l = float(row[4] or 0); w = float(row[5] or 0); h = float(row[6] or 0); k = float(row[7] or 0)
        c = float(row[8] or 0)
        mode, rate = FREIGHT.get(m, ('kg', 0))
        if l and w and h and k:
            if mode == 'kg': frt = max(k, l * w * h / 6000) * rate * CNY
            else: frt = l * w * h / 1_000_000 * rate * CNY
        else: frt = 0
        catalog[(s, m, a)] = {'cogs': c * CNY, 'frt': frt, 'title': str(row[3])[:60] if row[3] else ''}

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
        if key in lwa_cache: return lwa_cache[key]
    ref = STORES[store].get(region)
    if not ref: return None
    r = httpx.post('https://api.amazon.com/auth/o2/token',
        json={'grant_type': 'refresh_token', 'client_id': STORES[store]['CID'],
              'client_secret': STORES[store]['CSEC'], 'refresh_token': ref}, timeout=15)
    if r.status_code != 200: return None
    tk = r.json()['access_token']
    with lwa_lock: lwa_cache[key] = tk
    return tk

def sign(url, headers):
    AK = env['STORE_02_AWS_ACCESS_KEY_ID']; SK = env['STORE_02_AWS_SECRET_ACCESS_KEY']
    p = urllib.parse.urlparse(url)
    ad = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()); ds = time.strftime('%Y%m%d', time.gmtime())
    headers['x-amz-date'] = ad; headers['host'] = p.hostname
    cu = urllib.parse.quote(p.path, safe='/') or '/'
    ch = ''.join('{}:{}\n'.format(k.lower(), v.strip()) for k, v in sorted(headers.items()) if k.lower() != 'authorization')
    sh = ';'.join(sorted(k.lower() for k in headers if k.lower() != 'authorization'))
    cr = '\n'.join(['GET', cu, p.query, ch, sh, hashlib.sha256(b'').hexdigest()])
    cs = '{}/us-east-1/execute-api/aws4_request'.format(ds)
    st = '\n'.join(['AWS4-HMAC-SHA256', ad, cs, hashlib.sha256(cr.encode()).hexdigest()])
    def h8(k, m):
        if isinstance(k, str): k = k.encode()
        return hmac.new(k, m.encode(), hashlib.sha256).digest()
    headers['Authorization'] = 'AWS4-HMAC-SHA256 Credential={}/{},SignedHeaders={},Signature={}'.format(
        AK, cs, sh, hmac.new(h8(h8(h8(h8('AWS4' + SK, ds), 'us-east-1'), 'execute-api'), 'aws4_request'), st.encode(), hashlib.sha256).hexdigest())
    return headers

# Step 1: v0 statuses
print('Getting statuses...')
status_map = {}
for store in STORES:
    for region in ['NA', 'FE', 'EU']:
        tk = get_lwa(store, region)
        if not tk: continue
        host = HOSTS[region]
        if region == 'NA': mids = 'ATVPDKIKX0DER,A2EUQ1WTGCTBG2'
        elif region == 'FE': mids = 'A39IBJ37TRP1C6,A1VC38T7YXB528'
        else: mids = 'A1PA6795UKMFR9'
        nt = None
        while True:
            u = 'https://{}/orders/v0/orders?MarketplaceIds={}&CreatedAfter=2026-07-01T07:00:00Z&CreatedBefore=2026-07-02T07:00:00Z&MaxResultsPerPage=100'.format(host, mids)
            if nt: u += '&NextToken=' + nt
            r2 = httpx.get(u, headers={'x-amz-access-token': tk}, timeout=30)
            if r2.status_code != 200: break
            for o in r2.json().get('payload', r2.json()).get('Orders', []):
                status_map[o['AmazonOrderId']] = o.get('OrderStatus', '?')
            nt = r2.json().get('payload', r2.json()).get('NextToken')
            if not nt: break

# Step 2: v2026 items
print('Pulling items...')
all_items = []
for store in STORES:
    for region in ['NA', 'FE', 'EU']:
        tk = get_lwa(store, region)
        if not tk: continue
        host = HOSTS[region]
        if region == 'NA': mids = 'ATVPDKIKX0DER,A2EUQ1WTGCTBG2'
        elif region == 'FE': mids = 'A39IBJ37TRP1C6,A1VC38T7YXB528'
        else: mids = 'A1PA6795UKMFR9'
        nt = None
        while True:
            u = 'https://{}/orders/2026-01-01/orders?marketplaceIds={}&createdAfter=2026-07-01T07:00:00Z&MaxResultsPerPage=100'.format(host, mids)
            if nt: u += '&nextToken=' + nt
            r2 = httpx.get(u, headers={'x-amz-access-token': tk}, timeout=30)
            if r2.status_code != 200: break
            for o in r2.json().get('orders', []):
                oid = o['orderId']
                if oid not in status_map: continue  # Filter to v0 date range
                mkt_id = o.get('salesChannel', {}).get('marketplaceId', '')
                mkt = 'CA' if 'A2EUQ1WTGCTBG2' in mkt_id else (
                    'AU' if 'A39IBJ37TRP1C6' in mkt_id else (
                    'JP' if 'A1VC38T7YXB528' in mkt_id else (
                    'DE' if 'A1PA6795UKMFR9' in mkt_id else 'US')))
                status = status_map.get(oid, '?')
                for it in o.get('orderItems', []):
                    p = it.get('product', {})
                    asin = p.get('asin', '')
                    sku = p.get('sellerSku', '')
                    price = float(p.get('price', {}).get('unitPrice', {}).get('amount', 0) or 0)
                    qty = int(it.get('quantityOrdered', 1) or 1)
                    if price > 0 and status != 'Canceled':
                        prod = catalog.get((store, mkt, asin), {})
                        cogs = prod.get('cogs', 0)
                        frt = prod.get('frt', 0)
                        all_items.append({'store': store, 'mkt': mkt, 'oid': oid, 'status': status,
                                         'asin': asin, 'sku': sku, 'price': price, 'qty': qty, 'cogs': cogs, 'frt': frt})
            nt = r2.json().get('nextToken')
            if not nt: break

# Print
print('\nAll 140 units — July 1, 2026')
print('Store      Mkt OrderID               Status   ASIN          SKU                    Qty  Price   COGS   Freight')
print('-' * 110)
total_price = total_cogs = total_frt = total_qty = 0
for it in sorted(all_items, key=lambda x: (x['store'], x['mkt'], x['oid'])):
    print('{:10} {:>3} {}  {}  {}  {:25}  {:>2}  ${:>5.2f}  ${:>5.2f}  ${:>6.2f}'.format(
        it['store'], it['mkt'], it['oid'], it['status'][:7].ljust(7),
        it['asin'][:12], it['sku'][:25], it['qty'], it['price'], it['cogs'], it['frt']))
    total_price += it['price'] * it['qty']; total_cogs += it['cogs'] * it['qty']
    total_frt += it['frt'] * it['qty']; total_qty += it['qty']

print('-' * 110)
print('TOTAL: {} units, Rev=${:,.2f}, COGS=${:,.2f}, Freight=${:,.2f}'.format(
    total_qty, total_price, total_cogs, total_frt))
