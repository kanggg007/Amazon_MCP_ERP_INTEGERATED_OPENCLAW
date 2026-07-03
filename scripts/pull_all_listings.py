"""Pull merchant listings for all stores/markets (except CUCZUUS US), save to desktop Excel."""
import httpx, time, os, gzip, openpyxl, json, threading

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = {}
for line in open(os.path.join(BASE, '.env')).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

# Define all store-market combos
TASKS = []
# CUCZUUS CA/AU/JP/DE
for mkt, mid, region, ref_key in [
    ('CA', 'A2EUQ1WTGCTBG2', 'NA', 'AMERICAS'),
    ('AU', 'A39IBJ37TRP1C6', 'FE', 'AU'),
    ('JP', 'A1VC38T7YXB528', 'FE', 'JP'),
    ('DE', 'A1PA6795UKMFR9', 'EU', 'EU'),
]:
    TASKS.append(('CUCZUUS', mkt, mid, region, ref_key, '02'))

# BOOLUU all
for mkt, mid, region, ref_key in [
    ('US', 'ATVPDKIKX0DER', 'NA', 'AMERICAS'),
    ('CA', 'A2EUQ1WTGCTBG2', 'NA', 'AMERICAS'),
    ('AU', 'A39IBJ37TRP1C6', 'FE', 'AU'),
    ('JP', 'A1VC38T7YXB528', 'FE', 'JP'),
]:
    TASKS.append(('BOOLUU', mkt, mid, region, ref_key, '03'))

# Heliumx all
for mkt, mid, region, ref_key in [
    ('US', 'ATVPDKIKX0DER', 'NA', 'AMERICAS'),
    ('CA', 'A2EUQ1WTGCTBG2', 'NA', 'AMERICAS'),
    ('AU', 'A39IBJ37TRP1C6', 'FE', 'AU'),
    ('DE', 'A1PA6795UKMFR9', 'EU', 'EU'),
]:
    TASKS.append(('Heliumx', mkt, mid, region, ref_key, '04'))

results = {}
lock = threading.Lock()

def pull_one(store, mkt, mid, region, ref_key, snum):
    CID = env.get('STORE_' + snum + '_LWA_CLIENT_ID', '')
    CSEC = env.get('STORE_' + snum + '_LWA_CLIENT_SECRET', '')
    REF = env.get('STORE_' + snum + '_REFRESH_TOKEN_' + ref_key, '')
    if not all([CID, CSEC, REF]):
        with lock:
            print('{} {}: no token'.format(store, mkt))
        return

    r = httpx.post('https://api.amazon.com/auth/o2/token',
        json={'grant_type': 'refresh_token', 'client_id': CID,
              'client_secret': CSEC, 'refresh_token': REF}, timeout=15)
    if r.status_code != 200:
        with lock:
            print('{} {}: LWA fail {}'.format(store, mkt, r.status_code))
        return

    tk = r.json()['access_token']
    host = {'NA': 'sellingpartnerapi-na.amazon.com', 'FE': 'sellingpartnerapi-fe.amazon.com',
            'EU': 'sellingpartnerapi-eu.amazon.com'}[region]
    body = {'reportType': 'GET_MERCHANT_LISTINGS_DATA', 'marketplaceIds': [mid]}
    r2 = httpx.post('https://{}/reports/2021-06-30/reports'.format(host),
        headers={'x-amz-access-token': tk, 'Content-Type': 'application/json'}, json=body, timeout=15)
    rid = r2.json()['reportId']

    for i in range(30):
        time.sleep(10)
        rp = httpx.get('https://{}/reports/2021-06-30/reports/{}'.format(host, rid),
                       headers={'x-amz-access-token': tk}, timeout=10)
        s = rp.json().get('processingStatus', '?')
        if s == 'DONE':
            did = rp.json().get('reportDocumentId', '')
            doc = httpx.get('https://{}/reports/2021-06-30/documents/{}'.format(host, did),
                            headers={'x-amz-access-token': tk}, timeout=15).json()
            data = gzip.decompress(httpx.get(doc.get('url', ''), timeout=30).content).decode('utf-8-sig')
            with lock:
                results[(store, mkt)] = data
                print('{} {}: {} listings'.format(store, mkt, len(data.strip().split('\n')) - 1))
            break
        elif s == 'CANCELLED':
            with lock:
                print('{} {}: CANCELLED'.format(store, mkt))
            break

print('Pulling merchant listings ({} reports in parallel)...'.format(len(TASKS)))
threads = []
for task in TASKS:
    t = threading.Thread(target=pull_one, args=task)
    t.start()
    threads.append(t)
for t in threads:
    t.join()

# Save to Excel
print('\nSaving to Excel...')
out = openpyxl.Workbook()
out.remove(out.active)

for (store, mkt), data in results.items():
    if not data:
        continue
    ws = out.create_sheet('{} {}'.format(store, mkt))
    rows = [l.split('\t') for l in data.strip().split('\n')]
    header = rows[0]

    # Find columns
    try:
        ni = header.index('item-name')
        si = header.index('seller-sku')
        ai = header.index('asin1')
        try:
            fi = header.index('fulfillment-channel')
        except:
            fi = None

        ws.append(['item-name', 'seller-sku', 'asin1', 'fulfillment-channel'])
        for row in rows[1:]:
            if len(row) > max(ni, si, ai):
                name = row[ni][:100] if row[ni] else ''
                sku = row[si] if row[si] else ''
                asin = row[ai] if row[ai] else ''
                fc = row[fi] if fi and fi < len(row) else ''
                ws.append([name, sku, asin, fc])

        # Auto-width
        for col in ws.columns:
            mx = max((len(str(c.value or '')) for c in col), default=0)
            ws.column_dimensions[col[0].column_letter].width = min(mx + 2, 60)
    except Exception as e:
        print('{} {}: error {}'.format(store, mkt, e))

out_path = os.path.expanduser('~/Desktop/merchant_listings_all_stores.xlsx')
out.save(out_path)
print('Saved to {}'.format(out_path))
