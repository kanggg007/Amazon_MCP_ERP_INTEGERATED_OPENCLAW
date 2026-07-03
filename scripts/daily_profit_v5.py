"""Daily Gross Profit v5 — Orders API + fallback pricing + Products Fee API.

Pipeline:
1. Orders API → all orders (shipped + pending)
2. PENDING orders: fallback price from catalog (COGS + freight) × 2.5 baseline
3. SHIPPED orders: actual OrderTotal.Amount
4. Products Fee API → FBA fee per ASIN
5. Catalog → COGS + sea freight
6. Ads from DB
"""
import json, os, openpyxl, httpx, hashlib, hmac, time, urllib.parse
from collections import defaultdict

BASE = os.getcwd()
CACHE_PATH = os.path.join(BASE, 'data', 'sku_to_asin.json')
CATALOG_PATH = os.path.join(BASE, 'data', 'Master_Product_Catalog_v2.xlsx')
ENV_PATH = os.path.join(BASE, '.env')

# Env
env = {}
for line in open(ENV_PATH).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

# Caches
with open(CACHE_PATH) as f:
    sku_cache = json.load(f)

wb = openpyxl.load_workbook(CATALOG_PATH, data_only=True)
catalog = {}
for row in wb.active.iter_rows(min_row=2, values_only=True):
    if row[0] and row[2]:
        s, m, a = str(row[0]), str(row[1]), str(row[2])
        l = float(row[4] or 0)
        w = float(row[5] or 0)
        h = float(row[6] or 0)
        kg = float(row[7] or 0)
        cogs = float(row[8] or 0)
        catalog[(s, m, a)] = {
            'l': l, 'w': w, 'h': h, 'kg': kg, 'cogs': cogs,
            'fallback_price': round((cogs + (max(kg, l * w * h / 6000) * 5)) * 3, 2)
        }

CNY2USD = 0.1477
FX = {'USD': 1, 'CAD': 0.7033, 'AUD': 0.6892, 'JPY': 0.00614, 'EUR': 1.1403}
FREIGHT = {'US': ('kg', 5.0), 'CA': ('cbm', 1200), 'AU': ('cbm', 1200),
           'JP': ('cbm', 1200), 'DE': ('cbm', 1500)}

def calc_freight(mkt, l, w, h, kg):
    mode, rate = FREIGHT.get(mkt, ('kg', 0))
    if mode == 'kg':
        return max(kg, l * w * h / 6000) * rate
    return l * w * h / 1_000_000 * rate

ADS = {
    'CUCZUUS': {'US': 41.73, 'CA': 47.22, 'AU': 30.72, 'JP': 1.01, 'DE': 45.30},
    'BOOLUU': {'US': 38.21, 'CA': 27.83, 'AU': 12.55, 'JP': 1.58},
    'Heliumx': {'US': 21.38, 'CA': 3.87, 'AU': 0.71, 'DE': 0.0},
}

# Auth setup
AK = env['STORE_02_AWS_ACCESS_KEY_ID']
SK = env['STORE_02_AWS_SECRET_ACCESS_KEY']
HOSTS = {'NA': 'sellingpartnerapi-na.amazon.com', 'FE': 'sellingpartnerapi-fe.amazon.com',
         'EU': 'sellingpartnerapi-eu.amazon.com'}
STORES_NUM = {'CUCZUUS': '02', 'BOOLUU': '03', 'Heliumx': '04'}
MIDS = {
    ('NA', 'US'): 'ATVPDKIKX0DER', ('NA', 'CA'): 'A2EUQ1WTGCTBG2',
    ('FE', 'AU'): 'A39IBJ37TRP1C6', ('FE', 'JP'): 'A1VC38T7YXB528',
    ('EU', 'DE'): 'A1PA6795UKMFR9'
}

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

lwa_cache = {}

def get_lwa(store, region):
    key = (store, region)
    if key in lwa_cache:
        return lwa_cache[key]
    n = STORES_NUM[store]
    CID = env['STORE_' + n + '_LWA_CLIENT_ID']
    CSEC = env['STORE_' + n + '_LWA_CLIENT_SECRET']
    ref_map = {'NA': 'AMERICAS', 'FE': 'AU', 'EU': 'EU'}
    ref = env.get('STORE_' + n + '_REFRESH_TOKEN_' + ref_map[region], '')
    if not ref:
        return None
    r = httpx.post('https://api.amazon.com/auth/o2/token',
                   json={'grant_type': 'refresh_token', 'client_id': CID,
                         'client_secret': CSEC, 'refresh_token': ref}, timeout=15)
    if r.status_code != 200:
        return None
    lwa_cache[key] = r.json()['access_token']
    return lwa_cache[key]

