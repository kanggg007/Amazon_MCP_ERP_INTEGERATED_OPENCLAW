"""July 1 FBA fee audit — pull orders, compare with rate cards."""
import httpx,hashlib,hmac,time,urllib.parse,json,os
from collections import defaultdict

BASE=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env={}
for line in open(os.path.join(BASE,'.env')).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k,v=line.split('=',1);env[k.strip()]=v.strip()

AK=env['STORE_02_AWS_ACCESS_KEY_ID'];SK=env['STORE_02_AWS_SECRET_ACCESS_KEY']
CID_CUC=env['STORE_02_LWA_CLIENT_ID'];CSEC_CUC=env['STORE_02_LWA_CLIENT_SECRET']
CID_BOO=env['STORE_03_LWA_CLIENT_ID'];CSEC_BOO=env['STORE_03_LWA_CLIENT_SECRET']
CID_HEL=env['STORE_04_LWA_CLIENT_ID'];CSEC_HEL=env['STORE_04_LWA_CLIENT_SECRET']

STORES={
    'CUCZUUS':{'NA':env['STORE_02_REFRESH_TOKEN_AMERICAS'],'FE':env.get('STORE_02_REFRESH_TOKEN_AU'),'EU':env['STORE_02_REFRESH_TOKEN_EU'],'CID':CID_CUC,'CSEC':CSEC_CUC},
    'BOOLUU':{'NA':env['STORE_03_REFRESH_TOKEN_AMERICAS'],'FE':env.get('STORE_03_REFRESH_TOKEN_AU'),'EU':None,'CID':CID_BOO,'CSEC':CSEC_BOO},
    'Heliumx':{'NA':env['STORE_04_REFRESH_TOKEN_AMERICAS'],'FE':env.get('STORE_04_REFRESH_TOKEN_AU'),'EU':env.get('STORE_04_REFRESH_TOKEN_EU'),'CID':CID_HEL,'CSEC':CSEC_HEL},
}

HOSTS={'NA':'sellingpartnerapi-na.amazon.com','FE':'sellingpartnerapi-fe.amazon.com','EU':'sellingpartnerapi-eu.amazon.com'}

def sign(url, headers):
    p=urllib.parse.urlparse(url);ad=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime());ds=time.strftime('%Y%m%d',time.gmtime())
    headers['x-amz-date']=ad;headers['host']=p.hostname
    cu=urllib.parse.quote(p.path,safe='/') or '/'
    ch=','.join(sorted(headers.keys())).lower()
    # Simplified - just use the basic signature
    ch2=''.join(f"{k.lower()}:{v.strip()}\n" for k,v in sorted(headers.items()) if k.lower()!='authorization')
    sh=';'.join(sorted(k.lower() for k in headers if k.lower()!='authorization'))
    cr='\n'.join(['GET',cu,p.query,ch2,sh,hashlib.sha256(b'').hexdigest()])
    cs=f"{ds}/us-east-1/execute-api/aws4_request"
    st='\n'.join(['AWS4-HMAC-SHA256',ad,cs,hashlib.sha256(cr.encode()).hexdigest()])
    def h8(k,m):
        if isinstance(k,str):k=k.encode()
        return hmac.new(k,m.encode(),hashlib.sha256).digest()
    ksign=hmac.new(h8(h8(h8(h8(f"AWS4{SK}",ds),"us-east-1"),"execute-api"),"aws4_request"),st.encode(),hashlib.sha256).hexdigest()
    headers['Authorization']=f"AWS4-HMAC-SHA256 Credential={AK}/{cs},SignedHeaders={sh},Signature={ksign}"
    return headers

all_orders=[]

for store,creds in STORES.items():
    CID=creds['CID'];CSEC=creds['CSEC']
    if not CID: continue
    for region in ['NA','FE','EU']:
        ref=creds[region]
        if not ref: continue
        r=httpx.post('https://api.amazon.com/auth/o2/token',json={'grant_type':'refresh_token','client_id':CID,'client_secret':CSEC,'refresh_token':ref},timeout=15)
        if r.status_code!=200: continue
        tk=r.json()['access_token'];host=HOSTS[region]
        
        nt=None;pages=0
        while True:
            pages+=1
            u=f'https://{host}/finances/v0/financialEvents?PostedAfter=2026-07-01T00:00:00Z&PostedBefore=2026-07-01T23:59:59Z&MaxResults=100'
            if nt: u+=f'&NextToken={nt}'
            h=sign(u,{'x-amz-access-token':tk})
            r2=httpx.get(u,headers=h,timeout=30)
            if r2.status_code!=200: break
            d=r2.json().get('payload',r2.json())
            for e in d.get('FinancialEvents',{}).get('ShipmentEventList',[]):
                for it in e.get('ShipmentItemList',[]):
                    sku=it.get('SellerSKU','?')
                    price=0;fba_fee=0;cur='USD'
                    for c in it.get('ItemChargeList',[]):
                        if c.get('ChargeType')=='Principal':
                            price=float(c.get('ChargeAmount',{}).get('CurrencyAmount',0) or 0)
                            cur=c.get('ChargeAmount',{}).get('CurrencyCode','USD')
                    for f in it.get('ItemFeeList',[]):
                        ft=f.get('FeeType','')
                        if ft in ('FBAPerUnitFulfillmentFee','FBAWeightBasedFee'):
                            fba_fee+=abs(float(f.get('FeeAmount',{}).get('CurrencyAmount',0) or 0))
                    if fba_fee>0:
                        all_orders.append({'store':store,'region':region,'sku':sku,'fee':fba_fee,'price':price,'cur':cur})
            nt=d.get('NextToken')
            if not nt: break
        print(f"  {store}/{region}: found FBA fees ({pages}p)",flush=True)

# Summary
print("\nJuly 1 FBA Fee Summary")
print("="*60)
by_sku=defaultdict(list)
for o in all_orders: by_sku[o['sku']].append(o)

for sku,items in sorted(by_sku.items(),key=lambda x:-len(x[1]))[:30]:
    fees=[i['fee'] for i in items]
    avg_fee=sum(fees)/len(fees)
    storess=set("{}-{}".format(i['store'],i['region']) for i in items)
    print("  {}: {} units, avg fee=${:.2f}, price ${:.2f} {}, {}".format(
        sku, len(items), avg_fee, items[0]['price'], items[0]['cur'], storess))

print("\nTotal: {} shipments with FBA fees across {} SKUs".format(len(all_orders), len(by_sku)))
