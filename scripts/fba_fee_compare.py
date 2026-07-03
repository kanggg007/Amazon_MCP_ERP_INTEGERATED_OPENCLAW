"""FBA Fee Comparison v2 — LWA caching, correct marketplace IDs."""
import httpx,hashlib,hmac,time,urllib.parse,json,os
from collections import defaultdict

BASE=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env={}
for line in open(os.path.join(BASE,'.env')).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k,v=line.split('=',1);env[k.strip()]=v.strip()

AK=env['STORE_02_AWS_ACCESS_KEY_ID'];SK=env['STORE_02_AWS_SECRET_ACCESS_KEY']

STORES={
    'CUCZUUS':{'CID':env['STORE_02_LWA_CLIENT_ID'],'CSEC':env['STORE_02_LWA_CLIENT_SECRET'],'sid':'AXW589ZKKDJNM',
               'NA':env['STORE_02_REFRESH_TOKEN_AMERICAS'],'FE':env['STORE_02_REFRESH_TOKEN_AU'],'EU':env['STORE_02_REFRESH_TOKEN_EU']},
    'BOOLUU':{'CID':env['STORE_03_LWA_CLIENT_ID'],'CSEC':env['STORE_03_LWA_CLIENT_SECRET'],'sid':'A8P0GUSN1BWCC',
               'NA':env['STORE_03_REFRESH_TOKEN_AMERICAS'],'FE':env['STORE_03_REFRESH_TOKEN_AU'],'EU':None},
    'Heliumx':{'CID':env['STORE_04_LWA_CLIENT_ID'],'CSEC':env['STORE_04_LWA_CLIENT_SECRET'],'sid':'AD4UKHMR5K35J',
               'NA':env['STORE_04_REFRESH_TOKEN_AMERICAS'],'FE':env['STORE_04_REFRESH_TOKEN_AU'],'EU':env['STORE_04_REFRESH_TOKEN_EU']},
}
HOSTS={'NA':'sellingpartnerapi-na.amazon.com','FE':'sellingpartnerapi-fe.amazon.com','EU':'sellingpartnerapi-eu.amazon.com'}
CUR_MID={'USD':'ATVPDKIKX0DER','CAD':'A2EUQ1WTGCTBG2','AUD':'A39IBJ37TRP1C6','JPY':'A1VC38T7YXB528','EUR':'A1PA6795UKMFR9'}

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

# LWA token cache: (store,region) -> {token, expires}
lwa_tokens={}

def get_lwa(store,region):
    key=(store,region)
    if key in lwa_tokens: return lwa_tokens[key]
    ref=STORES[store][region]
    if not ref: return None
    r=httpx.post('https://api.amazon.com/auth/o2/token',
        json={'grant_type':'refresh_token','client_id':STORES[store]['CID'],
              'client_secret':STORES[store]['CSEC'],'refresh_token':ref},timeout=15)
    if r.status_code!=200: return None
    tk=r.json()['access_token']
    lwa_tokens[key]=tk
    return tk

# Step 1: Pull orders
print('1. Pulling July 1 orders...',flush=True)
orders_by_sku=defaultdict(list)

for store,creds in STORES.items():
    for region in ['NA','FE','EU']:
        tk=get_lwa(store,region)
        if not tk: continue
        host=HOSTS[region];nt=None
        while True:
            u=f'https://{host}/finances/v0/financialEvents?PostedAfter=2026-07-01T00:00:00Z&PostedBefore=2026-07-01T23:59:59Z&MaxResults=100'
            if nt: u+=f'&NextToken={nt}'
            h=sign(u,{'x-amz-access-token':tk})
            r2=httpx.get(u,headers=h,timeout=30)
            if r2.status_code!=200: break
            d=r2.json().get('payload',r2.json())
            for e in d.get('FinancialEvents',{}).get('ShipmentEventList',[]):
                for it in e.get('ShipmentItemList',[]):
                    sku=it.get('SellerSKU','');price=0;fba=0;cur='USD'
                    for c in it.get('ItemChargeList',[]):
                        if c.get('ChargeType')=='Principal':
                            price=float(c.get('ChargeAmount',{}).get('CurrencyAmount',0) or 0)
                            cur=c.get('ChargeAmount',{}).get('CurrencyCode','USD')
                    for f in it.get('ItemFeeList',[]):
                        if f.get('FeeType') in ('FBAPerUnitFulfillmentFee','FBAWeightBasedFee'):
                            fba+=abs(float(f.get('FeeAmount',{}).get('CurrencyAmount',0) or 0))
                    if fba>0:
                        orders_by_sku[(store,region,sku,cur)].append({'price':price,'actual':fba})
            nt=d.get('NextToken')
            if not nt: break

