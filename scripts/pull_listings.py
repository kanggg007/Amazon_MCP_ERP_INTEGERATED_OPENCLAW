"""Pull CUCZUUS US merchant listings and save to desktop."""
import httpx, time, os, gzip

env = {}
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')
for line in open(env_path).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

CID = env['STORE_02_LWA_CLIENT_ID']
CSEC = env['STORE_02_LWA_CLIENT_SECRET']
r = httpx.post('https://api.amazon.com/auth/o2/token',
    json={'grant_type': 'refresh_token', 'client_id': CID, 'client_secret': CSEC,
          'refresh_token': env['STORE_02_REFRESH_TOKEN_AMERICAS']}, timeout=15)
tk = r.json()['access_token']

body = {'reportType': 'GET_MERCHANT_LISTINGS_DATA', 'marketplaceIds': ['ATVPDKIKX0DER']}
r2 = httpx.post('https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/reports',
    headers={'x-amz-access-token': tk, 'Content-Type': 'application/json'}, json=body, timeout=15)
rid = r2.json()['reportId']
print('Report ID: {}'.format(rid))
print('Waiting for report generation (60-90s)...')

for i in range(30):
    time.sleep(10)
    rp = httpx.get('https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/reports/' + rid,
                   headers={'x-amz-access-token': tk}, timeout=10)
    s = rp.json().get('processingStatus', '?')
    print('  Poll {}: {}'.format(i, s))
    if s == 'DONE':
        did = rp.json().get('reportDocumentId', '')
        doc = httpx.get('https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/documents/' + did,
                        headers={'x-amz-access-token': tk}, timeout=15).json()
        data = gzip.decompress(httpx.get(doc.get('url', ''), timeout=30).content).decode('utf-8-sig')
        out = os.path.expanduser('~/Desktop/merchant_listings_CUCZUUS_US.txt')
        with open(out, 'w') as f:
            f.write(data)
        rows = data.strip().split('\n')
        print('\nSaved {} listings to {}'.format(len(rows) - 1, out))
        break
    elif s == 'CANCELLED':
        print('Report cancelled')
        break
