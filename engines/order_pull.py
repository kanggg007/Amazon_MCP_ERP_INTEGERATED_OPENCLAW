"""Daily order pull via SP-API Reports API — stores order data for revenue report."""
import os, httpx, json, time, psycopg2
from datetime import datetime, timedelta

DSN = os.environ.get("DATABASE_URL", "postgresql://postgres:ZTHtVHerPtatmfNeCufdSaqieNjmfxmW@acela.proxy.rlwy.net:58049/railway?sslmode=require")

STORES = {
    "CUCZUUS": ("amzn1.application-oa2-client.d8c93b864fef439886163fd321bb7cd2", "STORE_02_LWA_CLIENT_SECRET", "STORE_02_REFRESH_TOKEN_AMERICAS"),
    "BOOLUU":  ("amzn1.application-oa2-client.12b8db78749c4dd1b4235c4df915dfd0", "STORE_03_LWA_CLIENT_SECRET", "STORE_03_REFRESH_TOKEN_AMERICAS"),
    "Heliumx": ("amzn1.application-oa2-client.0d409ad3f84342eaace0e9d516f738e8", "STORE_04_LWA_CLIENT_SECRET", "STORE_04_REFRESH_TOKEN_AMERICAS"),
}

MARKETS = {"US":"ATVPDKIKX0DER","CA":"A2EUQ1WTGCTBG2"}
FE_MARKETS = {"AU":"A39IBJ37TRP1C6","JP":"A1VC38T7YXB528"}
EU_MARKETS = {"DE":"A1PA6795UKMFR9"}

FE_STORES = {
    "CUCZUUS":("amzn1.application-oa2-client.d8c93b864fef439886163fd321bb7cd2","STORE_02_LWA_CLIENT_SECRET","STORE_02_REFRESH_TOKEN_FE"),
    "BOOLUU":("amzn1.application-oa2-client.12b8db78749c4dd1b4235c4df915dfd0","STORE_03_LWA_CLIENT_SECRET","STORE_03_REFRESH_TOKEN_FE"),
    "Heliumx":("amzn1.application-oa2-client.0d409ad3f84342eaace0e9d516f738e8","STORE_04_LWA_CLIENT_SECRET","STORE_04_REFRESH_TOKEN_FE"),
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

def pull_orders(name, cid, csec_key, ref_key, mkt, mkt_id, date):
    csec = os.environ[csec_key]; ref = os.environ[ref_key]
    r = httpx.post("https://api.amazon.com/auth/o2/token",
        json={"grant_type":"refresh_token","client_id":cid,"client_secret":csec,"refresh_token":ref}, timeout=10)
    if r.status_code != 200: return f"Auth FAIL {r.status_code}"
    tk = r.json()["access_token"]
    
    host = "sellingpartnerapi-na.amazon.com"
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
            cur.execute("INSERT INTO orders_daily (store, market, date, data, order_count, revenue, shipped, pending, canceled) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (name, mkt, date, json.dumps(orders), len(orders), total, shipped, pending, cancelled))
            conn.commit(); conn.close()
            
            return f"{len(orders)} orders, ${total:.2f} ({shipped} shipped, {pending} pending, {cancelled} cancelled)"
        elif s == "CANCELLED": return "FAILED"
    return "Timeout"

def run():
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Order Pull (Reports API) — {yesterday}")
    t0 = time.time()
    results=[]
    # NA: US + CA for all 3 stores
    for name, (cid, csec_key, ref_key) in STORES.items():
        for mkt, mkt_id in MARKETS.items():
            result = pull_orders(name, cid, csec_key, ref_key, mkt, mkt_id, yesterday)
            results.append(f"{name}/{mkt}: {result}")
    # FE: AU + JP (BOOLUU has JP, Heliumx has AU)
    for name in ["CUCZUUS","BOOLUU"]:
        cid,csec_key,ref_key = FE_STORES[name]
        for mkt in ["AU","JP"]:
            result = pull_orders(name, cid, csec_key, ref_key, mkt, FE_MARKETS[mkt], yesterday)
            results.append(f"{name}/{mkt}: {result}")
    # Heliumx AU only (via FE)
    if "Heliumx" in FE_STORES:
        cid,csec_key,ref_key = FE_STORES["Heliumx"]
        result = pull_orders("Heliumx", cid, csec_key, ref_key, "AU", FE_MARKETS["AU"], yesterday)
        results.append(f"Heliumx/AU: {result}")
    # EU: DE
    for name in ["CUCZUUS","Heliumx"]:
        cid,csec_key,ref_key = EU_STORES[name]
        result = pull_orders(name, cid, csec_key, ref_key, "DE", EU_MARKETS["DE"], yesterday)
        results.append(f"{name}/DE: {result}")
    for r in results: print(f"  {r}")
    print(f"Done in {int(time.time()-t0)}s")

if __name__ == "__main__":
    load_env()
    init_db()
    run()