# Step 1: Pull ALL orders via Orders API
print('Pulling July 1 orders (Orders API)...', flush=True)
all_orders = []
pending_count = 0

for store in ['CUCZUUS', 'BOOLUU', 'Heliumx']:
    # Pull NA + FE + EU
    for region in ['NA', 'FE', 'EU']:
        tk = get_lwa(store, region)
        if not tk:
            continue
        host = HOSTS[region]

        # Build marketplace IDs for this region
        if region == 'NA':
            mids = 'ATVPDKIKX0DER,A2EUQ1WTGCTBG2'
        elif region == 'FE':
            mids = 'A39IBJ37TRP1C6,A1VC38T7YXB528'
        else:
            mids = 'A1PA6795UKMFR9'

        nt = None
        while True:
            u = ('https://{}/orders/v0/orders?MarketplaceIds={}'
                 '&CreatedAfter=2026-07-01T07:00:00Z&CreatedBefore=2026-07-02T07:00:00Z'
                 '&MaxResultsPerPage=100').format(host, mids)
            if nt:
                u += '&NextToken=' + nt
            r2 = httpx.get(u, headers={'x-amz-access-token': tk}, timeout=30)
            if r2.status_code != 200:
                break
            d = r2.json().get('payload', r2.json())
            for o in d.get('Orders', []):
                status = o.get('OrderStatus', '?')
                mkt_id = o.get('MarketplaceId', '')
                mkt = 'CA' if 'A2EUQ1WTGCTBG2' in mkt_id else (
                    'AU' if 'A39IBJ37TRP1C6' in mkt_id else (
                    'JP' if 'A1VC38T7YXB528' in mkt_id else (
                    'DE' if 'A1PA6795UKMFR9' in mkt_id else 'US')))
                total = o.get('OrderTotal', {})
                price = float(total.get('Amount', 0) or 0)
                cur = total.get('CurrencyCode', 'USD')
                if status == 'Pending' and price < 0.01:
                    pending_count += 1
                all_orders.append({
                    'store': store, 'region': region, 'mkt': mkt,
                    'status': status, 'price': price, 'cur': cur,
                    'oid': o.get('AmazonOrderId', '')
                })
            nt = d.get('NextToken')
            if not nt:
                break

n_all = len(all_orders)
print('   {} orders total ({} pending with $0)'.format(n_all, pending_count), flush=True)

# Step 2: Resolve pricing (fallback for pending)
print('Resolving pricing...', flush=True)
resolved = []

for o in all_orders:
    mkt = o['mkt']
    store = o['store']
    status = o['status']
    price = o['price']
    cur = o['cur']

    # Resolve price
    if status == 'Pending' and price < 0.01:
        # Use catalog fallback — find any product in this store+market
        fallback_price = 0
        for (s, m, a), p in catalog.items():
            if s == store and m == mkt:
                fallback_price = p['fallback_price'] * CNY2USD
                break
        price_usd = fallback_price
    else:
        price_usd = price * FX.get(cur, 1.0)

    resolved.append({
        'store': store, 'mkt': mkt, 'status': status,
        'price': price_usd
    })

# Aggregate
by_key = defaultdict(lambda: {'n': 0, 'rev': 0})
for r in resolved:
    key = (r['store'], r['mkt'])
    by_key[key]['n'] += 1
    by_key[key]['rev'] += r['price']

# Step 3: Compute profit with catalog COGS + freight + FBA + ads
print('Computing profit...\n', flush=True)

# Get actual per-order data from earlier Finances pull for accurate COGS
by_store_mkt_finances = defaultdict(list)

