"""Build SKU→ASIN cache for all stores across all markets via Listings API."""
import httpx, json, os, time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = {}
for line in open(os.path.join(BASE, '.env')).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        ENV[k.strip()] = v.strip()

STORES = {
    'CUCZUUS': {'sid': 'AXW589ZKKDJNM', 'CID': ENV['STORE_02_LWA_CLIENT_ID'],
                'CSEC': ENV['STORE_02_LWA_CLIENT_SECRET'],
                'NA': ENV['STORE_02_REFRESH_TOKEN_AMERICAS'],
                'FE': ENV.get('STORE_02_REFRESH_TOKEN_AU'),
                'EU': ENV['STORE_02_REFRESH_TOKEN_EU']},
    'BOOLUU': {'sid': 'A8P0GUSN1BWCC', 'CID': ENV['STORE_03_LWA_CLIENT_ID'],
               'CSEC': ENV['STORE_03_LWA_CLIENT_SECRET'],
               'NA': ENV['STORE_03_REFRESH_TOKEN_AMERICAS'],
               'FE': ENV.get('STORE_03_REFRESH_TOKEN_AU')},
    'Heliumx': {'sid': 'AD4UKHMR5K35J', 'CID': ENV['STORE_04_LWA_CLIENT_ID'],
                'CSEC': ENV['STORE_04_LWA_CLIENT_SECRET'],
                'NA': ENV['STORE_04_REFRESH_TOKEN_AMERICAS'],
                'FE': ENV.get('STORE_04_REFRESH_TOKEN_AU'),
                'EU': ENV['STORE_04_REFRESH_TOKEN_EU']},
}

REGIONS = {
    ('NA','US'): ('sellingpartnerapi-na.amazon.com', 'ATVPDKIKX0DER'),
    ('NA','CA'): ('sellingpartnerapi-na.amazon.com', 'A2EUQ1WTGCTBG2'),
    ('FE','AU'): ('sellingpartnerapi-fe.amazon.com', 'A39IBJ37TRP1C6'),
    ('FE','JP'): ('sellingpartnerapi-fe.amazon.com', 'A1VC38T7YXB528'),
    ('EU','DE'): ('sellingpartnerapi-eu.amazon.com', 'A1PA6795UKMFR9'),
}

sku_asin = {}
total = 0

for store, creds in STORES.items():
    sid = creds['sid']
    for (region, mkt), (host, mid) in REGIONS.items():
        ref = creds.get(region)
        if not ref:
            continue
        r = httpx.post('https://api.amazon.com/auth/o2/token',
                       json={'grant_type': 'refresh_token', 'client_id': creds['CID'],
                             'client_secret': creds['CSEC'], 'refresh_token': ref}, timeout=15)
        if r.status_code != 200:
            print('{} {}: LWA fail'.format(store, region))
            continue
        tk = r.json()['access_token']

        nt = None
        pages = 0
        store_count = 0
        while True:
            pages += 1
            url = 'https://{}/listings/2021-08-01/items/{}?marketplaceIds={}&includedData=summaries'.format(
                host, sid, mid)
            if nt:
                url += '&NextToken=' + nt
            r2 = httpx.get(url, headers={'x-amz-access-token': tk}, timeout=30)
            if r2.status_code == 429:
                time.sleep(2)
                continue
            if r2.status_code != 200:
                break
            d = r2.json().get('payload', r2.json())
            for item in d.get('items', []):
                sku = item.get('sku', '')
                summaries = item.get('summaries', [])
                if summaries and sku:
                    asin = summaries[0].get('asin', '')
                    if asin:
                        key = '{}/{}/{}'.format(store, mkt, sku)
                        sku_asin[key] = asin
                        total += 1
                        store_count += 1
            nt = d.get('NextToken')
            if not nt:
                break
            time.sleep(0.5)
        print('{} {}/{}: {} SKUs ({} pages)'.format(store, region, mkt, store_count, pages))

# Save
cache_path = os.path.join(BASE, 'data', 'sku_to_asin.json')
with open(cache_path, 'w') as f:
    json.dump(sku_asin, f, indent=2)
print('\nSaved {} SKU->ASIN mappings to {}'.format(total, cache_path))
