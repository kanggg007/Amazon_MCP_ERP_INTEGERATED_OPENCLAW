"""Daily Gross Profit — Orders API with fallback pricing for PENDING orders."""
import httpx, json, os, time, openpyxl
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = {}
for line in open(os.path.join(BASE, '.env')).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        ENV[k.strip()] = v.strip()

CNY2USD = 0.1477
FX2USD = {'USD': 1.0, 'CAD': 0.7033, 'AUD': 0.6892, 'JPY': 0.00614, 'EUR': 1.1403}
CUR2MKT = {'USD': 'US', 'CAD': 'CA', 'AUD': 'AU', 'JPY': 'JP', 'EUR': 'DE'}
MKT_IDS = {'US': 'ATVPDKIKX0DER', 'CA': 'A2EUQ1WTGCTBG2',
           'AU': 'A39IBJ37TRP1C6', 'JP': 'A1VC38T7YXB528', 'DE': 'A1PA6795UKMFR9'}

STORES = {
    'CUCZUUS': {'CID': ENV['STORE_02_LWA_CLIENT_ID'], 'CSEC': ENV['STORE_02_LWA_CLIENT_SECRET'],
                'NA': ENV['STORE_02_REFRESH_TOKEN_AMERICAS'], 'FE': ENV.get('STORE_02_REFRESH_TOKEN_AU'),
                'EU': ENV['STORE_02_REFRESH_TOKEN_EU']},
    'BOOLUU': {'CID': ENV['STORE_03_LWA_CLIENT_ID'], 'CSEC': ENV['STORE_03_LWA_CLIENT_SECRET'],
               'NA': ENV['STORE_03_REFRESH_TOKEN_AMERICAS'], 'FE': ENV.get('STORE_03_REFRESH_TOKEN_AU')},
    'Heliumx': {'CID': ENV['STORE_04_LWA_CLIENT_ID'], 'CSEC': ENV['STORE_04_LWA_CLIENT_SECRET'],
                'NA': ENV['STORE_04_REFRESH_TOKEN_AMERICAS'], 'FE': ENV.get('STORE_04_REFRESH_TOKEN_AU'),
                'EU': ENV['STORE_04_REFRESH_TOKEN_EU']},
}
HOSTS = {'NA': 'sellingpartnerapi-na.amazon.com', 'FE': 'sellingpartnerapi-fe.amazon.com',
         'EU': 'sellingpartnerapi-eu.amazon.com'}
REGION_HOSTS = {'NA': HOSTS['NA'], 'FE': HOSTS['FE'], 'EU': HOSTS['EU']}

FREIGHT_RATES = {'US': {'mode': 'kg', 'rate': 5.0}, 'CA': {'mode': 'cbm', 'rate': 1200},
                 'AU': {'mode': 'cbm', 'rate': 1200}, 'JP': {'mode': 'cbm', 'rate': 1200},
                 'DE': {'mode': 'cbm', 'rate': 1500}}

def calc_freight(mkt, l, w, h, kg):
    cfg = FREIGHT_RATES.get(mkt, {})
    if cfg.get('mode') == 'kg':
        return max(kg, l * w * h / 6000) * cfg.get('rate', 0)
    return l * w * h / 1_000_000 * cfg.get('rate', 0)

# Load catalog with fallback prices
print('Loading catalog...', flush=True)
wb = openpyxl.load_workbook(os.path.join(BASE, 'data', 'Master_Product_Catalog_v2.xlsx'), data_only=True)
catalog = {}
for row in wb.active.iter_rows(min_row=2, values_only=True):
    if row[0] and row[2]:
        store, mkt, asin = str(row[0]), str(row[1]), str(row[2])
        catalog[(store, mkt, asin)] = {
            'l': float(row[4] or 0), 'w': float(row[5] or 0), 'h': float(row[6] or 0),
            'kg': float(row[7] or 0), 'cogs': float(row[8] or 0),
            # Fallback: estimate listing price as 40% above COGS + freight
            'fallback_price': round((float(row[8] or 0) + calc_freight(mkt, float(row[4] or 0),
                float(row[5] or 0), float(row[6] or 0), float(row[7] or 0))) / 0.4 * 6.77, 2)  # CNY price estimate
        }
catalog_count = len(catalog)
print('   {} products'.format(catalog_count), flush=True)

lwa_cache = {}
def get_lwa(store, region):
    key = (store, region)
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
    lwa_cache[key] = r.json()['access_token']
    return lwa_cache[key]

# Pull orders via Orders API
print('Pulling July 1 orders...', flush=True)
all_orders = []

