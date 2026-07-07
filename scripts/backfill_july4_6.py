"""Backfill July 4, 5, 6 — pull v2026 + compute live profit"""
import httpx, os, sys, time, psycopg2
from datetime import datetime, timedelta
from psycopg2.extras import execute_values

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = {}
for line in open(os.path.join(BASE, '.env')).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

dsn = env['DATABASE_URL']
conn = psycopg2.connect(dsn, connect_timeout=15)
cur = conn.cursor()

stores = [('CUCZUUS','02','AXW589ZKKDJNM'),('BOOLUU','03','A8P0GUSN1BWCC'),('Heliumx','04','AD4UKHMR5K35J')]
regions = [('NA','ATVPDKIKX0DER,A2EUQ1WTGCTBG2'),('FE','A39IBJ37TRP1C6,A1VC38T7YXB528'),('EU','A1PA6795UKMFR9')]
HOSTS = {'NA':'sellingpartnerapi-na.amazon.com','FE':'sellingpartnerapi-fe.amazon.com','EU':'sellingpartnerapi-eu.amazon.com'}
MID_TO_MKT = {'ATVPDKIKX0DER':'US','A2EUQ1WTGCTBG2':'CA','A39IBJ37TRP1C6':'AU','A1VC38T7YXB528':'JP','A1PA6795UKMFR9':'DE'}

sys.path.insert(0, BASE)
from engines.live_profit import compute_profit, daily_summary, ensure_tables
ensure_tables()

for day_dt in [datetime(2026,7,4), datetime(2026,7,5), datetime(2026,7,6)]:
    after = day_dt.strftime('%Y-%m-%dT07:00:00Z')
    day = day_dt.strftime('%Y-%m-%d')
    all_items = []
    
    print(f'\n{"="*60}\n{day}\n{"="*60}')
    
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
            
            do=[];di=[];nt=None
            while True:
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
                        qty=int(it.get('quantityOrdered',1)or 1)
                        sku=p.get('sellerSku','')
                        di.append((oid,it.get('orderItemId',''),sku,asin,qty,price,p.get('price',{}).get('unitPrice',{}).get('currencyCode','USD'),(p.get('title','')or'')[:200]))
                        all_items.append({'order_id':oid,'store':store,'mkt':mkt,'sku':sku,'qty':qty,'asin':asin,'price':price})
                nt=odata.get('pagination',{}).get('nextToken')
                if not nt:break;time.sleep(0.3)
            
            if do:
                execute_values(cur,'INSERT INTO orders_active (order_id,store,marketplace,marketplace_id,seller_id,status,fulfillment_type,purchase_date,last_updated) VALUES %s ON CONFLICT (order_id) DO NOTHING',do,template='(%s,%s,%s,%s,%s,%s,%s,%s,NOW())',page_size=500)
                if di:execute_values(cur,'INSERT INTO order_items_active (order_id,order_item_id,seller_sku,asin,quantity,unit_price,currency,title) VALUES %s ON CONFLICT (order_id,order_item_id) DO NOTHING',di,page_size=500)
                conn.commit()
            time.sleep(1)
    
    print(f'Loaded: {len(all_items)} items')
    
    # Compute profit
    flagged=0
    for it in all_items:
        result=compute_profit(it['order_id'],it['store'],it['mkt'],it['sku'],it['qty'],day)
        if result and 'error' not in result:
            f=result.get('flags','?')
            if f!='OK':flagged+=1
            est='⚠️' if f!='OK' else '✓'
            st=it['store'][:6]; mk=it['mkt']; a=it['asin']; rev=result['revenue']; prof=result['profit']
            print(f'{est} {st:6} {mk:3} {a:14} ${rev:.2f} profit ${prof:.2f} [{f}]')
    
    if flagged:print(f'⚠️ {flagged} items with missing costs')
    
    # Summary
    # Summary
    s=daily_summary(day)
    for row in s["summary"]:
        st=row["store"]; mk=row["marketplace"]; o=row["orders"]; u=row["units"]
        rev=row["revenue"]; prof=row["profit"]; mrg=row["margin"]
        print(f"  {st:10} {mk:>4} {o:>5}o {u:>5}u rev ${rev:>10,.2f} profit ${prof:>10,.2f} ({mrg}%)")
    t=s["totals"]
    st="TOTAL"; o=t["orders"]; u=t["units"]; rev=t["revenue"]; prof=t["profit"]; mrg=t["margin"]
    print(f"  {st:10}     {o:>5}o {u:>5}u rev ${rev:>10,.2f} profit ${prof:>10,.2f} ({mrg}%)")
    print()
    print(f'  {st:10}     {o:>5}o {u:>5}u rev ${rev:>10,.2f} profit ${prof:>10,.2f} ({mrg}%)')

conn.close()
print('\nDone')
