"""Backfill Finances API on Railway (US network, no China latency)."""
import os,sys,json,time,hashlib,hmac,urllib.request

store = sys.argv[1] if len(sys.argv)>1 else 'BOOLUU'
target_month = sys.argv[2] if len(sys.argv)>2 else None
if not target_month:
    print('Usage: backfill_finances_railway.py <store> <YYYY-MM>')
    sys.exit(1)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = {}
with open(os.path.join(BASE, '.env')) as f:
    for line in f:
        if line and '=' in line and not line.startswith('#'):
            k,v = line.strip().split('=', 1)
            env[k] = v

# Store credentials
if store == 'BOOLUU':
    prefix = 'STORE_03'
elif store == 'CUCZUUS':
    prefix = 'STORE_02'
else:
    print(f'Unknown store: {store}')
    sys.exit(1)

aws_key = env.get(f'{prefix}_AWS_ACCESS_KEY_ID')
aws_secret = env.get(f'{prefix}_AWS_SECRET_ACCESS_KEY')
lwa_client = env.get(f'{prefix}_LWA_CLIENT_ID', env.get('STORE_02_LWA_CLIENT_ID'))
lwa_secret = env.get(f'{prefix}_LWA_CLIENT_SECRET', env.get('STORE_02_LWA_CLIENT_SECRET'))

MONTHS = {
    f'{target_month}-01': f'{target_month}-30',
}

MARKETS = [
    ('US', 'ATVPDKIKX0DER', 'https://sellingpartnerapi-na.amazon.com', 'us-east-1', f'{prefix}_REFRESH_TOKEN_AMERICAS'),
    ('CA', 'A2EUQ1WTGCTBG2', 'https://sellingpartnerapi-na.amazon.com', 'us-east-1', f'{prefix}_REFRESH_TOKEN_AMERICAS'),
    ('AU', 'A39IBJ37TRP1C6', 'https://sellingpartnerapi-fe.amazon.com', 'us-west-2', f'{prefix}_REFRESH_TOKEN_AU'),
    ('JP', 'A1VC38T7YXB528', 'https://sellingpartnerapi-fe.amazon.com', 'us-west-2', f'{prefix}_REFRESH_TOKEN_JP'),
]

if f'{prefix}_REFRESH_TOKEN_AU' not in env:
    MARKETS = [m for m in MARKETS if m[0] not in ('AU','JP')]

FX = {'USD': 1, 'CAD': 0.73, 'AUD': 0.67, 'JPY': 0.0067}

def aws_sig(host, region, uri, query, token):
    t = time.gmtime()
    amz = time.strftime('%Y%m%dT%H%M%SZ', t)
    ds = time.strftime('%Y%m%d', t)
    ph = hashlib.sha256(b'').hexdigest()
    ch = f'host:{host}\nx-amz-access-token:{token}\nx-amz-date:{amz}\n'
    sh = 'host;x-amz-access-token;x-amz-date'
    cr = f'GET\n{uri}\n{query}\n{ch}\n{sh}\n{ph}'
    cs = f'{ds}/{region}/execute-api/aws4_request'
    sts = f'AWS4-HMAC-SHA256\n{amz}\n{cs}\n{hashlib.sha256(cr.encode()).hexdigest()}'
    def s(k, msg): return hmac.new(k, msg.encode(), hashlib.sha256).digest()
    k = s(('AWS4' + aws_secret).encode(), ds)
    for sc in [region, 'execute-api', 'aws4_request']: k = s(k, sc)
    sig = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()
    return f'AWS4-HMAC-SHA256 Credential={aws_key}/{cs}, SignedHeaders={sh}, Signature={sig}', amz

def get_token(refresh):
    d = f'grant_type=refresh_token&client_id={lwa_client}&client_secret={lwa_secret}&refresh_token={refresh}'
    r = urllib.request.urlopen(urllib.request.Request('https://api.amazon.com/auth/o2/token',
        data=d.encode(), headers={'Content-Type': 'application/x-www-form-urlencoded'}), timeout=15)
    return json.loads(r.read())['access_token']

def fetch(host, region, token, start, end):
    base = '/finances/v0/financialEvents'
    params = f'PostedAfter={start}T00:00:00Z&PostedBefore={end}T23:59:59Z&MaxResultsPerPage=100'
    rev = 0; orders = 0; refunds = 0; ref_amt = 0; curr = 'USD'; nt = None
    
    while True:
        q = params + (f'&NextToken={nt}' if nt else '')
        auth, amz = aws_sig(host, region, base, q, token)
        url = f'https://{host}{base}?{q}'
        req = urllib.request.Request(url, headers={
            'host': host, 'x-amz-access-token': token,
            'x-amz-date': amz, 'Authorization': auth
        })
        body = json.loads(urllib.request.urlopen(req, timeout=30).read())
        fe = body.get('payload', body).get('FinancialEvents', {})
        
        for s in fe.get('ShipmentEventList', []):
            for item in s.get('ShipmentItemList', []):
                orders += 1
                for chg in item.get('ItemChargeList', []):
                    if chg.get('ChargeType') == 'Principal':
                        rev += float(chg.get('ChargeAmount', {}).get('CurrencyAmount', 0) or 0)
                        curr = chg.get('ChargeAmount', {}).get('CurrencyCode', 'USD')
        
        for r in fe.get('RefundEventList', []):
            for item in r.get('ShipmentItemAdjustmentList', []):
                refunds += 1
                for chg in item.get('ItemChargeAdjustmentList', []):
                    if chg.get('ChargeType') == 'Principal':
                        ref_amt += float(chg.get('ChargeAmount', {}).get('CurrencyAmount', 0) or 0)
        
        nt = body.get('payload', body).get('NextToken')
        if not nt: break
        time.sleep(1.5)
    
    return orders, rev, refunds, ref_amt, curr

print(f'{store} {target_month} Finances API Backfill')
print('=' * 50)

for start, end in MONTHS.items():
    month_label = start[:7]
    total_rev = 0; total_ref = 0; total_ord = 0
    print(f'\n{month_label}:')
    
    for mkt, mkt_id, base_url, region, token_env in MARKETS:
        if token_env not in env:
            print(f'  {mkt}: SKIP (no token {token_env})')
            continue
        token = get_token(env[token_env])
        host = base_url.replace('https://', '')
        o, r, rf, ra, cu = fetch(host, region, token, start, end)
        net = r - ra; rate = FX.get(cu, 1)
        total_ord += o; total_rev += r * rate; total_ref += ra * rate
        print(f'  {mkt}: {o} orders ({cu} {r:,.0f}), {rf} refunds (-{cu} {ra:,.0f}) = Net {cu} {net:,.0f} (${net*rate:,.0f})')
    
    print(f'  TOTAL USD: ${total_rev:,.0f} rev - ${total_ref:,.0f} refunds = Net ${total_rev-total_ref:,.0f}')

print('\nDone.')