for store, creds in STORES.items():
    for region in ['NA', 'FE', 'EU']:
        tk = get_lwa(store, region)
        if not tk:
            continue
        host = HOSTS[region]
        
        # Build marketplace list for this region
        if region == 'NA':
            mids = 'ATVPDKIKX0DER,A2EUQ1WTGCTBG2'
        elif region == 'FE':
            mids = 'A39IBJ37TRP1C6,A1VC38T7YXB528'
        else:
            mids = 'A1PA6795UKMFR9'

        nt = None
        while True:
            url = ('https://{}/orders/v0/orders?MarketplaceIds={}'
                   '&CreatedAfter=2026-07-01T00:00:00Z&CreatedBefore=2026-07-02T00:00:00Z'
                   '&MaxResultsPerPage=100'.format(host, mids))
            if nt:
                url += '&NextToken=' + nt
            r2 = httpx.get(url, headers={'x-amz-access-token': tk}, timeout=30)
            if r2.status_code != 200:
                break
            d = r2.json().get('payload', r2.json())
            for o in d.get('Orders', []):
                status = o.get('OrderStatus', '?')
                oid = o.get('AmazonOrderId', '')
                mkt_id = o.get('MarketplaceId', '')
                mkt = 'CA' if 'A2EUQ1WTGCTBG2' in mkt_id else (
                    'AU' if 'A39IBJ37TRP1C6' in mkt_id else (
                    'JP' if 'A1VC38T7YXB528' in mkt_id else (
                    'DE' if 'A1PA6795UKMFR9' in mkt_id else 'US')))
                total = o.get('OrderTotal', {})
                price = float(total.get('Amount', 0) or 0)
                cur = total.get('CurrencyCode', 'USD')
                items = o.get('OrderItemList', [])
                asin = items[0].get('ASIN', '') if items else ''
                qty = items[0].get('QuantityOrdered', 1) if items else 1
                sku = items[0].get('SellerSKU', '') if items else ''
                all_orders.append({
                    'store': store, 'region': region, 'mkt': mkt, 'oid': oid,
                    'status': status, 'price': price, 'cur': cur, 'asin': asin,
                    'sku': sku, 'qty': int(qty or 1)
                })
            nt = d.get('NextToken')
            if not nt:
                break

n_orders = len(all_orders)
print('   {} orders'.format(n_orders), flush=True)

# Resolve pricing: use actual for Shipped, fallback for Pending
print('Computing profit...', flush=True)
results = []
pending_count = 0
for o in all_orders:
    mkt = o['mkt']
    asin = o['asin']
    status = o['status']
    
    # Find in catalog
    prod = None
    for (s, m, a), p in catalog.items():
        if s == o['store'] and m == mkt and a == asin:
            prod = p
            break
    
    # Resolve price
    if status == 'Pending' or (status != 'Shipped' and o['price'] < 0.01):
        if prod:
            price_usd = prod['fallback_price'] * CNY2USD
        else:
            price_usd = 0  # unknown
        pending_count += 1
    else:
        price_usd = o['price'] * FX2USD.get(o['cur'], 1.0)
    
    # COGS + Freight
    if prod:
        cogs = prod['cogs'] * CNY2USD
        freight = calc_freight(mkt, prod['l'], prod['w'], prod['h'], prod['kg']) * CNY2USD
    else:
        cogs = 0
        freight = 0
    
    results.append({
        'store': o['store'], 'mkt': mkt, 'sku': o['sku'], 'status': status,
        'price': price_usd, 'qty': o['qty'], 'cogs': cogs, 'freight': freight
    })

# Aggregate by store + market
by_key = defaultdict(lambda: {'orders': 0, 'units': 0, 'rev': 0, 'cogs': 0, 'freight': 0})
for r in results:
    key = (r['store'], r['mkt'])
    by_key[key]['orders'] += 1
    by_key[key]['units'] += r['qty']
    by_key[key]['rev'] += r['price'] * r['qty']
    by_key[key]['cogs'] += r['cogs'] * r['qty']
    by_key[key]['freight'] += r['freight'] * r['qty']

print()
print('Daily Gross Profit — July 1, 2026 (Orders API + fallback)')
print('=' * 85)
print('{:8} {:>4} {:>4}u {:>5}o {:>10} {:>10} {:>10} {:>10}'.format(
    'Store', 'Mkt', '', '', 'Revenue', 'COGS', 'Freight', 'Gross'))
print('-' * 85)

grand_rev = grand_cogs = grand_freight = grand_units = grand_orders = 0
for store in ['CUCZUUS', 'BOOLUU', 'Heliumx']:
    for mkt in ['US', 'CA', 'AU', 'JP', 'DE']:
        d = by_key.get((store, mkt))
        if not d or d['orders'] == 0:
            continue
        profit = d['rev'] - d['cogs'] - d['freight']
        print('{:8} {:>4} {:>4}u {:>5}o ${:>9,.2f} ${:>9,.2f} ${:>9,.2f} ${:>9,.2f}'.format(
            store, mkt, d['units'], d['orders'], d['rev'], d['cogs'], d['freight'], profit))
        grand_rev += d['rev']
        grand_cogs += d['cogs']
        grand_freight += d['freight']
        grand_units += d['units']
        grand_orders += d['orders']

print('-' * 85)
gross_profit = grand_rev - grand_cogs - grand_freight
print('{:8} {:>4} {:>4}u {:>5}o ${:>9,.2f} ${:>9,.2f} ${:>9,.2f} ${:>9,.2f}'.format(
    'TOTAL', '', grand_units, grand_orders, grand_rev, grand_cogs, grand_freight, gross_profit))
print()
print('Pending orders with fallback pricing: {}'.format(pending_count))
print('(FBA fees + ads to be added next)')
