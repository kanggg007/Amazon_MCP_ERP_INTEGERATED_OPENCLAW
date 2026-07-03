"""CUCZUUS Apr-Jun revenue via Finances API — run from project root."""
import sys, os, httpx, hashlib, hmac, time, urllib.parse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
env_path = os.path.join(BASE, '.env')
env = {}
if os.path.exists(env_path):
    for line in open(env_path).read().splitlines():
        if line and '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()

CID = env.get('STORE_02_LWA_CLIENT_ID', '')
CSEC = env.get('STORE_02_LWA_CLIENT_SECRET', '')
AK = env.get('STORE_02_AWS_ACCESS_KEY_ID', '')
SK = env.get('STORE_02_AWS_SECRET_ACCESS_KEY', '')

HOSTS = {'US': 'sellingpartnerapi-na.amazon.com', 'CA': 'sellingpartnerapi-na.amazon.com',
         'AU': 'sellingpartnerapi-fe.amazon.com', 'JP': 'sellingpartnerapi-fe.amazon.com',
         'DE': 'sellingpartnerapi-eu.amazon.com'}
REFS = {'US': env.get('STORE_02_REFRESH_TOKEN_AMERICAS', ''),
        'CA': env.get('STORE_02_REFRESH_TOKEN_AMERICAS', ''),
        'AU': env.get('STORE_02_REFRESH_TOKEN_AU', ''),
        'JP': env.get('STORE_02_REFRESH_TOKEN_JP', ''),
        'DE': env.get('STORE_02_REFRESH_TOKEN_EU', '')}
FX = {'US': 1.0, 'CA': 0.7033, 'AU': 0.6892, 'JP': 0.00614, 'DE': 1.1403}

def sign(url, headers):
    p = urllib.parse.urlparse(url)
    ad = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    ds = time.strftime('%Y%m%d', time.gmtime())
    headers['x-amz-date'] = ad
    headers['host'] = p.hostname
    cu = urllib.parse.quote(p.path, safe='/') or '/'
    ch = ''.join(f"{k.lower()}:{v.strip()}\n" for k, v in sorted(headers.items()) if k.lower() != 'authorization')
    sh = ';'.join(sorted(k.lower() for k in headers if k.lower() != 'authorization'))
    ph = hashlib.sha256(b'').hexdigest()
    cr = '\n'.join(['GET', cu, p.query, ch, sh, ph])
    cs = f"{ds}/us-east-1/execute-api/aws4_request"
    st = '\n'.join(['AWS4-HMAC-SHA256', ad, cs, hashlib.sha256(cr.encode()).hexdigest()])
    def h8(k, m):
        if isinstance(k, str): k = k.encode()
        return hmac.new(k, m.encode(), hashlib.sha256).digest()
    ksign = hmac.new(h8(h8(h8(h8(f"AWS4{SK}", ds), "us-east-1"), "execute-api"), "aws4_request"), st.encode(), hashlib.sha256).hexdigest()
    headers['Authorization'] = f"AWS4-HMAC-SHA256 Credential={AK}/{cs},SignedHeaders={sh},Signature={ksign}"
    return headers

def get_principal(item):
    for c in item.get('ItemChargeList', []):
        if c.get('ChargeType') == 'Principal':
            return float(c.get('ChargeAmount', {}).get('CurrencyAmount', 0) or 0)
    return 0.0

months = [('2026-04-01', '2026-04-30'), ('2026-05-01', '2026-05-31'), ('2026-06-01', '2026-06-30')]
results = {}

for mkt in ['US', 'CA', 'AU', 'JP', 'DE']:
    host = HOSTS[mkt]
    ref = REFS.get(mkt, '')
    if not ref:
        print(f"{mkt}: no token")
        continue
    r = httpx.post('https://api.amazon.com/auth/o2/token',
                   json={'grant_type': 'refresh_token', 'client_id': CID,
                         'client_secret': CSEC, 'refresh_token': ref}, timeout=15)
    if r.status_code != 200:
        print(f"{mkt}: LWA {r.status_code}")
        continue
    tk = r.json()['access_token']

    for sd, ed in months:
        month = sd[:7]
        orders = 0
        rev = 0.0
        nt = None
        pages = 0
        while True:
            pages += 1
            url = (f"https://{host}/finances/v0/financialEvents?"
                   f"PostedAfter={sd}T00:00:00Z&PostedBefore={ed}T23:59:59Z&MaxResults=100")
            if nt:
                url += f"&NextToken={nt}"
            hd = sign(url, {'x-amz-access-token': tk})
            r2 = httpx.get(url, headers=hd, timeout=30)
            if r2.status_code != 200:
                print(f"{mkt} {month}: API {r2.status_code}")
                break
            d = r2.json().get('payload', r2.json())
            ev = d.get('FinancialEvents', {})
            for e in ev.get('ShipmentEventList', []):
                for it in e.get('ShipmentItemList', []):
                    p = get_principal(it)
                    q = int(it.get('QuantityShipped', 1) or 1)
                    rev += p * q
                    orders += 1
            for e in ev.get('RefundEventList', []):
                for it in e.get('RefundItemList', []):
                    rev -= get_principal(it)
            nt = d.get('NextToken')
            if not nt:
                break
        usd = round(rev * FX[mkt], 2)
        results[f"{mkt}/{month}"] = usd
        local_str = f"{rev:,.2f}"
        print(f"{mkt} {month}: {orders} orders, {local_str} local = ${usd:,.2f} USD ({pages}p)", flush=True)

print()
hdr = "Market   " + "Apr".rjust(12) + "May".rjust(12) + "Jun".rjust(12) + "Total".rjust(12)
print(hdr)
grand = 0
for mkt in ['US', 'CA', 'AU', 'JP', 'DE']:
    apr = results.get(f"{mkt}/2026-04", 0)
    may = results.get(f"{mkt}/2026-05", 0)
    jun = results.get(f"{mkt}/2026-06", 0)
    tot = apr + may + jun
    grand += tot
    print(f"{mkt:<8} ${apr:>11,.2f} ${may:>11,.2f} ${jun:>11,.2f} ${tot:>11,.2f}")
print(f"{'TOTAL':<8} ${grand:>11,.2f}")
