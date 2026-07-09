"""Seed FBA fees for ALL markets with correct local currency"""
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
CUR_MAP = {'US': 'USD', 'CA': 'CAD', 'AU': 'AUD', 'JP': 'JPY', 'DE': 'EUR'}

conn = psycopg2.connect(dsn, connect_timeout=10)
cur = conn.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS fba_fees (
    asin VARCHAR(15), marketplace VARCHAR(5), fba_fee DECIMAL(10,3),
    fba_currency VARCHAR(5) DEFAULT 'USD', retrieved_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY(asin, marketplace))""")
# Add currency column if missing
try:
    cur.execute('ALTER TABLE fba_fees ADD COLUMN IF NOT EXISTS fba_currency VARCHAR(5) DEFAULT %s', ('USD',))
    conn.commit()
except: conn.rollback()
conn.commit()

# Get ALL ASINs not yet fetched
cur.execute("""SELECT DISTINCT oa.marketplace, oi.asin, MAX(oi.unit_price) FROM orders_active oa
    JOIN order_items_active oi ON oa.order_id = oi.order_id
    WHERE oa.purchase_date>'2026-06-01' AND oi.unit_price>0
    AND NOT EXISTS (SELECT 1 FROM fba_fees f WHERE f.asin=oi.asin AND f.marketplace=oa.marketplace)
    GROUP BY oa.marketplace, oi.asin""")
asins = [(r[0], r[1], float(r[2] or 29.99)) for r in cur.fetchall()]
conn.close()
print(f'ASINs to fetch: {len(asins)}')

if not asins:
    print('All done!')
    exit()

cid = env['STORE_02_LWA_CLIENT_ID']
csec = env['STORE_02_LWA_CLIENT_SECRET']
tokens = {}
for region, rk in [('NA', 'AMERICAS'), ('FE', 'AU'), ('EU', 'EU')]:
    ref = env.get(f'STORE_02_REFRESH_TOKEN_{rk}', '')
    if not ref: continue
    r = httpx.post('https://api.amazon.com/auth/o2/token', json={'grant_type': 'refresh_token', 'client_id': cid, 'client_secret': csec, 'refresh_token': ref}, timeout=15)
    if r.status_code == 200: tokens[region] = r.json()['access_token']
print(f'Tokens: {list(tokens.keys())}')

results = []
fetched = 0
errors = 0
lock = threading.Lock()
sem = threading.Semaphore(3)

def fetch_one(mkt, asin, price):
    global fetched, errors
    sem.acquire()
    try:
        region = REGION_MAP.get(mkt, 'NA')
        tk = tokens.get(region)
        if not tk:
            with lock: errors += 1
            sem.release()
            return
        host = HOSTS[region]
        mid = MID_MAP.get(mkt)
        cur = CUR_MAP.get(mkt, 'USD')
        body = {'FeesEstimateRequest': {
            'MarketplaceId': mid, 'IsAmazonFulfilled': True,
            'PriceToEstimateFees': {'ListingPrice': {'CurrencyCode': cur, 'Amount': price}},
            'Identifier': asin}}
        r2 = httpx.post(f'https://{host}/products/fees/v0/items/{asin}/feesEstimate',
            headers={'x-amz-access-token': tk, 'Content-Type': 'application/json'}, json=body, timeout=15)
        if r2.status_code == 200:
            fe = r2.json().get('payload', r2.json()).get('FeesEstimateResult', {}).get('FeesEstimate', {})
            fba = 0; fba_cur = 'USD'
            for f in fe.get('FeeDetailList', []):
                if f.get('FeeType') == 'FBAFees':
                    fba = float(f.get('FinalFee', {}).get('Amount', 0))
                    fba_cur = f.get('FinalFee', {}).get('CurrencyCode', 'USD')
            if fba > 0:
                results.append((asin, mkt, fba, fba_cur))
                with lock:
                    fetched += 1
                    print(f'{fetched} {mkt} {asin}: {fba} {fba_cur}')
        else:
            with lock: errors += 1
    except:
        with lock: errors += 1
    finally:
        sem.release()

threads = []
for mkt, asin, price in asins:
    t = threading.Thread(target=fetch_one, args=(mkt, asin, price))
    t.start(); threads.append(t); time.sleep(0.03)
for t in threads: t.join()

# Save results
if results:
    c = psycopg2.connect(dsn, connect_timeout=10)
    cur = c.cursor()
    for asin, mkt, fba, cur_code in results:
        cur.execute("""INSERT INTO fba_fees (asin, marketplace, fba_fee, fba_currency, retrieved_at)
            VALUES (%s,%s,%s,%s,NOW())
            ON CONFLICT (asin, marketplace) DO UPDATE
            SET fba_fee=EXCLUDED.fba_fee, fba_currency=EXCLUDED.fba_currency, retrieved_at=NOW()""",
            (asin, mkt, fba, cur_code))
    c.commit(); c.close()

print(f'Done: {fetched} fees, {errors} errors')
