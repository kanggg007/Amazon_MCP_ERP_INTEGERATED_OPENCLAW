"""Quick ingestion — all profiles in parallel across stores. Threaded for speed."""
import os,httpx,json,gzip,time,psycopg2,threading
from datetime import datetime,timedelta

DSN=os.environ.get("DATABASE_URL","postgresql://postgres:ZTHtVHerPtatmfNeCufdSaqieNjmfxmW@acela.proxy.rlwy.net:58049/railway?sslmode=require")
FX_USD={"USD":1.0,"CAD":0.2059,"AUD":0.2102,"JPY":0.0063,"EUR":0.95}
HOSTS={"na":"advertising-api.amazon.com","fe":"advertising-api-fe.amazon.com","eu":"advertising-api-eu.amazon.com"}
COLUMNS="cost,campaignName,campaignStatus,sales1d,clicks,impressions,campaignBudgetAmount,campaignBiddingStrategy"

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

def pull_one(store,snum,market,pid,region,yesterday):
    cid=os.environ[f"STORE_{snum}_ADS_CLIENT_ID"];csec=os.environ[f"STORE_{snum}_ADS_CLIENT_SECRET"];ref=os.environ[f"STORE_{snum}_ADS_REFRESH_TOKEN"]
    
    # Auth with retry
    for a in range(3):
        r=httpx.post("https://api.amazon.com/auth/o2/token",json={"grant_type":"refresh_token","client_id":cid,"client_secret":csec,"refresh_token":ref},timeout=10)
        if r.status_code==200:break
        time.sleep(5*(2**a))
    else:return f"{store}/{market}: Auth FAIL"
    
    tk=r.json()["access_token"]
    host=HOSTS[region]
    h={"Authorization":f"Bearer {tk}","Amazon-Advertising-API-ClientId":cid,"Amazon-Advertising-API-Scope":pid,"Content-Type":"application/json"}
    body={"startDate":yesterday,"endDate":yesterday,"configuration":{"adProduct":"SPONSORED_PRODUCTS","groupBy":["campaign"],"columns":COLUMNS.split(","),"reportTypeId":"spCampaigns","timeUnit":"SUMMARY","format":"GZIP_JSON"}}
    
    # Create report with rate-limit retry
    for a in range(5):
        rr=httpx.post(f"https://{host}/reporting/reports",headers=h,json=body,timeout=15)
        if rr.status_code==200:break
        if rr.status_code in (425,429):time.sleep(10*(2**a));continue
        return f"{store}/{market}: Create FAIL {rr.status_code}"
    else:return f"{store}/{market}: Rate limited 5x"
    
    rid=rr.json()["reportId"]
    max_polls=40 if region=="na" else 25  # NA can be slow
    for i in range(max_polls):
        time.sleep(6)
        rp=httpx.get(f"https://{host}/reporting/reports/{rid}",headers=h,timeout=10)
        s=rp.json().get("status","NO_KEY")
        if i%10==0 or s not in ("PENDING",):
            return f"{store}/{market}: [{i}] status={s}" # Debug: show status
            raw=gzip.decompress(httpx.get(rp.json()["url"],timeout=30).content).decode()
            rows=json.loads(raw)
            if isinstance(rows,list) and len(rows)==1 and isinstance(rows[0],list):rows=rows[0]
            cost=sum(float(r2.get("cost",0)) for r2 in rows if isinstance(r2,dict))
            sales=sum(float(r2.get("sales1d",0)) for r2 in rows if isinstance(r2,dict))
            fx=FX_USD.get(market,1.0) or FX_USD.get("USD",1.0)
            cost_usd=cost*fx;sales_usd=sales*fx
            conn=psycopg2.connect(DSN)
            cur=conn.cursor()
            cur.execute("INSERT INTO ads_daily (store,market,report_type,date,data,cost,sales) VALUES (%s,%s,%s,%s,%s,%s,%s)",(store,market,"sp_campaigns",yesterday,json.dumps(rows),cost_usd,sales_usd))
            conn.commit();conn.close()
            return f"{store}/{market}: {len(rows)} rows, $%.2f USD"%cost_usd
        elif s=="FAILURE":return f"{store}/{market}: Report FAILURE"
    return f"{store}/{market}: Timeout"

def pull_store(store,results):
    snum=STORES[store]
    yesterday=(datetime.now()-timedelta(days=1)).strftime("%Y-%m-%d")
    profiles=PROFILES[store]
    for market,pid,region in profiles:
        r=pull_one(store,snum,market,pid,region,yesterday)
        results.append(r)
        print(r,flush=True)
        time.sleep(2)

def run():
    yesterday=(datetime.now()-timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Ingestion (sequential) — {yesterday}")
    t0=time.time()
    results=[]
    for store in STORES:
        pull_store(store,results)
    print(f"Done. {len(results)} reports in {int(time.time()-t0)}s")

if __name__=="__main__":
    load_env()
    run()
