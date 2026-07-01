"""Async ads ingestion — Task A (trigger) + Task B (collector). No blocking waits."""
import os, httpx, json, gzip, time, psycopg2, threading, re
from datetime import datetime, timedelta

DSN=os.environ.get("DATABASE_URL","postgresql://postgres:ZTHtVHerPtatmfNeCufdSaqieNjmfxmW@acela.proxy.rlwy.net:58049/railway?sslmode=require")

FX_USD={"US":1.0,"CA":0.7033,"AU":0.6892,"JP":0.00614,"DE":1.1403}
HOSTS={"na":"advertising-api.amazon.com","fe":"advertising-api-fe.amazon.com","eu":"advertising-api-eu.amazon.com"}

# Slim columns — no string metadata, just IDs and metrics
REPORT_TYPES={
    "sp_campaigns":{"reportTypeId":"spCampaigns","groupBy":["campaign"],"columns":"cost,campaignId,sales1d,clicks,impressions"},
    "sp_search_terms":{"reportTypeId":"spSearchTerm","groupBy":["searchTerm"],"columns":"cost,keywordId,searchTerm,clicks,impressions,sales1d"},
    "sp_targeting":{"reportTypeId":"spTargeting","groupBy":["targeting"],"columns":"keyword,matchType,cost,clicks,impressions,sales1d,campaignName"},
    "sp_advertised_product":{"reportTypeId":"spAdvertisedProduct","groupBy":["advertiser"],"columns":"cost,advertisedAsin,clicks,impressions,sales1d"},
    "sp_purchased_product":{"reportTypeId":"spPurchasedProduct","groupBy":["asin"],"columns":"purchasedAsin,advertisedAsin,advertisedSku,sales1d,sales7d,sales30d,purchases1d,purchases7d,purchases30d,campaignName"},
}

STORES={"CUCZUUS":"02","BOOLUU":"03","Heliumx":"04"}

PROFILES={
    "CUCZUUS":[("US","3465892858543553","na"),("CA","3474360124295876","na"),("AU","3457387109813217","fe"),("JP","1336746761611490","fe"),("DE","2148844609196157","eu")],
    "BOOLUU":[("US","2902488181372396","na"),("CA","1000058889542591","na"),("AU","671129043949754","fe"),("JP","1702417705815456","fe")],
    "Heliumx":[("US","1416052288010129","na"),("CA","3865316191299322","na"),("AU","422659196126799","fe"),("DE","4304939656717104","eu")],
}

