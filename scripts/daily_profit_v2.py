"""Daily Gross Profit (before storage/disposal) — July 1, all 3 stores."""
import httpx,hashlib,hmac,time,urllib.parse,json,os,openpyxl
from collections import defaultdict

BASE=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env={}
for line in open(os.path.join(BASE,'.env')).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k,v=line.split('=',1);env[k.strip()]=v.strip()

AK=env['STORE_02_AWS_ACCESS_KEY_ID'];SK=env['STORE_02_AWS_SECRET_ACCESS_KEY']
CNY2USD=0.1477  # 1 CNY = 0.1477 USD (~6.77 CNY/USD)
FX2USD={'USD':1.0,'CAD':0.7033,'AUD':0.6892,'JPY':0.00614,'EUR':1.1403}

STORES={
    'CUCZUUS':{'CID':env['STORE_02_LWA_CLIENT_ID'],'CSEC':env['STORE_02_LWA_CLIENT_SECRET'],
               'NA':env['STORE_02_REFRESH_TOKEN_AMERICAS'],'FE':env.get('STORE_02_REFRESH_TOKEN_AU'),'EU':env['STORE_02_REFRESH_TOKEN_EU']},
    'BOOLUU':{'CID':env['STORE_03_LWA_CLIENT_ID'],'CSEC':env['STORE_03_LWA_CLIENT_SECRET'],
               'NA':env['STORE_03_REFRESH_TOKEN_AMERICAS'],'FE':env.get('STORE_03_REFRESH_TOKEN_AU'),'EU':None},
    'Heliumx':{'CID':env['STORE_04_LWA_CLIENT_ID'],'CSEC':env['STORE_04_LWA_CLIENT_SECRET'],
               'NA':env['STORE_04_REFRESH_TOKEN_AMERICAS'],'FE':env.get('STORE_04_REFRESH_TOKEN_AU'),'EU':env['STORE_04_REFRESH_TOKEN_EU']},
}
HOSTS={'NA':'sellingpartnerapi-na.amazon.com','FE':'sellingpartnerapi-fe.amazon.com','EU':'sellingpartnerapi-eu.amazon.com'}

# Freight rates
FREIGHT={'US':{'mode':'kg','rate':5.0},'CA':{'mode':'cbm','rate':1200},'AU':{'mode':'cbm','rate':1200},'JP':{'mode':'cbm','rate':1200},'DE':{'mode':'cbm','rate':1500}}

def sign(url, headers):
    p=urllib.parse.urlparse(url);ad=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime());ds=time.strftime('%Y%m%d',time.gmtime())
    headers['x-amz-date']=ad;headers['host']=p.hostname
    cu=urllib.parse.quote(p.path,safe='/') or '/'
    ch=''.join('{}:{}\n'.format(k.lower(),v.strip()) for k,v in sorted(headers.items()) if k.lower()!='authorization')
    sh=';'.join(sorted(k.lower() for k in headers if k.lower()!='authorization'))
    cr='\n'.join(['GET',cu,p.query,ch,sh,hashlib.sha256(b'').hexdigest()])
    cs=ds+'/us-east-1/execute-api/aws4_request'
    st='\n'.join(['AWS4-HMAC-SHA256',ad,cs,hashlib.sha256(cr.encode()).hexdigest()])
    def h8(k,m):
        if isinstance(k,str):k=k.encode()
        return hmac.new(k,m.encode(),hashlib.sha256).digest()
    headers['Authorization']='AWS4-HMAC-SHA256 Credential={}/{},SignedHeaders={},Signature={}'.format(
        AK,cs,sh,hmac.new(h8(h8(h8(h8('AWS4'+SK,ds),'us-east-1'),'execute-api'),'aws4_request'),st.encode(),hashlib.sha256).hexdigest())
    return headers

# Load catalog
print('1. Loading catalog...',flush=True)
wb=openpyxl.load_workbook(os.path.join(BASE,'data','Master_Product_Catalog_v2.xlsx'),data_only=True)
catalog={}
for row in wb.active.iter_rows(min_row=2,values_only=True):
    if row[0] and row[2]:
        store,mkt,asin=str(row[0]),str(row[1]),str(row[2])
        l=float(row[4] or 0);w=float(row[5] or 0);h=float(row[6] or 0);kg=float(row[7] or 0)
        cogs=float(row[8] or 0);pkg=float(row[9] or 0) if row[9] else 0
        catalog[(store,mkt,asin)]={'l':l,'w':w,'h':h,'kg':kg,'cogs':cogs,'pkg':pkg}
print('   {} products'.format(len(catalog)),flush=True)