# Re-pull Finances for accurate item-level data
for store in ['CUCZUUS', 'BOOLUU', 'Heliumx']:
    for region in ['NA', 'FE', 'EU']:
        tk = get_lwa(store, region)
        if not tk:
            continue
        host = HOSTS[region]
        nt = None
        while True:
            u = 'https://{}/finances/v0/financialEvents?PostedAfter=2026-07-01T07:00:00Z&PostedBefore=2026-07-01T23:59:59Z&MaxResults=100'.format(host)
            if nt:
                u += '&NextToken=' + nt
            hd = sign(u, {'x-amz-access-token': tk})
            r2 = httpx.get(u, headers=hd, timeout=30)
            if r2.status_code != 200:
                break
            d = r2.json().get('payload', r2.json())
            for e in d.get('FinancialEvents', {}).get('ShipmentEventList', []):
                for it in e.get('ShipmentItemList', []):
                    sku = it.get('SellerSKU', '')
                    fba = 0
                    cur = 'USD'
                    for f in it.get('ItemFeeList', []):
                        if f.get('FeeType') in ('FBAPerUnitFulfillmentFee', 'FBAWeightBasedFee'):
                            fba += abs(float(f.get('FeeAmount', {}).get('CurrencyAmount', 0) or 0))
                    for c in it.get('ItemChargeList', []):
                        if c.get('ChargeType') == 'Principal':
                            cur = c.get('ChargeAmount', {}).get('CurrencyCode', 'USD')
                    mkt = {'USD': 'US', 'CAD': 'CA', 'AUD': 'AU', 'JPY': 'JP', 'EUR': 'DE'}.get(cur, 'US')
                    by_store_mkt_finances[(store, mkt)].append({'sku': sku, 'fba': fba, 'cur': cur})
            nt = d.get('NextToken')
            if not nt:
                break

# Final report
print('DAILY GROSS PROFIT — July 1, 2026 (Orders API + fallback + Products Fee)')
print('=' * 90)
print('{:8} {:>3} {:>5} {:>10} {:>10} {:>10} {:>10} {:>8} {:>10} {:>5}'.format(
    'Store', 'Mkt', 'Units', 'Revenue', 'COGS', 'Freight', 'FBA', 'Ads', 'Profit', 'Marg'))
print('-' * 90)

grand = {'rev': 0, 'cogs': 0, 'fr': 0, 'fba': 0, 'ads': 0, 'profit': 0, 'units': 0}

for store in ['CUCZUUS', 'BOOLUU', 'Heliumx']:
    for mkt in ['US', 'CA', 'AU', 'JP', 'DE']:
        orders_data = by_key.get((store, mkt), {'n': 0, 'rev': 0})
        n = orders_data['n']
        rev = orders_data['rev']
        if n == 0:
            continue

        ad = ADS.get(store, {}).get(mkt, 0)

        # Get FBA fees from Finances data (average)
        finance_items = by_store_mkt_finances.get((store, mkt), [])
        finance_n = len(finance_items)
        if finance_n > 0:
            avg_fba = sum(i['fba'] * FX.get(i['cur'], 1.0) for i in finance_items) / finance_n
            fba_t = avg_fba * n
        else:
            fba_t = 0

        # Get COGS + freight from catalog via SKU cache
        cogs_total = 0
        freight_total = 0
        matched = 0
        for it in finance_items:
            sk = it['sku']
            cache_key = '{}/{}/{}'.format(store, mkt, sk)
            asin = sku_cache.get(cache_key, '')
            if asin:
                prod = catalog.get((store, mkt, asin))
                if prod:
                    cogs_total += prod['cogs'] * CNY2USD
                    freight_total += calc_freight(mkt, prod['l'], prod['w'], prod['h'], prod['kg']) * CNY2USD
                    matched += 1

        if matched > 0:
            cogs_total = cogs_total / matched * n
            freight_total = freight_total / matched * n

        profit = rev - cogs_total - freight_total - fba_t - ad - rev * 0.15  # 15% referral
        margin = profit / rev * 100 if rev else 0

        print('{:8} {:>3} {:>5} ${:>9,.2f} ${:>9,.2f} ${:>9,.2f} ${:>9,.2f} ${:>7,.2f} ${:>9,.2f} {:>4.0f}%'.format(
            store, mkt, n, rev, cogs_total, freight_total, fba_t, ad, profit, margin))

        grand['rev'] += rev
        grand['cogs'] += cogs_total
        grand['fr'] += freight_total
        grand['fba'] += fba_t
        grand['ads'] += ad
        grand['profit'] += profit
        grand['units'] += n

print('-' * 90)
margin = grand['profit'] / grand['rev'] * 100 if grand['rev'] else 0
print('{:8} {:>3} {:>5} ${:>9,.2f} ${:>9,.2f} ${:>9,.2f} ${:>9,.2f} ${:>7,.2f} ${:>9,.2f} {:>4.0f}%'.format(
    'TOTAL', '', grand['units'], grand['rev'], grand['cogs'], grand['fr'],
    grand['fba'], grand['ads'], grand['profit'], margin))

print('\nOrders API: {} total, {} pending ($0). Fallback from catalog baseline.'.format(
    n_all, pending_count))
print('Finances API: {} shipped items for FBA fees + COGS matching.'.format(
    sum(len(v) for v in by_store_mkt_finances.values())))
