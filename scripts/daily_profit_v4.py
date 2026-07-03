"""Daily Gross Profit v4 — Finances API + SKU cache + catalog + live FX + ads."""
import json, os, openpyxl, httpx, hashlib, hmac, time, urllib.parse
from collections import defaultdict

BASE = os.getcwd()
CACHE_PATH = os.path.join(BASE, 'data', 'sku_to_asin.json')
CATALOG_PATH = os.path.join(BASE, 'data', 'Master_Product_Catalog_v2.xlsx')
ENV_PATH = os.path.join(BASE, '.env')

# Load env
env = {}
for line in open(ENV_PATH).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

# Load caches
with open(CACHE_PATH) as f:
    sku_cache = json.load(f)

wb = openpyxl.load_workbook(CATALOG_PATH, data_only=True)
catalog = {}
for row in wb.active.iter_rows(min_row=2, values_only=True):
    if row[0] and row[2]:
        s, m, a = str(row[0]), str(row[1]), str(row[2])
        catalog[(s, m, a)] = {
            'l': float(row[4] or 0), 'w': float(row[5] or 0),
            'h': float(row[6] or 0), 'kg': float(row[7] or 0),
            'cogs': float(row[8] or 0)
        }

CNY2USD = 0.1477
FX = {'USD': 1, 'CAD': 0.7033, 'AUD': 0.6892, 'JPY': 0.00614, 'EUR': 1.1403}
CUR2MKT = {'USD': 'US', 'CAD': 'CA', 'AUD': 'AU', 'JPY': 'JP', 'EUR': 'DE'}
FREIGHT = {
    'US': ('kg', 5.0), 'CA': ('cbm', 1200), 'AU': ('cbm', 1200),
    'JP': ('cbm', 1200), 'DE': ('cbm', 1500)
}

def calc_freight(mkt, l, w, h, kg):
    mode, rate = FREIGHT.get(mkt, ('kg', 0))
    if mode == 'kg':
        return max(kg, l * w * h / 6000) * rate
    return l * w * h / 1_000_000 * rate

# Ads from DB (July 1 actual)
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

STORES = {
    'CUCZUUS': '02', 'BOOLUU': '03', 'Heliumx': '04'
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
    n = STORES[store]
    CID = env['STORE_' + n + '_LWA_CLIENT_ID']
    CSEC = env['STORE_' + n + '_LWA_CLIENT_SECRET']
    ref = env.get('STORE_' + n + '_REFRESH_TOKEN_AMERICAS' if region == 'NA' else
                  'STORE_' + n + '_REFRESH_TOKEN_AU' if region == 'FE' else
                  'STORE_' + n + '_REFRESH_TOKEN_EU', '')
    if not ref:
        return None
    r = httpx.post('https://api.amazon.com/auth/o2/token',
                   json={'grant_type': 'refresh_token', 'client_id': CID,
                         'client_secret': CSEC, 'refresh_token': ref}, timeout=15)
    if r.status_code != 200:
        return None
    lwa_cache[key] = r.json()['access_token']
    return lwa_cache[key]

# Pull orders via Finances API
print('Pulling July 1 orders via Finances API...', flush=True)
data = defaultdict(list)

for store in ['CUCZUUS', 'BOOLUU', 'Heliumx']:
    for region in ['NA', 'FE', 'EU']:
        tk = get_lwa(store, region)
        if not tk:
            continue
        host = HOSTS[region]
        nt = None
        while True:
            u = 'https://{}/finances/v0/financialEvents?PostedAfter=2026-07-01T00:00:00Z&PostedBefore=2026-07-01T23:59:59Z&MaxResults=100'.format(host)
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
                    price = 0
                    fba = 0
                    cur = 'USD'
                    for c in it.get('ItemChargeList', []):
                        if c.get('ChargeType') == 'Principal':
                            price = float(c.get('ChargeAmount', {}).get('CurrencyAmount', 0) or 0)
                            cur = c.get('ChargeAmount', {}).get('CurrencyCode', 'USD')
                    for f in it.get('ItemFeeList', []):
                        if f.get('FeeType') in ('FBAPerUnitFulfillmentFee', 'FBAWeightBasedFee'):
                            fba += abs(float(f.get('FeeAmount', {}).get('CurrencyAmount', 0) or 0))
                    if price > 0:
                        mkt = CUR2MKT.get(cur, 'US')
                        data[(store, mkt)].append({
                            'sku': sku, 'price': price, 'fba': fba, 'cur': cur
                        })
            nt = d.get('NextToken')
            if not nt:
                break

total_orders = sum(len(v) for v in data.values())
print('   {} orders total'.format(total_orders), flush=True)

# Compute profit per store-market
print('\nDAILY GROSS PROFIT — July 1, 2026 (before storage & disposal)')
print('=' * 90)
print('{:8} {:>3} {:>5} {:>10} {:>10} {:>10} {:>10} {:>8} {:>10} {:>5}'.format(
    'Store', 'Mkt', 'Units', 'Revenue', 'COGS', 'Freight', 'FBA', 'Ads', 'Profit', 'Marg'))
print('-' * 90)

grand = {'rev': 0, 'cogs': 0, 'fr': 0, 'fba': 0, 'ads': 0, 'profit': 0, 'units': 0}

for store in ['CUCZUUS', 'BOOLUU', 'Heliumx']:
    for mkt in ['US', 'CA', 'AU', 'JP', 'DE']:
        items = data.get((store, mkt), [])
        if not items:
            continue
        n = len(items)
        rev = sum(i['price'] * FX[i['cur']] for i in items)
        fba_t = sum(i['fba'] * FX[i['cur']] for i in items)
        ad = ADS.get(store, {}).get(mkt, 0)

        # Match catalog via SKU cache
        cogs_total = 0
        freight_total = 0
        matched = 0
        for it in items:
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

        profit = rev - cogs_total - freight_total - fba_t - ad
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

n_matched = sum(1 for v in data.values() for it in v if sku_cache.get('{}/{}/{}'.format(
    'CUCZUUS', CUR2MKT.get(it['cur'], 'US'), it['sku']), ''))
print('\nSKU cache: {} mappings. Catalog: {} products. Orders: {}'.format(
    len(sku_cache), len(catalog), total_orders))
print('NOTE: FBA fees + Freight from logistics config. COGS from catalog (CNY->USD).')
