"""Seed fba_fees table — Products Fee API"""
import httpx, os, psycopg2, threading, time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = {}
for line in open(os.path.join(BASE, '.env')).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

dsn = env['DATABASE_URL']
REGION_MAP = {'US': 'NA', 'CA': 'NA', 'AU': 'FE', 'JP': 'FE', 'DE': 'EU'}
HOSTS = {'NA': 'sellingpartnerapi-na.amazon.com', 'FE': 'sellingpartnerapi-fe.amazon.com', 'EU': 'sellingpartnerapi-eu.amazon.com'}
MID_MAP = {'US': 'ATVPDKIKX0DER', 'CA': 'A2EUQ1WTGCTBG2', 'AU': 'A39IBJ37TRP1C6', 'JP': 'A1VC38T7YXB528', 'DE': 'A1PA6795UKMFR9'}

conn = psycopg2.connect(dsn, connect_timeout=15)
cur = conn.cursor()
cur.execute('CREATE TABLE IF NOT EXISTS fba_fees (asin VARCHAR(15), marketplace VARCHAR(5), fba_fee DECIMAL(10,3), retrieved_at TIMESTAMP DEFAULT NOW(), PRIMARY KEY(asin, marketplace))')
conn.commit()

cur.execute("SELECT DISTINCT oa.marketplace, oi.asin, MAX(oi.unit_price) FROM orders_active oa JOIN order_items_active oi ON oa.order_id=oi.order_id WHERE oa.purchase_date>'2026-06-01' AND oi.unit_price>0 GROUP BY oa.marketplace, oi.asin")
asins = [(r[0], r[1], float(r[2] or 29.99)) for r in cur.fetchall()]
conn.close()
print(f'ASINs to fetch: {len(asins)}')

# Get LWA tokens per region
cid = env['STORE_02_LWA_CLIENT_ID']
csec = env['STORE_02_LWA_CLIENT_SECRET']
tokens = {}
for region, rk in [('NA', 'AMERICAS'), ('FE', 'AU'), ('EU', 'EU')]:
    ref = env.get(f'STORE_02_REFRESH_TOKEN_{rk}', '')
    if not ref:
        continue
    r = httpx.post('https://api.amazon.com/auth/o2/token',
        json={'grant_type': 'refresh_token', 'client_id': cid, 'client_secret': csec, 'refresh_token': ref}, timeout=15)
    if r.status_code == 200:
        tokens[region] = r.json()['access_token']
print(f'Tokens: {list(tokens.keys())}')

results = []
fetched = 0
errors = 0
lock = threading.Lock()
sem = threading.Semaphore(5)

def fetch_one(mkt, asin, price):
    global fetched, errors
    sem.acquire()
    try:
        region = REGION_MAP.get(mkt, 'NA')
        tk = tokens.get(region)
        if not tk:
            with lock:
                errors += 1
            return
        host = HOSTS[region]
        mid = MID_MAP.get(mkt, 'ATVPDKIKX0DER')
        
        body = {
            'FeesEstimateRequest': {
                'MarketplaceId': mid,
                'IsAmazonFulfilled': True,
                'PriceToEstimateFees': {'ListingPrice': {'CurrencyCode': 'USD', 'Amount': price}},
                'Identifier': asin
            }
        }
        r2 = httpx.post(f'https://{host}/products/fees/v0/items/{asin}/feesEstimate',
            headers={'x-amz-access-token': tk, 'Content-Type': 'application/json'}, json=body, timeout=15)
        
        if r2.status_code == 200:
            fe = r2.json().get('payload', r2.json()).get('FeesEstimateResult', {}).get('FeesEstimate', {})
            fba = 0
            for f in fe.get('FeeDetailList', []):
                if f.get('FeeType') in ('FBAFees',):
                    fba = float(f.get('FinalFee', {}).get('Amount', 0))
            if fba > 0:
                results.append((asin, mkt, fba))
                with lock:
                    fetched += 1
                    print(f'  {fetched} {mkt} {asin}: \${fba}')
        else:
            with lock:
                errors += 1
    except:
        with lock:
            errors += 1
    finally:
        sem.release()

threads = []
for mkt, asin, price in asins:
    t = threading.Thread(target=fetch_one, args=(mkt, asin, price))
    t.start()
    threads.append(t)
    time.sleep(0.03)

for t in threads:
    t.join()

# Insert results
conn = psycopg2.connect(dsn, connect_timeout=15)
cur = conn.cursor()
for asin, mkt, fba in results:
    cur.execute('INSERT INTO fba_fees (asin, marketplace, fba_fee, retrieved_at) VALUES (%s,%s,%s,NOW()) ON CONFLICT (asin, marketplace) DO UPDATE SET fba_fee=EXCLUDED.fba_fee, retrieved_at=NOW()', (asin, mkt, fba))
conn.commit()
conn.close()

print(f'\nDone: {fetched} FBA fees, {errors} errors, {len(asins)} total')