n=len(orders_by_sku)
print(f'   {n} unique combos',flush=True)

# Step 2: Map SKU->ASIN via Listings API
print('2. Mapping SKU->ASIN...',flush=True)
sku_asin={}

for (store,region,sku,cur),items in orders_by_sku.items():
    key=(store,region,sku)
    if key in sku_asin: continue
    tk=get_lwa(store,region)
    if not tk: continue
    sid=STORES[store]['sid'];mid=CUR_MID.get(cur,'ATVPDKIKX0DER')
    r3=httpx.get(
        f'https://{HOSTS[region]}/listings/2021-08-01/items/{sid}/{sku}?marketplaceIds={mid}',
        headers={'x-amz-access-token':tk},timeout=15)
    if r3.status_code==200:
        a=r3.json().get('summaries',[{}])[0].get('asin','')
        if a: sku_asin[key]=a
    elif r3.status_code==429:
        time.sleep(2)
        r3=httpx.get(
            f'https://{HOSTS[region]}/listings/2021-08-01/items/{sid}/{sku}?marketplaceIds={mid}',
            headers={'x-amz-access-token':tk},timeout=15)
        if r3.status_code==200 and r3.json().get('summaries',[{}])[0].get('asin',''):
            sku_asin[key]=r3.json()['summaries'][0]['asin']
    time.sleep(0.5)

print(f'   Mapped {len(sku_asin)}',flush=True)

# Step 3: Products Fee API comparison
print('3. Comparing...',flush=True)
results=[]

for (store,region,sku,cur),items in orders_by_sku.items():
    asin=sku_asin.get((store,region,sku),'')
    if not asin: continue
    avg_price=sum(i['price'] for i in items)/len(items)
    avg_actual=sum(i['actual'] for i in items)/len(items)
    tk=get_lwa(store,region)
    if not tk: continue
    mid=CUR_MID.get(cur,'ATVPDKIKX0DER')
    body={'FeesEstimateRequest':{'MarketplaceId':mid,'IsAmazonFulfilled':True,
        'PriceToEstimateFees':{'ListingPrice':{'CurrencyCode':cur,'Amount':avg_price}},'Identifier':sku}}
    r2=httpx.post(f'https://{HOSTS[region]}/products/fees/v0/items/{asin}/feesEstimate',
        headers={'x-amz-access-token':tk,'Content-Type':'application/json'},json=body,timeout=15)
    if r2.status_code!=200: continue
    d=r2.json().get('payload',r2.json())
    fe=d.get('FeesEstimateResult',{}).get('FeesEstimate',{})
    api_fee=0
    for f in fe.get('FeeDetailList',[]):
        if f.get('FeeType','')=='FBAFees':
            api_fee+=float(f.get('FinalFee',{}).get('Amount',0) or 0)
    diff=avg_actual-api_fee
    flag='OK' if abs(diff)<0.10 else ('OVERCHARGE' if diff>0 else 'UNDER')
    results.append({
        'store':store,'sku':sku,'cur':cur,'units':len(items),
        'price':round(avg_price,2),'actual':round(avg_actual,2),
        'api':round(api_fee,2),'diff':round(diff,2),'flag':flag
    })

# Display
print()
for r in sorted(results,key=lambda x:-abs(x['diff'])):
    s='{} {:<30} {:>3}u act=${:>6.2f} api=${:>6.2f} diff=${:+6.2f} {}'
    print(s.format(r['store'][:4],r['sku'][:30],r['units'],r['actual'],r['api'],r['diff'],r['flag']))

ok=sum(1 for r in results if r['flag']=='OK')
over=sum(1 for r in results if r['flag']=='OVERCHARGE')
total_over=sum(r['diff']*r['units'] for r in results if r['diff']>0.10)
print()
print('OK: {}  OVERCHARGE: {}  Total: ${:,.2f} across {} results'.format(ok,over,total_over,len(results)))
