"""Breakdown CUCZUUS US orders from v2026 with ASIN/COGS/Freight."""
import httpx, os, openpyxl, json

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = {}
for line in open(os.path.join(BASE, '.env')).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

# Catalog
wb = openpyxl.load_workbook(os.path.join(BASE, 'data', 'Master_Product_Catalog_v2.xlsx'), data_only=True)
cat = {}
for row in wb.active.iter_rows(min_row=2, values_only=True):
    if row[0] and row[2] and str(row[0]) == 'CUCZUUS' and str(row[1]) == 'US':
        a = str(row[2]); l = float(row[4] or 0); w = float(row[5] or 0)
        h = float(row[6] or 0); k = float(row[7] or 0); c = float(row[8] or 0)
        frt = max(k, l * w * h / 6000) * 5 * 0.1477
        cat[a] = {'cogs': c * 0.1477, 'frt': frt, 'title': str(row[3])[:60] if row[3] else ''}

# Orders
r = httpx.post('https://api.amazon.com/auth/o2/token',
    json={'grant_type': 'refresh_token', 'client_id': env['STORE_02_LWA_CLIENT_ID'],
          'client_secret': env['STORE_02_LWA_CLIENT_SECRET'],
          'refresh_token': env['STORE_02_REFRESH_TOKEN_AMERICAS']}, timeout=15)
tk = r.json()['access_token']

url = 'https://sellingpartnerapi-na.amazon.com/orders/2026-01-01/orders?marketplaceIds=ATVPDKIKX0DER&createdAfter=2026-07-01T07:00:00Z&MaxResultsPerPage=100'
r2 = httpx.get(url, headers={'x-amz-access-token': tk}, timeout=15)
orders = r2.json().get('orders', [])

print('CUCZUUS US July 1 — Per-Order Breakdown')
print('ASIN          SKU                         Price   COGS   Freight  Product')
print('-' * 100)

total_rev = total_cogs = total_frt = matched = 0
for o in orders:
    items = o.get('orderItems', [])
    if not items:
        continue
    it = items[0]; prod = it.get('product', {})
    asin_t = prod.get('asin', '?')
    sku = prod.get('sellerSku', '?')
    price = float(prod.get('price', {}).get('unitPrice', {}).get('amount', 0) or 0)
    c = cat.get(asin_t, {})
    cogs = c.get('cogs', 0)
    frt = c.get('frt', 0)
    title = c.get('title', 'NOT IN CATALOG')
    if c:
        matched += 1
    total_rev += price; total_cogs += cogs; total_frt += frt
    print('{:14} {:28} ${:>6.2f} ${:>5.2f} ${:>7.2f}  {}'.format(
        asin_t[:14], sku[:28], price, cogs, frt, title[:50]))

n = len(orders)
print('-' * 100)
print('{} orders, {}/{} matched. Rev=${:.2f} COGS=${:.2f} Frt=${:.2f}'.format(
    n, matched, n, total_rev, total_cogs, total_frt))