def init_db():
    conn=psycopg2.connect(DSN);cur=conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS ads_reports (
        id SERIAL PRIMARY KEY, store VARCHAR(10), market VARCHAR(5),
        report_type VARCHAR(30), date DATE, report_id VARCHAR(50),
        status VARCHAR(12) DEFAULT 'PENDING', created_at TIMESTAMP DEFAULT NOW(),
        UNIQUE(store, market, report_type, date))""")
    conn.commit();conn.close()

# ═══════════════ TASK A: Trigger (fires report, saves ID, exits) ═══════════════

def trigger_report(store,snum,market,pid,region,rtype,cfg,yesterday):
    """Create report + save reportId. Returns immediately. No polling."""
    cid=os.environ[f"STORE_{snum}_ADS_CLIENT_ID"]
    csec=os.environ[f"STORE_{snum}_ADS_CLIENT_SECRET"]
    ref=os.environ[f"STORE_{snum}_ADS_REFRESH_TOKEN"]
    
    # Auth
    for a in range(3):
        r=httpx.post("https://api.amazon.com/auth/o2/token",
            json={"grant_type":"refresh_token","client_id":cid,"client_secret":csec,"refresh_token":ref},timeout=15)
        if r.status_code==200:break
        time.sleep(5*(2**a))
    else:return f"Auth FAIL"
    
    tk=r.json()["access_token"]
    host=HOSTS[region]
    h={"Authorization":f"Bearer {tk}","Amazon-Advertising-API-ClientId":cid,"Amazon-Advertising-API-Scope":pid,"Content-Type":"application/json"}
    
    body={"startDate":yesterday,"endDate":yesterday,"configuration":{
        "adProduct":"SPONSORED_PRODUCTS","groupBy":cfg["groupBy"],
        "columns":cfg["columns"].split(","),"reportTypeId":cfg["reportTypeId"],
        "timeUnit":"SUMMARY","format":"GZIP_JSON"}}
    
    rr=httpx.post(f"https://{host}/reporting/reports",headers=h,json=body,timeout=15)
    if rr.status_code==200:
        rid=rr.json()["reportId"]
    elif rr.status_code==425:
        msg=rr.json().get("detail","")
        m=re.search(r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})',msg)
        rid=m.group(1) if m else None
        if not rid:return f"425 no reportId"
    else:
        return f"Create FAIL {rr.status_code}"
    
    # Save report ID to DB
    conn=psycopg2.connect(DSN);cur=conn.cursor()
    cur.execute("DELETE FROM ads_reports WHERE store=%s AND market=%s AND report_type=%s AND date=%s",
        (store,market,rtype,yesterday))
    cur.execute("INSERT INTO ads_reports (store,market,report_type,date,report_id,status) VALUES (%s,%s,%s,%s,%s,'PENDING')",
        (store,market,rtype,yesterday,rid))
    conn.commit();conn.close()
    return f"Triggered {rid[:8]}"

def task_a_trigger(date=None):
    """Trigger all reports — fire + forget. No polling."""
    yesterday=date or (datetime.now()-timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Task A (Trigger) — {yesterday}")
    t0=time.time()
    results=[]
    for store in STORES:
        snum=STORES[store]
        for market,pid,region in PROFILES[store]:
            for rtype,cfg in REPORT_TYPES.items():
                r=trigger_report(store,snum,market,pid,region,rtype,cfg,yesterday)
                results.append(f"{store}/{market}/{rtype}: {r}")
                print(f"  {store}/{market}/{rtype}: {r}",flush=True)
    print(f"Task A done. {len(results)} reports triggered in {int(time.time()-t0)}s")

# ═══════════════ TASK B: Collector (polls pending, downloads completed) ═══════════════

def task_b_collect(date=None):
    """Poll all PENDING reports. Download completed ones."""
    yesterday=date or (datetime.now()-timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Task B (Collect) — {yesterday}")
    t0=time.time()
    
    conn=psycopg2.connect(DSN);cur=conn.cursor()
    cur.execute("SELECT store, market, report_type, date, report_id FROM ads_reports WHERE status IN ('PENDING','IN_PROGRESS') ORDER BY created_at")
    pending=cur.fetchall()
    conn.close()
    
    if not pending:
        print(f"  No pending reports")
        return
    
    print(f"  {len(pending)} pending reports to check")
    
    # Group by store to reuse auth tokens
    completed=0
    for store,market,rtype,rep_date,rid in pending:
        snum=STORES[store]
        cid=os.environ[f"STORE_{snum}_ADS_CLIENT_ID"]
        csec=os.environ[f"STORE_{snum}_ADS_CLIENT_SECRET"]
        ref=os.environ[f"STORE_{snum}_ADS_REFRESH_TOKEN"]
        
        # Find region for this market
        region=None
        for m,pid,r in PROFILES[store]:
            if m==market:region=r;profile_id=pid;break
        if not region:continue
        
        # Auth
        r=httpx.post("https://api.amazon.com/auth/o2/token",
            json={"grant_type":"refresh_token","client_id":cid,"client_secret":csec,"refresh_token":ref},timeout=15)
        if r.status_code!=200:continue
        tk=r.json()["access_token"]
        
        host=HOSTS[region]
        h={"Authorization":f"Bearer {tk}","Amazon-Advertising-API-ClientId":cid,"Amazon-Advertising-API-Scope":profile_id}
        
        # Check status
        rp=httpx.get(f"https://{host}/reporting/reports/{rid}",headers=h,timeout=10)
        s=rp.json().get("status","?")
        
        if s=="COMPLETED":
            try:
                raw=gzip.decompress(httpx.get(rp.json()["url"],timeout=60).content).decode()
                rows=json.loads(raw)
                if isinstance(rows,list) and len(rows)==1 and isinstance(rows[0],list):rows=rows[0]
                cost=sum(float(r2.get("cost",0)) for r2 in rows if isinstance(r2,dict))
                sales=sum(float(r2.get("sales1d",0)) for r2 in rows if isinstance(r2,dict))
                fx=FX_USD.get(market,1.0)
                cost_usd=round(cost*fx,2);sales_usd=round(sales*fx,2)
                
                conn=psycopg2.connect(DSN);cur=conn.cursor()
                cur.execute("DELETE FROM ads_daily WHERE store=%s AND market=%s AND report_type=%s AND date=%s",
                    (store,market,rtype,rep_date))
                cur.execute("INSERT INTO ads_daily (store,market,report_type,date,data,cost,sales) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (store,market,rtype,rep_date,json.dumps(rows),cost_usd,sales_usd))
                cur.execute("UPDATE ads_reports SET status='SUCCESS' WHERE report_id=%s",(rid,))
                conn.commit();conn.close()
                completed+=1
                print(f"  {store}/{market}/{rtype}: {len(rows)} rows, ${cost_usd:.2f} USD",flush=True)
            except Exception as e:
                print(f"  {store}/{market}/{rtype}: Download error: {e}",flush=True)
        elif s=="FAILURE":
            conn=psycopg2.connect(DSN);cur=conn.cursor()
            cur.execute("UPDATE ads_reports SET status='FAILED' WHERE report_id=%s",(rid,))
            conn.commit();conn.close()
            print(f"  {store}/{market}/{rtype}: FAILED",flush=True)
        elif s in ("PENDING","IN_PROGRESS","PROCESSING"):
            conn=psycopg2.connect(DSN);cur=conn.cursor()
            cur.execute("UPDATE ads_reports SET status='IN_PROGRESS' WHERE report_id=%s",(rid,))
            conn.commit();conn.close()
            # Don't print — too noisy for pending
    
    print(f"Task B done. {completed} completed in {int(time.time()-t0)}s")

if __name__=="__main__":
    import sys
    init_db()
    if len(sys.argv)>1 and sys.argv[1]=="collect":
        task_b_collect()
    else:
        task_a_trigger()
