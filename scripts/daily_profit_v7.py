"""Daily Gross Profit v7 — Orders API v2026-01-01 (includes orderItems natively).

One API call per store+region. ASIN+SKU+title+price in response.
No extra Order Items calls needed.
"""
import json, os, openpyxl, httpx, time, threading
from collections import defaultdict

BASE = os.getcwd()
CACHE_PATH = os.path.join(BASE, 'data', 'sku_to_asin.json')
CATALOG_PATH = os.path.join(BASE, 'data', 'Master_Product_Catalog_v2.xlsx')
ENV_PATH = os.path.join(BASE, '.env')

env = {}
for line in open(ENV_PATH).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

wb = openpyxl.load_workbook(CATALOG_PATH, data_only=True)
catalog = {}
for row in wb.active.iter_rows(min_row=2, values_only=True):
    if row[0] and row[2]:
        s, m, a = str(row[0]), str(row[1]), str(row[2])
        catalog[(s, m, a)] = {
            'l': float(row[4] or 0), 'w': float(row[5] or 0),
            'h': float(row[6] or 0), 'kg': float(row[7] or 0),
            'cogs': float(row[8] or 0), 'title': str(row[3])[:60] if row[3] else ''
        }

CNY = 0.1477
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

HOSTS = {'NA': 'sellingpartnerapi-na.amazon.com', 'FE': 'sellingpartnerapi-fe.amazon.com',
         'EU': 'sellingpartnerapi-eu.amazon.com'}
STORES_NUM = {'CUCZUUS': '02', 'BOOLUU': '03', 'Heliumx': '04'}

lwa_cache = {}
lwa_lock = threading.Lock()

def get_lwa(store, region):
    key = (store, region)
    with lwa_lock:
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
    tk = r.json()['access_token']
    with lwa_lock:
        lwa_cache[key] = tk
    return tk

# Step 1: Pull all orders via v2026-01-01 (includes orderItems)
print('v7: Orders API v2026-01-01 with orderItems...', flush=True)
all_orders = []

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
        while True:
            u = ('https://{}/orders/2026-01-01/orders?marketplaceIds={}'
                 '&createdAfter=2026-07-01T07:00:00Z&MaxResultsPerPage=100').format(host, mids)
            if nt:
                u += '&nextToken=' + nt
            r2 = httpx.get(u, headers={'x-amz-access-token': tk}, timeout=30)
            if r2.status_code != 200:
                break
            d = r2.json()
            orders = d.get('orders', [])
            for o in orders:
                mkt_id = o.get('salesChannel', {}).get('marketplaceId', '')
                mkt = 'CA' if 'A2EUQ1WTGCTBG2' in mkt_id else (
                    'AU' if 'A39IBJ37TRP1C6' in mkt_id else (
                    'JP' if 'A1VC38T7YXB528' in mkt_id else (
                    'DE' if 'A1PA6795UKMFR9' in mkt_id else 'US')))

                total = o.get('orderTotal', {})
                price = float(total.get('amount', 0) or 0)
                cur = total.get('currencyCode', 'USD')
                status = o.get('orderStatus', '?')

                items = o.get('orderItems', [])
                it = items[0] if items else {}
                p = it.get('product', {})
                asin = p.get('asin', '') if items else ''
                sku = p.get('sellerSku', '') if items else ''
                title = p.get('title', '') if items else ''

                # v2026: price from orderItems, sum all items × quantity
                price = 0
                if items:
                    for it in items:
                        pr = it.get('product', {}).get('price', {}).get('unitPrice', {})
                        qty = int(it.get('quantityOrdered', 1) or 1)
                        price += float(pr.get('amount', 0) or 0) * qty
                        cur = pr.get('currencyCode', 'USD')
                status = 'FOUND'  # v2026 doesn't expose status. Fixed below

                all_orders.append({
                    'store': store, 'region': region, 'mkt': mkt,
                    'oid': o['orderId'], 'status': status, 'price': price, 'cur': cur,
                    'asin': asin, 'sku': sku, 'title': title,
                    'host': host
                })

            nt = d.get('nextToken')
            if not nt:
                break

print('   {} orders found'.format(len(all_orders)), flush=True)

