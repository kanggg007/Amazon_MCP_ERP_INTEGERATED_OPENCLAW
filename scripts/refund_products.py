"""Show refunded products by SKU."""
import httpx,hashlib,hmac,time,urllib.parse,json,os
from collections import defaultdict

BASE=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env={}
for line in open(os.path.join(BASE,'.env')).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k,v=line.split('=',1);env[k.strip()]=v.strip()

AK=env['STORE_02_AWS_ACCESS_KEY_ID'];SK=env['STORE_02_AWS_SECRET_ACCESS_KEY']
CID=env['STORE_02_LWA_CLIENT_ID'];CSEC=env['STORE_02_LWA_CLIENT_SECRET']
REF=env['STORE_02_REFRESH_TOKEN_AMERICAS']

def sign(url, headers):
    p=urllib.parse.urlparse(url);ad=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime());ds=time.strftime('%Y%m%d',time.gmtime())
    headers['x-amz-date']=ad;headers['host']=p.hostname
    cu=urllib.parse.quote(p.path,safe='/') or '/'
    ch=''.join(f"{k.lower()}:{v.strip()}\n" for k,v in sorted(headers.items()) if k.lower()!='authorization')
    sh=';'.join(sorted(k.lower() for k in headers if k.lower()!='authorization'))
    cr='\n'.join(['GET',cu,p.query,ch,sh,hashlib.sha256(b'').hexdigest()])
    cs=f"{ds}/us-east-1/execute-api/aws4_request"
    st='\n'.join(['AWS4-HMAC-SHA256',ad,cs,hashlib.sha256(cr.encode()).hexdigest()])
    def h8(k,m):
        if isinstance(k,str):k=k.encode()
        return hmac.new(k,m.encode(),hashlib.sha256).digest()
    headers['Authorization']=f"AWS4-HMAC-SHA256 Credential={AK}/{cs},SignedHeaders={sh},Signature={hmac.new(h8(h8(h8(h8(f'AWS4{SK}',ds),'us-east-1'),'execute-api'),'aws4_request'),st.encode(),hashlib.sha256).hexdigest()}"
    return headers

# Auth
r=httpx.post('https://api.amazon.com/auth/o2/token',json={'grant_type':'refresh_token','client_id':CID,'client_secret':CSEC,'refresh_token':REF},timeout=15)
tk=r.json()['access_token']

refunds_by_sku=defaultdict(lambda: {'count':0,'amount':0.0,'orders':set()})
nt=None;pages=0
while True:
    pages+=1
    u=f'https://sellingpartnerapi-na.amazon.com/finances/v0/financialEvents?PostedAfter=2026-04-01T00:00:00Z&PostedBefore=2026-06-30T23:59:59Z&MaxResults=100'
    if nt: u+=f'&NextToken={nt}'
    h=sign(u,{'x-amz-access-token':tk})
    r2=httpx.get(u,headers=h,timeout=30)
    if r2.status_code!=200: break
    d=r2.json().get('payload',r2.json())
    for e in d.get('FinancialEvents',{}).get('RefundEventList',[]):
        oid=e.get('AmazonOrderId','?')
        for it in e.get('ShipmentItemAdjustmentList',[]):
            sku=it.get('SellerSKU','?')
            for c in it.get('ItemChargeAdjustmentList',[]):
                if c.get('ChargeType')=='Principal':
                    amt=abs(float(c.get('ChargeAmount',{}).get('CurrencyAmount',0) or 0))
                    refunds_by_sku[sku]['count']+=1
                    refunds_by_sku[sku]['amount']+=amt
                    refunds_by_sku[sku]['orders'].add(oid)
    nt=d.get('NextToken')
    if not nt: break
    if pages%10==0: print(f'  Page {pages}...',flush=True)

print()
print('Top Refunded SKUs (CUCZUUS NA, Apr-Jun):')
for sku,data in sorted(refunds_by_sku.items(),key=lambda x:-x[1]['amount'])[:20]:
    print(f'  {sku}: {data["count"]} units, ${data["amount"]:,.2f}, {len(data["orders"])} orders')
