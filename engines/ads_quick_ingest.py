"""Ads ingestion with de-duplication, DB skip-check, exponential backoff."""
import os,httpx,json,gzip,time,psycopg2,threading
from datetime import datetime,timedelta

DSN=os.environ.get("DATABASE_URL","postgresql://postgres:ZTHtVHerPtatmfNeCufdSaqieNjmfxmW@acela.proxy.rlwy.net:58049/railway?sslmode=require")
FX_USD={"US":1.0,"CA":0.2059,"AU":0.2102,"JP":0.0063,"DE":0.95}
HOSTS={"na":"advertising-api.amazon.com","fe":"advertising-api-fe.amazon.com","eu":"advertising-api-eu.amazon.com"}

REPORT_TYPES={
    "sp_campaigns":{"reportTypeId":"spCampaigns","groupBy":["campaign"],"columns":"cost,campaignName,campaignStatus,sales1d,clicks,impressions,campaignBudgetAmount,campaignBiddingStrategy"},
    "sp_search_terms":{"reportTypeId":"spSearchTerm","groupBy":["searchTerm"],"columns":"searchTerm,keyword,matchType,cost,clicks,impressions,sales1d,campaignName"},
    "sp_targeting":{"reportTypeId":"spTargeting","groupBy":["targeting"],"columns":"keyword,matchType,cost,clicks,impressions,sales1d,campaignName"},
    "sp_advertised_product":{"reportTypeId":"spAdvertisedProduct","groupBy":["advertiser"],"columns":"advertisedAsin,advertisedSku,cost,clicks,impressions,sales1d"},
    "sp_purchased_product":{"reportTypeId":"spPurchasedProduct","groupBy":["asin"],"columns":"purchasedAsin,advertisedAsin,advertisedSku,sales1d,sales7d,sales30d,purchases1d,purchases7d,purchases30d,campaignName"},
}

STORES={"CUCZUUS":"02","BOOLUU":"03","Heliumx":"04"}

PROFILES={
    "CUCZUUS":[("US","3465892858543553","na"),("CA","3474360124295876","na"),("AU","3457387109813217","fe"),("JP","1336746761611490","fe"),("DE","2148844609196157","eu")],
    "BOOLUU":[("US","2902488181372396","na"),("CA","1000058889542591","na"),("AU","671129043949754","fe"),("JP","1702417705815456","fe")],
    "Heliumx":[("US","1416052288010129","na"),("CA","3865316191299322","na"),("AU","422659196126799","fe"),("DE","4304939656717104","eu")],
}

# Global set to prevent duplicate report creation within the same process
_IN_FLIGHT=set()

def _already_in_db(store,market,rtype,date_str):
    """Check if data already exists in DB for this (store,market,rtype,date)."""
    try:
        conn=psycopg2.connect(DSN)
        cur=conn.cursor()
        cur.execute("SELECT 1 FROM ads_daily WHERE store=%s AND market=%s AND report_type=%s AND date=%s LIMIT 1",(store,market,rtype,date_str))
        exists=cur.fetchone() is not None
        conn.close()
        return exists
    except:return False