# Step 1b: Get status from v0 (v2026 doesn't expose orderStatus)
print('   Fetching status from v0...', flush=True)
status_map = {}  # (store, region, host) -> {orderId: status}
for store in ['CUCZUUS', 'BOOLUU', 'Heliumx']:
    for region in ['NA', 'FE', 'EU']:
        tk = get_lwa(store, region)
        if not tk: continue
        host = HOSTS[region]
        if region == 'NA': mids_v0 = 'ATVPDKIKX0DER,A2EUQ1WTGCTBG2'
        elif region == 'FE': mids_v0 = 'A39IBJ37TRP1C6,A1VC38T7YXB528'
        else: mids_v0 = 'A1PA6795UKMFR9'
        nt_v0 = None
        while True:
            u_v0 = ('https://{}/orders/v0/orders?MarketplaceIds={}&CreatedAfter=2026-07-01T07:00:00Z'
                     '&CreatedBefore=2026-07-02T07:00:00Z&MaxResultsPerPage=100').format(host, mids_v0)
            if nt_v0: u_v0 += '&NextToken=' + nt_v0
            r_v0 = httpx.get(u_v0, headers={'x-amz-access-token': tk}, timeout=30)
            if r_v0.status_code != 200: break
            for o_v0 in r_v0.json().get('payload', r_v0.json()).get('Orders', []):
                status_map[o_v0['AmazonOrderId']] = o_v0.get('OrderStatus', '?')
            nt_v0 = r_v0.json().get('payload', r_v0.json()).get('NextToken')
            if not nt_v0: break

default_status = '?'
# Filter to only orders from v0 date range (exact July 1)
all_orders = [o for o in all_orders if o['oid'] in status_map]
for o in all_orders:
    o['status'] = status_map.get(o['oid'], default_status)

shipped = sum(1 for o in all_orders if o['status'] == 'Shipped')
pending = sum(1 for o in all_orders if o['status'] == 'Pending')
cancelled = sum(1 for o in all_orders if o['status'] == 'Canceled')
print('   {} shipped, {} pending, {} cancelled'.format(shipped, pending, cancelled), flush=True)

# Step 2: Parallel Products Fee API for FBA fees (unique ASINs only)
print('Fetching FBA fees via Products Fee API...', flush=True)
fba_cache = {}  # (store, mkt, asin) -> fee_usd
unique_asins = set()
for o in all_orders:
    if o['asin']:
        unique_asins.add((o['store'], o['mkt'], o['asin'], o['cur']))

sem = threading.Semaphore(10)
fba_lock = threading.Lock()
fba_count = 0

def fetch_fba(store, mkt, asin, cur):
    global fba_count
    with sem:
        mid_map = {'US': 'ATVPDKIKX0DER', 'CA': 'A2EUQ1WTGCTBG2', 'AU': 'A39IBJ37TRP1C6', 'JP': 'A1VC38T7YXB528', 'DE': 'A1PA6795UKMFR9'}
        mid = mid_map.get(mkt, 'ATVPDKIKX0DER')
        region = 'NA' if mkt in ('US', 'CA') else ('FE' if mkt in ('AU', 'JP') else 'EU')
        tk = get_lwa(store, region)
        if not tk: return
        body = {'FeesEstimateRequest': {'MarketplaceId': mid, 'IsAmazonFulfilled': True,
            'PriceToEstimateFees': {'ListingPrice': {'CurrencyCode': cur, 'Amount': 29.99}}, 'Identifier': asin}}
        r2 = httpx.post('https://{}/products/fees/v0/items/{}/feesEstimate'.format(HOSTS[region], asin),
            headers={'x-amz-access-token': tk, 'Content-Type': 'application/json'}, json=body, timeout=15)
        if r2.status_code == 200:
            d = r2.json().get('payload', r2.json())
            fe = d.get('FeesEstimateResult', {}).get('FeesEstimate', {})
            fba = 0
            for f in fe.get('FeeDetailList', []):
                if f.get('FeeType') == 'FBAFees':
                    fba = float(f.get('FinalFee', {}).get('Amount', 0) or 0)
            with fba_lock:
                fba_cache[(store, mkt, asin)] = fba
                fba_count += 1

