"""Backfill BOOLUU Apr-May 2026 from Finances API: orders & refunds by marketplace."""
import os,json,time,hashlib,hmac,urllib.request
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env={}
for line in open(os.path.join(BASE,'.env')).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k,v=line.split('=',1);env[k.strip()]=v.strip()

aws_key = env['STORE_03_AWS_ACCESS_KEY_ID']
aws_secret = env['STORE_03_AWS_SECRET_ACCESS_KEY']
lwa_client = env['STORE_03_LWA_CLIENT_ID']
lwa_secret = env['STORE_03_LWA_CLIENT_SECRET']

MARKETS = [
    ('US','A2Q3Y263D00KWC','https://sellingpartnerapi-na.amazon.com','STORE_03_REFRESH_TOKEN_AMERICAS'),
    ('CA','A2EUQ1WTGCTBG2','https://sellingpartnerapi-na.amazon.com','STORE_03_REFRESH_TOKEN_AMERICAS'),
    ('AU','A39IBJ37TRP1C6','https://sellingpartnerapi-fe.amazon.com','STORE_03_REFRESH_TOKEN_AU'),
    ('JP','A1VC38T7YXB528','https://sellingpartnerapi-fe.amazon.com','STORE_03_REFRESH_TOKEN_JP'),
]

FX = {'USD':1.0,'CAD':0.73,'AUD':0.67,'JPY':0.0067}
MONTHS = [('2026-04-01','2026-04-30'),('2026-05-01','2026-05-31')]

def aws_sigv4(method,host,region,uri,query,payload_bytes,token,service='execute-api'):
    t = time.gmtime()
    amz_date = time.strftime('%Y%m%dT%H%M%SZ',t)
    date_stamp = time.strftime('%Y%m%d',t)
    
    canonical_uri = uri
    canonical_query = query
    canonical_headers = 'host:{}\nx-amz-access-token:{}\nx-amz-date:{}\n'.format(host,token,amz_date)
    signed_headers = 'host;x-amz-access-token;x-amz-date'
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()
    canonical_request = '{}\n{}\n{}\n{}\n{}\n{}'.format(method,canonical_uri,canonical_query,canonical_headers,signed_headers,payload_hash)
    
    algorithm = 'AWS4-HMAC-SHA256'
    credential_scope = '{}/{}/{}/aws4_request'.format(date_stamp,region,service)
    string_to_sign = '{}\n{}\n{}\n{}'.format(algorithm,amz_date,credential_scope,hashlib.sha256(canonical_request.encode()).hexdigest())
    
    def sign(key,msg): return hmac.new(key,msg.encode(),hashlib.sha256).digest()
    k = sign(('AWS4'+aws_secret).encode(), date_stamp)
    for scope in [region,service,'aws4_request']: k=sign(k,scope)
    sig = hmac.new(k,string_to_sign.encode(),hashlib.sha256).hexdigest()
    
    return '{} Credential={}/{}, SignedHeaders={}, Signature={}'.format(algorithm,aws_key,credential_scope,signed_headers,sig),amz_date

def get_token(refresh_token):
    data = 'grant_type=refresh_token&client_id={}&client_secret={}&refresh_token={}'.format(lwa_client,lwa_secret,refresh_token)
    req = urllib.request.Request('https://api.amazon.com/auth/o2/token',data=data.encode(),headers={'Content-Type':'application/x-www-form-urlencoded'})
    return json.loads(urllib.request.urlopen(req,timeout=15).read())['access_token']

def fetch(market,mkt_id,endpoint_url,refresh_env,start,end):
    token = get_token(env[refresh_env])
    url = '{}/finances/v0/financialEvents'.format(endpoint_url)
    pu = urlparse(url)
    host = pu.hostname
    region = 'us-east-1' if 'na' in host else 'us-west-2'
    
    params = 'PostedAfter={}T00:00:00Z&PostedBefore={}T23:59:59Z&MaxResultsPerPage=100'.format(start,end)
    result = {'orders':0,'rev':0,'refunds':0,'ref_amt':0,'currency':'USD'}
    next_token = None
    
    for page in range(20):
        q = params + ('&NextToken={}'.format(next_token) if next_token else '')
        auth,amz_date = aws_sigv4('GET',host,region,'/finances/v0/financialEvents',q,b'',token)
        
        req = urllib.request.Request('{}?{}'.format(url,q),headers={
            'host':host,'x-amz-access-token':token,'x-amz-date':amz_date,'Authorization':auth})
        
        try:
            resp = urllib.request.urlopen(req,timeout=30)
            body = json.loads(resp.read())
        except Exception as e:
            print('  {} page {}: {}'.format(market,page,e))
            break
        
        fe = body.get('payload',body).get('FinancialEvents',{})
        
        for s in fe.get('ShipmentEventList',[]):
            for item in s.get('ShipmentItemList',[]):
                amt = item.get('Principal',{})
                result['orders'] += 1
                result['rev'] += float(amt.get('Amount',0) or 0)
                result['currency'] = amt.get('CurrencyCode','USD')
        
        for ref in fe.get('RefundEventList',[]):
            for item in ref.get('ShipmentItemAdjustmentList',[]):
                amt = item.get('Principal',{})
                result['refunds'] += 1
                result['ref_amt'] += float(amt.get('Amount',0) or 0)
        
        next_token = body.get('payload',body).get('NextToken')
        if not next_token:
            break
        time.sleep(2)
    
    return result

print('BOOLUU April-May 2026 Finances API Backfill')
print('='*60)

for month_start, month_end in MONTHS:
    month_label = month_start[:7]
    print('\n[{}]'.format(month_label))
    
    total_ord = 0; total_rev = 0; total_ref = 0; total_ref_amt = 0
    
    for mkt_name,mkt_id,endpoint_url,refresh_env in MARKETS:
        r = fetch(mkt_name,mkt_id,endpoint_url,refresh_env,month_start,month_end)
        net = r['rev'] - r['ref_amt']
        c = r['currency']
        
        print('  {}: {} orders ({}{:,.2f}), {} refunds (-{}{:,.2f}) = Net: {}{:,.2f}'.format(
            mkt_name,r['orders'],c,r['rev'],r['refunds'],c,r['ref_amt'],c,net))
        
        total_ord += r['orders']
        total_rev += r['rev'] * FX.get(c,1.0)
        total_ref += r['refunds']
        total_ref_amt += r['ref_amt'] * FX.get(c,1.0)
    
    net_usd = total_rev - total_ref_amt
    print('  >> USD: Revenue ${:,.2f} - Refunds ${:,.2f} = Net ${:,.2f}'.format(total_rev,total_ref_amt,net_usd))
    
print('\nDone.')