def pull_one(store,snum,market,pid,region,rtype,rtype_cfg,yesterday,skip_dedup=False):
    # 1. DEDUP: skip if already in DB
    if not skip_dedup and _already_in_db(store,market,rtype,yesterday):
        return f"{store}/{market}/{rtype}: SKIP (already in DB)"
    
    # 2. DEDUP: skip if already in flight
    flight_key=(store,market,rtype,yesterday)
    if flight_key in _IN_FLIGHT:
        return f"{store}/{market}/{rtype}: SKIP (in flight)"
    _IN_FLIGHT.add(flight_key)
    
    try:
        # Auth
        cid=os.environ[f"STORE_{snum}_ADS_CLIENT_ID"];csec=os.environ[f"STORE_{snum}_ADS_CLIENT_SECRET"];ref=os.environ[f"STORE_{snum}_ADS_REFRESH_TOKEN"]
        for a in range(3):
            r=httpx.post("https://api.amazon.com/auth/o2/token",json={"grant_type":"refresh_token","client_id":cid,"client_secret":csec,"refresh_token":ref},timeout=15)
            if r.status_code==200:break
            time.sleep(5*(2**a))
        else:return f"{store}/{market}/{rtype}: Auth FAIL"
        
        tk=r.json()["access_token"]
        host=HOSTS[region]
        h={"Authorization":f"Bearer {tk}","Amazon-Advertising-API-ClientId":cid,"Amazon-Advertising-API-Scope":pid,"Content-Type":"application/json"}
        cols=rtype_cfg["columns"]
        body={"startDate":yesterday,"endDate":yesterday,"configuration":{"adProduct":"SPONSORED_PRODUCTS","groupBy":rtype_cfg["groupBy"],"columns":cols.split(","),"reportTypeId":rtype_cfg["reportTypeId"],"timeUnit":"SUMMARY","format":"GZIP_JSON"}}
        
        # 3. Create report — exponential backoff for 425/429, max 5 attempts
        rid=None
        backoff=10
        for a in range(5):
            rr=httpx.post(f"https://{host}/reporting/reports",headers=h,json=body,timeout=15)
            if rr.status_code==200:
                rid=rr.json()["reportId"]
                break
            if rr.status_code==429:
                print(f"  [{store}/{market}/{rtype}] 429, backoff {backoff}s")
                time.sleep(backoff);backoff*=2;continue
            if rr.status_code==425:
                # Try get reportId from response, then from detail message
                try:
                    d=rr.json()
                    rid=d.get("reportId")
                    if not rid:
                        import re
                        m=re.search(r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})',d.get("detail",""))
                        rid=m.group(1) if m else None
                except:pass
                if rid:
                    print(f"  [{store}/{market}/{rtype}] 425 using existing report {rid}")
                    break
                print(f"  [{store}/{market}/{rtype}] 425, backoff {backoff}s")
                time.sleep(backoff);backoff*=2;continue
            return f"{store}/{market}/{rtype}: Create FAIL {rr.status_code}"
        if not rid:
            return f"{store}/{market}/{rtype}: Rate limited 5x"
        
        # 4. Poll: 10, 20, 30, 30, 30... (cap 30s, 15 polls max)
        poll_interval=10
        max_polls=25 if region=="fe" else 15
        for i in range(max_polls):
            time.sleep(poll_interval)
            rp=httpx.get(f"https://{host}/reporting/reports/{rid}",headers=h,timeout=10)
            s=rp.json().get("status","NO_KEY")
            if i==0 or i%5==0:
                print(f"  [{store}/{market}/{rtype}] poll {i}: {s} (interval={poll_interval}s)")
            if s=="COMPLETED":
                raw=gzip.decompress(httpx.get(rp.json()["url"],timeout=60).content).decode()
                rows=json.loads(raw)
                if isinstance(rows,list) and len(rows)==1 and isinstance(rows[0],list):rows=rows[0]
                cost=sum(float(r2.get("cost",0)) for r2 in rows if isinstance(r2,dict))
                sales=sum(float(r2.get("sales1d",0)) for r2 in rows if isinstance(r2,dict))
                # Convert local currency to USD
                fx=FX_USD.get(market,1.0)
                cost_usd=round(cost*fx,2);sales_usd=round(sales*fx,2)
                conn=psycopg2.connect(DSN)
                cur=conn.cursor()
                cur.execute("DELETE FROM ads_daily WHERE store=%s AND market=%s AND report_type=%s AND date=%s",(store,market,rtype,yesterday))
                cur.execute("INSERT INTO ads_daily (store,market,report_type,date,data,cost,sales) VALUES (%s,%s,%s,%s,%s,%s,%s)",(store,market,rtype,yesterday,json.dumps(rows),cost_usd,sales_usd))
                conn.commit();conn.close()
                return f"{store}/{market}/{rtype}: {len(rows)} rows, $%.2f USD"%cost_usd
            elif s=="FAILURE":
                return f"{store}/{market}/{rtype}: Report FAILURE"
            # Exponential backoff: double interval, cap at 120s
            poll_interval=min(poll_interval*2,30)
        return f"{store}/{market}/{rtype}: Timeout after {max_polls} polls"
    finally:
        _IN_FLIGHT.discard(flight_key)

def pull_profile(store,snum,market,pid,region,yesterday,results,skip_dedup=False):
    """FE: parallel types. NA/EU: sequential types (kinder to the API)."""
    # NA/EU: hard delay before touching the profile (Railway is too fast)
    if region in ("na","eu"):
        print(f"  [{store}/{market}] delay 30s (Railway too fast for NA/EU)",flush=True)
        time.sleep(30)
    if region=="fe":
        threads=[]
        def _pull(rtype,cfg):
            r=pull_one(store,snum,market,pid,region,rtype,cfg,yesterday,skip_dedup)
            results.append(r);print(r,flush=True)
        for rtype,cfg in REPORT_TYPES.items():
            t=threading.Thread(target=_pull,args=(rtype,cfg));t.start();threads.append(t)
        for t in threads:t.join()
    else:
        for rtype,cfg in REPORT_TYPES.items():
            r=pull_one(store,snum,market,pid,region,rtype,cfg,yesterday,skip_dedup)
            results.append(r);print(r,flush=True)

def run(date=None, skip_dedup=False):
    yesterday = date if date else (datetime.now()-timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Ingestion v3 — {yesterday}")
    t0=time.time()
    results=[];threads=[]
    for store in STORES:
        snum=STORES[store]
        for market,pid,region in PROFILES[store]:
            t=threading.Thread(target=pull_profile,args=(store,snum,market,pid,region,yesterday,results,skip_dedup))
            t.start();threads.append(t)
    for t in threads:t.join()
    total=len(results)
    ok=sum(1 for r in results if "SKIP" not in r and "rows" in r)
    print(f"Done. {total} tasks, {ok} stored in {int(time.time()-t0)}s")

def load_env():
    env_path=os.path.join(os.path.dirname(__file__) or '.','..','.env')
    if os.path.exists(env_path):
        for l in open(env_path).read().splitlines():
            if l and '=' in l and not l.startswith('#'):k,v=l.split('=',1);os.environ[k.strip()]=v.strip()

if __name__=="__main__":
    load_env()
    run()