def calc_freight(mkt,l,w,h,kg):
    cfg=FREIGHT.get(mkt,{})
    mode=cfg.get('mode','kg');rate=cfg.get('rate',0)
    if mode=='kg':
        vol_kg=l*w*h/6000
        return max(kg,vol_kg)*rate
    else:
        vol_m3=l*w*h/1_000_000
        return vol_m3*rate

# Step 2: Pull orders + ads
print('2. Pulling July 1 orders...',flush=True)
lwa_cache={}
orders_by_store=defaultdict(list)

for store,creds in STORES.items():
    for region in ['NA','FE','EU']:
        ref=creds[region]
        if not ref: continue
        ckey=(store,region)
        if ckey not in lwa_cache:
            r=httpx.post('https://api.amazon.com/auth/o2/token',json={'grant_type':'refresh_token','client_id':creds['CID'],'client_secret':creds['CSEC'],'refresh_token':ref},timeout=15)
            if r.status_code!=200: continue
            lwa_cache[ckey]=r.json()['access_token']
        tk=lwa_cache[ckey];host=HOSTS[region];nt=None
        while True:
            u='https://{}/finances/v0/financialEvents?PostedAfter=2026-07-01T00:00:00Z&PostedBefore=2026-07-01T23:59:59Z&MaxResults=100'.format(host)
            if nt: u+='&NextToken='+nt
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
                    orders_by_store[store].append({'sku':sku,'price':price,'fba':fba,'cur':cur,'region':region})
            nt=d.get('NextToken')
            if not nt: break
print('   {} orders total'.format(sum(len(v) for v in orders_by_store.values())),flush=True)

# Step 3: Match with catalog + compute profit
print('3. Computing profit...',flush=True)
store_summary={}
total_rev=total_cogs=total_freight=total_fba=total_ads=total_profit=0
total_orders=0

# Ads: allocate based on daily total per store-market
ads_by_store={}
# TODO: pull from DB. For now, estimate from June 30 data
ads_est={'CUCZUUS':267.55/3,'BOOLUU':84.59/3,'Heliumx':39.86/3}  # June 30 daily / 3

for store,items in orders_by_store.items():
    n=len(items)
    if n==0: continue
    rev=sum(it['price']*FX2USD[it['cur']] for it in items)
    fba_total=sum(it['fba']*FX2USD[it['cur']] for it in items)
    
    cogs_total=freight_total=0;matched=0
    for it in items:
        prod=None
        for (s,m,a),p in catalog.items():
            if s==store:
                prod=p;break
        if prod:
            cogs_total+=prod['cogs']*CNY2USD
            freight_total+=calc_freight('US',prod['l'],prod['w'],prod['h'],prod['kg'])*CNY2USD
            matched+=1
    
    ads_alloc=ads_est.get(store,0)
    if matched==0:
        avg_cogs=0;avg_freight=0
    else:
        avg_cogs=cogs_total/matched;avg_freight=freight_total/matched
    cogs_total=avg_cogs*n
    freight_total=avg_freight*n
    
    profit=rev-cogs_total-freight_total-fba_total-ads_alloc
    margin=profit/rev*100 if rev else 0
    
    store_summary[store]={
        'orders':n,'rev':rev,'cogs':cogs_total,'freight':freight_total,
        'fba':fba_total,'ads':ads_alloc,'profit':profit,'margin':margin
    }
    total_rev+=rev;total_cogs+=cogs_total;total_freight+=freight_total
    total_fba+=fba_total;total_ads+=ads_alloc;total_profit+=profit;total_orders+=n
    print('  {}: {} orders, rev=${:,.2f}, profit=${:,.2f} ({:.0f}%)'.format(store,n,rev,profit,margin),flush=True)

print()
print('='*70)
print('DAILY GROSS PROFIT — July 1, 2026 (before storage/disposal)')
print('='*70)
print('{:15} {:>8} {:>10} {:>10} {:>10} {:>10} {:>10}'.format('', 'Orders', 'Revenue', 'COGS', 'Freight', 'FBA', 'Ads'))
for store in ['CUCZUUS','BOOLUU','Heliumx']:
    s=store_summary.get(store,{})
    if not s: continue
    print('{:15} {:>8} ${:>9,.2f} ${:>9,.2f} ${:>9,.2f} ${:>9,.2f} ${:>9,.2f}'.format(
        store,s['orders'],s['rev'],s['cogs'],s['freight'],s['fba'],s['ads']))
print('-'*70)
print('{:15} {:>8} ${:>9,.2f} ${:>9,.2f} ${:>9,.2f} ${:>9,.2f} ${:>9,.2f}'.format(
    'TOTAL',total_orders,total_rev,total_cogs,total_freight,total_fba,total_ads))
print()
print('GROSS PROFIT: ${:,.2f}'.format(total_profit))
print('PROFIT MARGIN: {:.1f}%'.format(total_profit/total_rev*100 if total_rev else 0))
