"""Daily order pull via SP-API Reports API — stores order data for revenue report."""
import os, httpx, json, time, psycopg2
from datetime import datetime, timedelta

DSN = os.environ.get("DATABASE_URL", "postgresql://postgres:ZTHtVHerPtatmfNeCufdSaqieNjmfxmW@acela.proxy.rlwy.net:58049/railway?sslmode=require")

STORES = {
    "CUCZUUS": ("amzn1.application-oa2-client.d8c93b864fef439886163fd321bb7cd2", "STORE_02_LWA_CLIENT_SECRET", "STORE_02_REFRESH_TOKEN_AMERICAS"),
    "BOOLUU":  ("amzn1.application-oa2-client.12b8db78749c4dd1b4235c4df915dfd0", "STORE_03_LWA_CLIENT_SECRET", "STORE_03_REFRESH_TOKEN_AMERICAS"),
    "Heliumx": ("amzn1.application-oa2-client.0d409ad3f84342eaace0e9d516f738e8", "STORE_04_LWA_CLIENT_SECRET", "STORE_04_REFRESH_TOKEN_AMERICAS"),
}

MARKETS = {"US":"ATVPDKIKX0DER"}  # CA duplicates US — skip
FE_MARKETS = {"AU":"A39IBJ37TRP1C6","JP":"A1VC38T7YXB528"}
EU_MARKETS = {"DE":"A1PA6795UKMFR9"}

FE_STORES = {
    "CUCZUUS":{"AU":("amzn1.application-oa2-client.d8c93b864fef439886163fd321bb7cd2","STORE_02_LWA_CLIENT_SECRET","STORE_02_REFRESH_TOKEN_AU"),"JP":("amzn1.application-oa2-client.d8c93b864fef439886163fd321bb7cd2","STORE_02_LWA_CLIENT_SECRET","STORE_02_REFRESH_TOKEN_JP")},
    "BOOLUU":{"AU":("amzn1.application-oa2-client.12b8db78749c4dd1b4235c4df915dfd0","STORE_03_LWA_CLIENT_SECRET","STORE_03_REFRESH_TOKEN_AU"),"JP":("amzn1.application-oa2-client.12b8db78749c4dd1b4235c4df915dfd0","STORE_03_LWA_CLIENT_SECRET","STORE_03_REFRESH_TOKEN_JP")},
}
EU_STORES = {
    "CUCZUUS":("amzn1.application-oa2-client.d8c93b864fef439886163fd321bb7cd2","STORE_02_LWA_CLIENT_SECRET","STORE_02_REFRESH_TOKEN_EU"),
    "Heliumx":("amzn1.application-oa2-client.0d409ad3f84342eaace0e9d516f738e8","STORE_04_LWA_CLIENT_SECRET","STORE_04_REFRESH_TOKEN_EU"),
}

def load_env():
    env_path=os.path.join(os.path.dirname(__file__) or '.','..','.env')
    if not os.path.exists(env_path):
        print("No .env file, using system env vars")
        return
    for line in open(env_path).read().splitlines():
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

