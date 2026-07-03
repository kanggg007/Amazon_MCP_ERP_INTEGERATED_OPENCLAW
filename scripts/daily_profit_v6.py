"""Daily Gross Profit v6 — Orders API + Order Items thread pool + catalog.
Rate-limited: semaphore(10) + exponential backoff on 429."""
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

# LWA token cache
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

# Step 1: Pull all order IDs via Orders API
print('Step 1: Pulling order IDs (Orders API)...', flush=True)
all_orders = []

for store in ['CUCZUUS', 'BOOLUU', 'Heliumx']:
    for region in ['NA', 'FE', 'EU']:
        tk = get_lwa(store, region)
        if not tk: continue
        host = HOSTS[region]
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
                mkt_id = o.get('MarketplaceId', '')
                mkt = 'CA' if 'A2EUQ1WTGCTBG2' in mkt_id else (
                    'AU' if 'A39IBJ37TRP1C6' in mkt_id else (
                    'JP' if 'A1VC38T7YXB528' in mkt_id else (
                    'DE' if 'A1PA6795UKMFR9' in mkt_id else 'US')))
                total = o.get('OrderTotal', {})
                price = float(total.get('Amount', 0) or 0)
                cur = total.get('CurrencyCode', 'USD')
                all_orders.append({
                    'store': store, 'region': region, 'host': host, 'mkt': mkt,
                    'oid': o['AmazonOrderId'], 'status': o.get('OrderStatus', '?'),
                    'price': price, 'cur': cur
                })
            nt = d.get('NextToken')
            if not nt:
                break

print('   {} orders found'.format(len(all_orders)), flush=True)

# Step 2: Thread pool fetch Order Items
print('Step 2: Fetching Order Items (semaphore=10, exponential backoff)...', flush=True)
sem = threading.Semaphore(10)
lock = threading.Lock()
order_asins = {}  # oid -> [{'asin': ..., 'sku': ...}]
completed = 0

def fetch(o):
    global completed
    with sem:
        store = o['store']; region = o['region']; oid = o['oid']
        for attempt in range(3):
            tk = get_lwa(store, region)
            if not tk:
                return
            r2 = httpx.get('https://{}/orders/v0/orders/{}/orderItems'.format(o['host'], oid),
                           headers={'x-amz-access-token': tk}, timeout=15)
            if r2.status_code == 200:
                items = r2.json().get('payload', r2.json()).get('OrderItems', [])
                with lock:
                    order_asins[oid] = [{'asin': it.get('ASIN', ''), 'sku': it.get('SellerSKU', '')} for it in items]
                    completed += 1
                return
            elif r2.status_code == 429:
                time.sleep(2 * (2 ** attempt))
            else:
                return  # 400/403/404 — skip
        # After retries
        with lock:
            order_asins[oid] = []

threads = []
for o in all_orders:
    t = threading.Thread(target=fetch, args=(o,))
    t.start()
    threads.append(t)

for t in threads:
    t.join()

print('   Fetched {}/{} orders with ASINs'.format(sum(1 for v in order_asins.values() if v), len(all_orders)), flush=True)

# Step 3: Match to catalog + compute profit
print('Step 3: Computing profit...', flush=True)
by_mkt = defaultdict(lambda: {'n': 0, 'rev': 0, 'cogs': 0, 'frt': 0, 'fba': 0, 'matched': 0, 'matched_cogs': 0})

for o in all_orders:
    store = o['store']; mkt = o['mkt']; cur = o['cur']; price = o['price']
    status = o['status']; oid = o['oid']

    items = order_asins.get(oid, [])
    asin = items[0]['asin'] if items else ''
    sku = items[0]['sku'] if items else ''

    # Resolve price
    if status == 'Pending' and price < 0.01 and items:
        # Use catalog fallback: (COGS + freight) × 3
        prod = catalog.get((store, mkt, asin), {})
        if prod:
            fallback_cny = prod['cogs'] + calc_freight(mkt, prod['l'], prod['w'], prod['h'], prod['kg'])
            price_usd = round(fallback_cny * 3 * CNY, 2)
        else:
            price_usd = 0
    elif price > 0:
        price_usd = price * FX.get(cur, 1.0)
    else:
        price_usd = 0

    if price_usd <= 0:
        continue

    # COGS + Freight
    prod = catalog.get((store, mkt, asin))
    cogs = 0; frt = 0
    if prod:
        cogs = prod['cogs'] * CNY
        frt = calc_freight(mkt, prod['l'], prod['w'], prod['h'], prod['kg']) * CNY
        by_mkt[(store, mkt)]['matched_cogs'] += 1

    # FBA: 25% estimate or Products Fee API
    fba = price_usd * 0.25

    key = (store, mkt)
    by_mkt[key]['n'] += 1
    by_mkt[key]['rev'] += price_usd
    by_mkt[key]['cogs'] += cogs
    by_mkt[key]['frt'] += frt
    by_mkt[key]['fba'] += fba
    if items:
        by_mkt[key]['matched'] += 1

# Report
print()
print('DAILY GROSS PROFIT — July 1, 2026')
print('Store    Mkt Units    Revenue       COGS    Freight        FBA   Referral       Ads     Profit  Marg')
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
        print('{:8} {:>3} {:>5} ${:>9,.2f} ${:>9,.2f} ${:>9,.2f} ${:>9,.2f} ${:>8,.2f} ${:>8,.2f} ${:>8,.2f} {:>3.0f}%'.format(
            store, mkt, n, rev, cogs, frt, fba, ref, ad, profit, margin))
        grand_r += rev; grand_c += cogs; grand_f += frt; grand_fb += fba
        grand_a += ad; grand_p += profit; grand_u += n

grand_ref = grand_r * 0.15
print('-' * 100)
margin = grand_p / grand_r * 100 if grand_r else 0
print('{:8} {:>3} {:>5} ${:>9,.2f} ${:>9,.2f} ${:>9,.2f} ${:>9,.2f} ${:>8,.2f} ${:>8,.2f} ${:>8,.2f} {:>3.0f}%'.format(
    'TOTAL', '', grand_u, grand_r, grand_c, grand_f, grand_fb, grand_ref, grand_a, grand_p, margin))

matched_with_items = sum(1 for v in order_asins.values() if v)
total_ords = len(all_orders)
pending_w_fallback = sum(1 for o in all_orders if o['status'] == 'Pending' and o['price'] < 0.01)

print('\n{} orders ({}) via Order Items API. {} pending with fallback.'.format(
    total_ords, matched_with_items, pending_w_fallback))
