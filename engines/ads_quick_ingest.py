"""Quick ingestion — all profiles, sync, for today's test."""
import os,httpx,json,gzip,time,psycopg2
from datetime import datetime,timedelta

# Try loading from .env file (local), but Railway has vars in environment
env_path = os.path.join(os.path.dirname(__file__) or '.','..','.env')
if os.path.exists(env_path):
    for l in open(env_path).read().splitlines():
        if l and '=' in l and not l.startswith('#'):k,v=l.split('=',1);os.environ[k.strip()]=v.strip()

DSN=os.environ.get("DATABASE_URL","postgresql://postgres:ZTHtVHerPtatmfNeCufdSaqieNjmfxmW@acela.proxy.rlwy.net:58049/railway?sslmode=require")

# FX rates to USD
FX_USD={"USD":1.0,"CAD":0.2059,"AUD":0.2102,"JPY":0.0063,"EUR":0.95,"GBP":1.13,"MXN":0.05,"BRL":0.18}

PROFILES={
    "CUCZUUS":{("US","02","3465892858543553","na"),("CA","02","3474360124295876","na"),("AU","02","3457387109813217","fe"),("JP","02","1336746761611490","fe"),("DE","02","2148844609196157","eu")},
    "BOOLUU":{("US","03","2902488181372396","na"),("CA","03","1000058889542591","na"),("AU","03","671129043949754","fe"),("JP","03","1702417705815456","fe")},
    "Heliumx":{("US","04","1416052288010129","na"),("CA","04","3865316191299322","na"),("AU","04","422659196126799","fe"),("DE","04","4304939656717104","eu")},
}
HOSTS={"na":"advertising-api.amazon.com","fe":"advertising-api-fe.amazon.com","eu":"advertising-api-eu.amazon.com"}

COLUMNS="cost,campaignName,campaignStatus,sales1d,clicks,impressions,campaignBudgetAmount,campaignBiddingStrategy"

def run():
    yesterday=(datetime.now()-timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Ingestion — {yesterday}")
    t0=time.time()
    n=0
    
    for store,markets in PROFILES.items():
        for market,snum,pid,region in markets:
            cid=os.environ[f"STORE_{snum}_ADS_CLIENT_ID"];csec=os.environ[f"STORE_{snum}_ADS_CLIENT_SECRET"];ref=os.environ[f"STORE_{snum}_ADS_REFRESH_TOKEN"]
            # Get token with retry
            r=None
            for a in range(3):
                r=httpx.post("https://api.amazon.com/auth/o2/token",json={"grant_type":"refresh_token","client_id":cid,"client_secret":csec,"refresh_token":ref},timeout=10)
                if r.status_code==200:break
                time.sleep(5*(2**a))
            if not r or r.status_code!=200:
                print(f"  {store}/{market}: Auth FAIL")
                continue
            tk=r.json()["access_token"]
            host=HOSTS[region]
            h={"Authorization":f"Bearer {tk}","Amazon-Advertising-API-ClientId":cid,"Amazon-Advertising-API-Scope":pid,"Content-Type":"application/json"}
            body={"startDate":yesterday,"endDate":yesterday,"configuration":{"adProduct":"SPONSORED_PRODUCTS","groupBy":["campaign"],"columns":COLUMNS.split(","),"reportTypeId":"spCampaigns","timeUnit":"SUMMARY","format":"GZIP_JSON"}}
            # Create report with retry for rate limits
            rr=None
            for attempt in range(5):
                rr=httpx.post(f"https://{host}/reporting/reports",headers=h,json=body,timeout=15)
                if rr.status_code==200:break
                if rr.status_code in (425,429):
                    wait=10*(2**attempt)
                    print(f"  {store}/{market}: rate limited, waiting {wait}s...")
                    time.sleep(wait)
                    continue
                print(f"  {store}/{market}: FAIL {rr.status_code}")
                break
            if not rr or rr.status_code!=200:
                continue
            rid=rr.json()["reportId"]
            for i in range(25):
                time.sleep(5)
                rp=httpx.get(f"https://{host}/reporting/reports/{rid}",headers=h,timeout=10)
                s=rp.json()["status"]
                if s=="COMPLETED":
                    raw=gzip.decompress(httpx.get(rp.json()["url"],timeout=30).content).decode()
                    rows=json.loads(raw)
                    if isinstance(rows,list) and len(rows)==1 and isinstance(rows[0],list):rows=rows[0]
                    cost=sum(float(r2.get("cost",0)) for r2 in rows if isinstance(r2,dict))
                    sales=sum(float(r2.get("sales1d",0)) for r2 in rows if isinstance(r2,dict))
                    # Convert to USD (local currency from Ads API)
                    fx=FX_USD.get("USD",1.0)  # Default USD — per-market override below
                    if market=="CA":fx=FX_USD["CAD"]
                    elif market=="AU":fx=FX_USD["AUD"]
                    elif market=="JP":fx=FX_USD["JPY"]
                    elif market=="DE":fx=FX_USD["EUR"]
                    cost_usd=cost*fx;sales_usd=sales*fx
                    conn=psycopg2.connect(DSN)
                    cur=conn.cursor()
                    cur.execute("INSERT INTO ads_daily (store,market,report_type,date,data,cost,sales) VALUES (%s,%s,%s,%s,%s,%s,%s)",(store,market,"sp_campaigns",yesterday,json.dumps(rows),cost_usd,sales_usd))
                    conn.commit();conn.close()
                    n+=1
                    print(f"  {store}/{market}: {len(rows)} rows, ${cost:.2f}, {int(time.time()-t0)}s")
                    break
                elif s=="FAILURE":print(f"  {store}/{market}: FAILURE");break
    print(f"Done. {n} reports in {int(time.time()-t0)}s")

if __name__=="__main__":
    run()
