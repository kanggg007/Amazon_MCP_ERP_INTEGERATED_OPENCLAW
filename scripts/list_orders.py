"""List BOOLUU US orders July 1 with product names."""
import httpx, json, os, openpyxl

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = {}
for line in open(os.path.join(BASE, '.env')).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

# Catalog
wb = openpyxl.load_workbook(os.path.join(BASE, 'data', 'Master_Product_Catalog_v2.xlsx'), data_only=True)
cat_by_asin = {}
cat_by_sku = {}  # SKU in title?
for row in wb.active.iter_rows(min_row=2, values_only=True):
    if row[0] and row[2] and row[0] == 'BOOLUU' and row[1] == 'US':
        asin = str(row[2])
        title = str(row[3]) if row[3] else ''
        l = float(row[4] or 0); w = float(row[5] or 0); h = float(row[6] or 0); kg = float(row[7] or 0)
        cogs = float(row[8] or 0)
        cat_by_asin[asin] = {'title': title, 'l': l, 'w': w, 'h': h, 'kg': kg, 'cogs': cogs}

# SKU cache
with open(os.path.join(BASE, 'data', 'sku_to_asin.json')) as f:
    sku_cache = json.load(f)

# BOOLUU US Orders
CID = env['STORE_03_LWA_CLIENT_ID']
CSEC = env['STORE_03_LWA_CLIENT_SECRET']
REF = env['STORE_03_REFRESH_TOKEN_AMERICAS']
r = httpx.post('https://api.amazon.com/auth/o2/token', json={
    'grant_type': 'refresh_token', 'client_id': CID, 'client_secret': CSEC, 'refresh_token': REF}, timeout=15)
tk = r.json()['access_token']

orders = []
nt = None
while True:
    u = 'https://sellingpartnerapi-na.amazon.com/orders/v0/orders?MarketplaceIds=ATVPDKIKX0DER&CreatedAfter=2026-07-01T07:00:00Z&CreatedBefore=2026-07-02T07:00:00Z&MaxResultsPerPage=100'
    if nt:
        u += '&NextToken=' + nt
    r2 = httpx.get(u, headers={'x-amz-access-token': tk}, timeout=30)
    if r2.status_code != 200:
        break
    d = r2.json().get('payload', r2.json())
    for o in d.get('Orders', []):
        oid = o.get('AmazonOrderId', '?')
        status = o.get('OrderStatus', '?')
        total = o.get('OrderTotal', {})
        amt = float(total.get('Amount', 0) or 0)
        cur = total.get('CurrencyCode', '?')
        orders.append({'oid': oid, 'status': status, 'price': amt, 'cur': cur})
    nt = d.get('NextToken')
    if not nt:
        break

# Pull SKUs from Finances API for pricing
import hashlib, hmac, time, urllib.parse
AK = env['STORE_02_AWS_ACCESS_KEY_ID']
SK = env['STORE_02_AWS_SECRET_ACCESS_KEY']

def sign(url, headers):
    p = urllib.parse.urlparse(url)
    ad = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    ds = time.strftime('%Y%m%d', time.gmtime())
    headers['x-amz-date'] = ad
    headers['host'] = p.hostname
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

r3 = httpx.post('https://api.amazon.com/auth/o2/token', json={
    'grant_type': 'refresh_token', 'client_id': CID, 'client_secret': CSEC, 'refresh_token': REF}, timeout=15)
tk2 = r3.json()['access_token']

sku_prices = {}
nt = None
while True:
    u = 'https://sellingpartnerapi-na.amazon.com/finances/v0/financialEvents?PostedAfter=2026-07-01T07:00:00Z&PostedBefore=2026-07-02T07:00:00Z&MaxResults=100'
    if nt:
        u += '&NextToken=' + nt
    h = sign(u, {'x-amz-access-token': tk2})
    r2 = httpx.get(u, headers=h, timeout=30)
    if r2.status_code != 200:
        break
    d = r2.json().get('payload', r2.json())
    for e in d.get('FinancialEvents', {}).get('ShipmentEventList', []):
        for it in e.get('ShipmentItemList', []):
            sku = it.get('SellerSKU', '')
            price = 0
            for c in it.get('ItemChargeList', []):
                if c.get('ChargeType') == 'Principal':
                    price = float(c.get('ChargeAmount', {}).get('CurrencyAmount', 0) or 0)
            if price > 0:
                sku_prices[sku] = max(sku_prices.get(sku, 0), price)
    nt = d.get('NextToken')
    if not nt:
        break

# Display
CNY = 0.1477
print('BOOLUU US Orders — July 1, 2026 (LA time)')
print('Order ID              Status     Price   SKU                         Product')
print('-' * 110)
for o in orders:
    oid = o['oid']
    status = o['status']
    price = o['price'] if o['price'] > 0 else 0
    # Find matching product
    best_sku = ''
    best_price = price if price > 0 else 0
    best_asin = ''
    best_title = ''
    if best_price > 0:
        for sk, p in sku_prices.items():
            if abs(p - best_price) < 0.50:
                best_sku = sk
                asin = sku_cache.get('BOOLUU/US/' + sk, '')
                prod = cat_by_asin.get(asin, {})
                best_title = prod.get('title', '')[:50]
                best_asin = asin
                break
    status_short = status[:10].ljust(10)
    print('{}  {}  ${:>6.2f}  {:25}  {}'.format(oid, status_short, best_price, best_sku[:25], best_title))

print('-' * 110)
print('{} orders'.format(len(orders)))
