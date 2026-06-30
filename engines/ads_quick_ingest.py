"""Quick ingestion — all profiles in parallel across stores. Threaded for speed."""
import os,httpx,json,gzip,time,psycopg2,threading
from datetime import datetime,timedelta

DSN=os.environ.get("DATABASE_URL","postgresql://postgres:ZTHtVHerPtatmfNeCufdSaqieNjmfxmW@acela.proxy.rlwy.net:58049/railway?sslmode=require")
FX_USD={"USD":1.0,"CAD":0.2059,"AUD":0.2102,"JPY":0.0063,"EUR":0.95}
HOSTS={"na":"advertising-api.amazon.com","fe":"advertising-api-fe.amazon.com","eu":"advertising-api-eu.amazon.com"}
COLUMNS="cost,campaignName,campaignStatus,sales1d,clicks,impressions,campaignBudgetAmount,campaignBiddingStrategy"

REPORT_TYPES={
    "sp_campaigns":{"reportTypeId":"spCampaigns","groupBy":["campaign"],"columns":"cost,campaignName,campaignStatus,sales1d,clicks,impressions,campaignBudgetAmount,campaignBiddingStrategy"},
    "sp_search_terms":{"reportTypeId":"spSearchTerm","groupBy":["searchTerm"],"columns":"searchTerm,keyword,matchType,cost,clicks,impressions,sales1d,campaignName"},
    "sp_targeting":{"reportTypeId":"spTargeting","groupBy":["targeting"],"columns":"keyword,matchType,cost,clicks,impressions,sales1d,campaignName"},
    "sp_advertised_product":{"reportTypeId":"spAdvertisedProduct","groupBy":["advertiser"],"columns":"advertisedAsin,advertisedSku,cost,clicks,impressions,sales1d"},
    "sp_purchased_product":{"reportTypeId":"spPurchasedProduct","groupBy":["asin"],"columns":"purchasedAsin,advertisedAsin,advertisedSku,sales1d,sales7d,sales30d,purchases1d,purchases7d,purchases30d,campaignName"},
}

STORES={
    "CUCZUUS":"02","BOOLUU":"03","Heliumx":"04"
}

PROFILES={
    "CUCZUUS":[("US","3465892858543553","na"),("CA","3474360124295876","na"),("AU","3457387109813217","fe"),("JP","1336746761611490","fe"),("DE","2148844609196157","eu")],
    "BOOLUU":[("US","2902488181372396","na"),("CA","1000058889542591","na"),("AU","671129043949754","fe"),("JP","1702417705815456","fe")],
    "Heliumx":[("US","1416052288010129","na"),("CA","3865316191299322","na"),("AU","422659196126799","fe"),("DE","4304939656717104","eu")],
}

def load_env():
    env_path=os.path.join(os.path.dirname(__file__) or '.','..','.env')
    if os.path.exists(env_path):
        for l in open(env_path).read().splitlines():
            if l and '=' in l and not l.startswith('#'):k,v=l.split('=',1);os.environ[k.strip()]=v.strip()