def init_db():
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS orders_daily (
        id SERIAL PRIMARY KEY, store VARCHAR(10), market VARCHAR(5),
        date DATE, data JSONB, order_count INT, revenue NUMERIC,
        shipped INT, pending INT, canceled INT, pulled_at TIMESTAMP DEFAULT NOW())""")
    conn.commit()
    conn.close()

SP_HOSTS={"US":"sellingpartnerapi-na.amazon.com","CA":"sellingpartnerapi-na.amazon.com","AU":"sellingpartnerapi-fe.amazon.com","JP":"sellingpartnerapi-fe.amazon.com","DE":"sellingpartnerapi-eu.amazon.com"}

def pull_orders(name, cid, csec_key, ref_key, mkt, mkt_id, date):
    csec = os.environ[csec_key]; ref = os.environ[ref_key]
    r = httpx.post("https://api.amazon.com/auth/o2/token",
        json={"grant_type":"refresh_token","client_id":cid,"client_secret":csec,"refresh_token":ref}, timeout=10)
    if r.status_code != 200: return f"Auth FAIL {r.status_code}"
    tk = r.json()["access_token"]
    
    host = SP_HOSTS.get(mkt, "sellingpartnerapi-na.amazon.com")
    h = {"x-amz-access-token": tk, "Content-Type": "application/json"}
    
    # Create Reports API order report
    body = {
        "reportType": "GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL",
        "dataStartTime": f"{date}T00:00:00Z",
        "dataEndTime": f"{date}T23:59:59Z",
        "marketplaceIds": [mkt_id]
    }
    
    rr = httpx.post(f"https://{host}/reports/2021-06-30/reports", headers=h, json=body, timeout=15)
    if rr.status_code not in (200, 202): return f"Create FAIL {rr.status_code}"
    rid = rr.json()["reportId"]
    
    for i in range(30):
        time.sleep(10)
        rp = httpx.get(f"https://{host}/reports/2021-06-30/reports/{rid}", headers=h, timeout=10)
        s = rp.json().get("processingStatus", "?")
        if s == "DONE":
            did = rp.json().get("reportDocumentId", "")
            doc = httpx.get(f"https://{host}/reports/2021-06-30/documents/{did}", headers=h, timeout=15).json()
            data = httpx.get(doc.get("url", ""), timeout=30).text
            
            lines = [l.strip() for l in data.split("\n") if l.strip()]
            header = lines[0].split("\t")
            oi = header.index("amazon-order-id"); pi = header.index("item-price")
            qi = header.index("quantity"); si = header.index("order-status")
            
            total = 0; orders = {}; cancelled = 0
            for line in lines[1:]:
                cols = line.split("\t")
                if len(cols) < 16: continue
                oid = cols[oi]; status = cols[si]
                try: qty = int(cols[qi])
                except: qty = 1
                try: price = float(cols[pi]) if cols[pi] else 0
                except: price = 0
                lt = price * qty; total += lt
                if oid not in orders: orders[oid] = {"items": 0, "total": 0, "status": status}
                orders[oid]["items"] += 1; orders[oid]["total"] += lt
                if "Cancel" in status: cancelled += 1
            
            shipped = sum(1 for o in orders.values() if o["status"] == "Shipped")
            pending = len(orders) - shipped - cancelled
            
            conn = psycopg2.connect(DSN)
            cur = conn.cursor()
            # Dedup: delete old, insert new
            cur.execute("DELETE FROM orders_daily WHERE store=%s AND market=%s AND date=%s",(name,mkt,date))
            cur.execute("INSERT INTO orders_daily (store, market, date, data, order_count, revenue, shipped, pending, canceled) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (name, mkt, date, json.dumps(orders), len(orders), total, shipped, pending, cancelled))
            conn.commit(); conn.close()
            
            return f"{len(orders)} orders, ${total:.2f} ({shipped} shipped, {pending} pending, {cancelled} cancelled)"
        elif s == "CANCELLED": return "FAILED"
    return "Timeout"

def run():
    import threading
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Order Pull (parallel) — {yesterday}")
    t0 = time.time()
    results=[];threads=[]
    def _go(name, cid, csec_key, ref_key, mkt, mkt_id):
        r=pull_orders(name, cid, csec_key, ref_key, mkt, mkt_id, yesterday)
        results.append(f"{name}/{mkt}: {r}");print(f"  {name}/{mkt}: {r}",flush=True)
    # NA: US+CA all 3 stores = 6 threads
    for name, (cid, csec_key, ref_key) in STORES.items():
        for mkt, mkt_id in MARKETS.items():
            t=threading.Thread(target=_go,args=(name,cid,csec_key,ref_key,mkt,mkt_id));t.start();threads.append(t)
    # FE: AU+JP for CUCZUUS+BOOLUU = 4 threads
    for name, mkts in FE_STORES.items():
        for mkt, (cid, csec_key, ref_key) in mkts.items():
            t=threading.Thread(target=_go,args=(name,cid,csec_key,ref_key,mkt,FE_MARKETS[mkt]));t.start();threads.append(t)
    # EU: DE for CUCZUUS+Heliumx = 2 threads
    for name, (cid, csec_key, ref_key) in EU_STORES.items():
        t=threading.Thread(target=_go,args=(name,cid,csec_key,ref_key,"DE",EU_MARKETS["DE"]));t.start();threads.append(t)
    for t in threads:t.join()
    print(f"Done. {len(results)} markets in {int(time.time()-t0)}s")

if __name__ == "__main__":
    load_env()
    init_db()
    run()
