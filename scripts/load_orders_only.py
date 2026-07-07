"""Load-only backfill: pull v2026 → INSERT DB. No profit compute."""
import httpx, os, time, psycopg2
from datetime import datetime, timedelta
from psycopg2.extras import execute_values

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = {}
for line in open(os.path.join(BASE, '.env')).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

dsn = env['DATABASE_URL']

stores = [('CUCZUUS','02','AXW589ZKKDJNM'),('BOOLUU','03','A8P0GUSN1BWCC'),('Heliumx','04','AD4UKHMR5K35J')]
regions = [('NA','ATVPDKIKX0DER,A2EUQ1WTGCTBG2'),('FE','A39IBJ37TRP1C6,A1VC38T7YXB528'),('EU','A1PA6795UKMFR9')]
HOSTS = {'NA':'sellingpartnerapi-na.amazon.com','FE':'sellingpartnerapi-fe.amazon.com','EU':'sellingpartnerapi-eu.amazon.com'}
MID_TO_MKT = {'ATVPDKIKX0DER':'US','A2EUQ1WTGCTBG2':'CA','A39IBJ37TRP1C6':'AU','A1VC38T7YXB528':'JP','A1PA6795UKMFR9':'DE'}

conn = psycopg2.connect(dsn, connect_timeout=15)
cur = conn.cursor()

# Pull July 5 and 6
for day_dt in [datetime(2026,7,5), datetime(2026,7,6)]:
    after = day_dt.strftime('%Y-%m-%dT07:00:00Z')
    day = day_dt.strftime('%Y-%m-%d')
    
    print(f'\n{day}')
    day_orders = 0; day_items = 0
    
    for store,snum,sid in stores:
        cid=env.get(f'STORE_{snum}_LWA_CLIENT_ID','');csec=env.get(f'STORE_{snum}_LWA_CLIENT_SECRET','')
        if not cid:continue
        for region,mids in regions:
            ref_key={'NA':'AMERICAS','FE':'AU','EU':'EU'}[region]
            ref=env.get(f'STORE_{snum}_REFRESH_TOKEN_{ref_key}','')
            if not ref:continue
            r=httpx.post('https://api.amazon.com/auth/o2/token',json={'grant_type':'refresh_token','client_id':cid,'client_secret':csec,'refresh_token':ref},timeout=15)
            if r.status_code!=200:continue
            tk=r.json()['access_token'];host=HOSTS[region]
            
            do=[];di=[];nt=None;pages=0
            while True:
                pages+=1
                u=f'https://{host}/orders/2026-01-01/orders?marketplaceIds={mids}&createdAfter={after}&MaxResultsPerPage=100'
                if nt:u+='&nextToken='+nt
                r2=httpx.get(u,headers={'x-amz-access-token':tk},timeout=30)
                if r2.status_code!=200:break
                odata=r2.json();orders=odata.get('orders',[])
                if not orders:break
                for o in orders:
                    ct=o.get('createdTime','')
                    if ct:
                        try:ld=(datetime.fromisoformat(str(ct).replace('Z','+00:00'))-timedelta(hours=7)).date()
                        except:ld=None
                        if ld!=day_dt.date():continue
                    oid=o['orderId'];mid=o.get('salesChannel',{}).get('marketplaceId','')
                    mkt=MID_TO_MKT.get(mid,'US');do.append((oid,store,mkt,mid,sid,'FOUND','FBA',ct or f'{day}T00:00:00Z'))
                    for it in o.get('orderItems',[]):
                        p=it.get('product',{});asin=p.get('asin','')
                        if not asin:continue
                        price=float(p.get('price',{}).get('unitPrice',{}).get('amount',0)or 0)
                        if price<=0:continue
                        qty=int(it.get('quantityOrdered',1)or 1)
                        sku=p.get('sellerSku','')
                        di.append((oid,it.get('orderItemId',''),sku,asin,qty,price,p.get('price',{}).get('unitPrice',{}).get('currencyCode','USD'),(p.get('title','')or'')[:200]))
                nt=odata.get('pagination',{}).get('nextToken')
                if not nt:break;time.sleep(0.3)
            
            if do:
                execute_values(cur,'INSERT INTO orders_active (order_id,store,marketplace,marketplace_id,seller_id,status,fulfillment_type,purchase_date,last_updated) VALUES %s ON CONFLICT (order_id) DO NOTHING',do,template='(%s,%s,%s,%s,%s,%s,%s,%s,NOW())',page_size=500)
                if di:execute_values(cur,'INSERT INTO order_items_active (order_id,order_item_id,seller_sku,asin,quantity,unit_price,currency,title) VALUES %s ON CONFLICT (order_id,order_item_id) DO NOTHING',di,page_size=500)
                conn.commit()
                day_orders+=len(do);day_items+=len(di)
                # Summary per store/region
                rev=sum(it[5]*it[4] for it in di)
                print(f'  {store}/{region}: {len(do)} orders, {len(di)} items ({pages}p)')
            time.sleep(1.5)
    
    total_rev=sum(it[5]*it[4] for it in di) if 'di' in dir() else 0
    print(f'  TOTAL: {day_orders} orders, {day_items} items')

conn.close()
print('\nLoad complete. Now run: POST /admin/compute-profit?date=2026-07-05 etc.')