def pull_one(store,snum,market,pid,region,rtype,rtype_cfg,yesterday):
    cid=os.environ[f"STORE_{snum}_ADS_CLIENT_ID"];csec=os.environ[f"STORE_{snum}_ADS_CLIENT_SECRET"];ref=os.environ[f"STORE_{snum}_ADS_REFRESH_TOKEN"]
    
    # Auth with retry
    for a in range(3):
        r=httpx.post("https://api.amazon.com/auth/o2/token",json={"grant_type":"refresh_token","client_id":cid,"client_secret":csec,"refresh_token":ref},timeout=10)
        if r.status_code==200:break
        time.sleep(5*(2**a))
    else:return f"{store}/{market}/{rtype}: Auth FAIL"
    
    tk=r.json()["access_token"]
    host=HOSTS[region]
    h={"Authorization":f"Bearer {tk}","Amazon-Advertising-API-ClientId":cid,"Amazon-Advertising-API-Scope":pid,"Content-Type":"application/json"}
    cols=rtype_cfg["columns"]
    body={"startDate":yesterday,"endDate":yesterday,"configuration":{"adProduct":"SPONSORED_PRODUCTS","groupBy":rtype_cfg["groupBy"],"columns":cols.split(","),"reportTypeId":rtype_cfg["reportTypeId"],"timeUnit":"SUMMARY","format":"GZIP_JSON"}}
    
    # Create report with rate-limit retry
    for a in range(5):
        rr=httpx.post(f"https://{host}/reporting/reports",headers=h,json=body,timeout=15)
        if rr.status_code==200:break
        if rr.status_code==429:time.sleep(10*(2**a));continue
        if rr.status_code==425:
            # Duplicate report exists — use its ID
            rid=rr.json().get("reportId") if rr.text else None
            if rid:break
            time.sleep(10*(2**a));continue
        return f"{store}/{market}/{rtype}: Create FAIL {rr.status_code}"
    else:return f"{store}/{market}/{rtype}: Rate limited 5x"
    
    rid=rr.json()["reportId"]
    max_polls=100 if region=="na" or region=="eu" else 25  # NA can be slow
    for i in range(max_polls):
        time.sleep(6)
        rp=httpx.get(f"https://{host}/reporting/reports/{rid}",headers=h,timeout=10)
        s=rp.json().get("status","NO_KEY")
        if i==0 or i%10==0:
            print(f"  [{store}/{market}] poll {i}: {s}")
        if s=="COMPLETED":
            raw=gzip.decompress(httpx.get(rp.json()["url"],timeout=30).content).decode()
            rows=json.loads(raw)
            if isinstance(rows,list) and len(rows)==1 and isinstance(rows[0],list):rows=rows[0]
            cost=sum(float(r2.get("cost",0)) for r2 in rows if isinstance(r2,dict))
            sales=sum(float(r2.get("sales1d",0)) for r2 in rows if isinstance(r2,dict))
            fx=FX_USD.get(market,1.0) or FX_USD.get("USD",1.0)
            cost_usd=cost*fx;sales_usd=sales*fx
            conn=psycopg2.connect(DSN)
            cur=conn.cursor()
            cur.execute("INSERT INTO ads_daily (store,market,report_type,date,data,cost,sales) VALUES (%s,%s,%s,%s,%s,%s,%s)",(store,market,rtype,yesterday,json.dumps(rows),cost_usd,sales_usd))
            conn.commit();conn.close()
            return f"{store}/{market}/{rtype}: {len(rows)} rows, $%.2f USD"%cost_usd
        elif s=="FAILURE":return f"{store}/{market}: Report FAILURE"
    return f"{store}/{market}: Timeout"

def pull_profile(store,snum,market,pid,region,yesterday,results):
    """Pull all report types for one profile. FE: parallel, NA/EU: sequential."""
    if region=="fe":
        # FE is fast — parallel
        threads=[]
        def _pull_type(rtype,cfg):
            r=pull_one(store,snum,market,pid,region,rtype,cfg,yesterday)
            results.append(r)
            print(r,flush=True)
        for rtype,cfg in REPORT_TYPES.items():
            t=threading.Thread(target=_pull_type,args=(rtype,cfg))
            t.start()
            threads.append(t)
        for t in threads:t.join()
    else:
        # NA/EU needs patience — sequential types
        for rtype,cfg in REPORT_TYPES.items():
            r=pull_one(store,snum,market,pid,region,rtype,cfg,yesterday)
            results.append(r)
            print(r,flush=True)

def run():
    yesterday=(datetime.now()-timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Ingestion (FE parallel, NA/EU sequential types) — {yesterday}")
    t0=time.time()
    results=[]
    threads=[]
    for store in STORES:
        snum=STORES[store]
        for market,pid,region in PROFILES[store]:
            t=threading.Thread(target=pull_profile,args=(store,snum,market,pid,region,yesterday,results))
            t.start()
            threads.append(t)
    for t in threads:t.join()
    print(f"Done. {len(results)} reports in {int(time.time()-t0)}s")

if __name__=="__main__":
    load_env()
    run()