fba_threads = []
for store, mkt, asin, cur in unique_asins:
    t = threading.Thread(target=fetch_fba, args=(store, mkt, asin, cur))
    t.start(); fba_threads.append(t)
for t in fba_threads: t.join()
print('   Fetched {}/{} FBA fees'.format(fba_count, len(unique_asins)), flush=True)

# Step 3: Match to catalog + compute
print('Computing profit...', flush=True)
by_mkt = defaultdict(lambda: {'n': 0, 'rev': 0, 'cogs': 0, 'frt': 0, 'fba': 0})

for o in all_orders:
    store = o['store']; mkt = o['mkt']; cur = o['cur']; price = o['price']
    status = o['status']; asin = o['asin']

    # v2026: prices from orderItems product.price.unitPrice
    if price > 0:
        price_usd = price * FX.get(cur, 1.0)
    else:
        # Fallback for $0 orders
        prod = catalog.get((store, mkt, asin), {})
        if prod:
            fallback_cny = prod['cogs'] + calc_freight(mkt, prod['l'], prod['w'], prod['h'], prod['kg'])
            price_usd = round(fallback_cny * 3 * CNY, 2)
        else:
            price_usd = 30

    if price_usd <= 0:
        continue

    # COGS + Freight from catalog
    prod = catalog.get((store, mkt, asin))
    cogs = 0; frt = 0
    if prod:
        cogs = prod['cogs'] * CNY
        frt = calc_freight(mkt, prod['l'], prod['w'], prod['h'], prod['kg']) * CNY

    # FBA: use Products Fee API value, fallback to estimate
    fba = fba_cache.get((store, mkt, asin), price_usd * 0.25)

    key = (store, mkt)
    by_mkt[key]['n'] += 1
    by_mkt[key]['rev'] += price_usd
    by_mkt[key]['cogs'] += cogs
    by_mkt[key]['frt'] += frt
    by_mkt[key]['fba'] += fba

# Report
print()
print('DAILY GROSS PROFIT — July 1, 2026 (v7: Orders v2026)')
print('{:8} {:>3} {:>5} {:>10} {:>10} {:>10} {:>10} {:>8} {:>8} {:>10} {:>5}'.format(
    'Store', 'Mkt', 'Units', 'Revenue', 'COGS', 'Freight', 'FBA', 'Referral', 'Ads', 'Profit', 'Marg'))
print('-' * 100)

grand_r = grand_c = grand_f = grand_fb = grand_a = grand_p = grand_u = 0

for store in ['CUCZUUS', 'BOOLUU', 'Heliumx']:
    for mkt in ['US', 'CA', 'AU', 'JP', 'DE']:
        d = by_mkt.get((store, mkt), {'n': 0})
        n = d['n']
        if n == 0: continue
        rev = d['rev']; cogs = d['cogs']; frt = d['frt']; fba = d['fba']
        ref = rev * 0.15; ad = ADS.get(store, {}).get(mkt, 0)
        profit = rev - cogs - frt - fba - ref - ad
        margin = profit / rev * 100 if rev else 0
        print('{:8} {:>3} {:>5} ${:>9,.2f} ${:>9,.2f} ${:>9,.2f} ${:>9,.2f} ${:>7,.2f} ${:>7,.2f} ${:>9,.2f} {:>4.0f}%'.format(
            store, mkt, n, rev, cogs, frt, fba, ref, ad, profit, margin))
        grand_r += rev; grand_c += cogs; grand_f += frt; grand_fb += fba
        grand_a += ad; grand_p += profit; grand_u += n

grand_ref = grand_r * 0.15
print('-' * 100)
margin = grand_p / grand_r * 100 if grand_r else 0
print('{:8} {:>3} {:>5} ${:>9,.2f} ${:>9,.2f} ${:>9,.2f} ${:>9,.2f} ${:>7,.2f} ${:>7,.2f} ${:>9,.2f} {:>4.0f}%'.format(
    'TOTAL', '', grand_u, grand_r, grand_c, grand_f, grand_fb, grand_ref, grand_a, grand_p, margin))

with_asin = sum(1 for o in all_orders if o['asin'])
print('\nv2026-01-01: {} orders, {} with ASIN ({}%)'.format(
    len(all_orders), with_asin, with_asin * 100 // len(all_orders) if all_orders else 0))
