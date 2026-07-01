"""
FastAPI Application — Amazon Operations Intelligence Platform
==============================================================
OpenClaw command center and API layer.
Includes RBAC: Master Admin and Sub Admin permission control.
"""

import logging
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from api.query_engine import QueryEngine
from api.approval_workflow import ApprovalWorkflow
from engines.inventory_engine import InventoryEngine
from engines.discrepancy_engine import DiscrepancyEngine
from engines.ads_engine import AdsEngine
from engines.voc_engine import VoCEngine
from engines.return_refund_engine import ReturnRefundEngine
from engines.profit_leakage_engine import ProfitLeakageEngine
from engines.inventory_autopilot_engine import InventoryAutopilotEngine
from engines.cost_engine import CostEngine
from engines.product_catalog_engine import ProductCatalogEngine
from engines.ccis_engine import CCISEngine
from engines.reimbursement_engine import ReimbursementEngine
from api.direct_finances import router as direct_finances_router
from db.connection import get_session
# lazy import inside route handlers
from auth import get_store_registry
from auth.rbac import AdminRole, RBACManager, AdminUser

logger = logging.getLogger("amazon.api")

# Router
from api.direct_finances import router as direct_finances_router
app = FastAPI(
    title="Amazon Operations Intelligence Platform",
    version="1.0.0",
    description="Multi-store Amazon FBA management with RBAC.",
)
app.include_router(direct_finances_router)


# ─── Schemas ───

class QueryRequest(BaseModel):
    user_id: str = "master"
    store_id: str
    query: str

class QueryResponse(BaseModel):
    status: str = "ok"
    data: Dict

class ActionRequest(BaseModel):
    user_id: str = "master"
    store_id: str
    action_type: str
    message: str
    confidence: float = 0.75
    metadata: Optional[Dict] = None

class ActionResponse(BaseModel):
    status: str
    data: Dict

class ApproveRequest(BaseModel):
    store_id: str
    action_id: str
    approved: bool
    notes: Optional[str] = None
    actioned_by: str = "master"

class CreateUserRequest(BaseModel):
    user_id: str
    username: str
    assigned_stores: List[str] = Field(default_factory=list)
    created_by: str = "master"


# ─── Dependencies ───

def get_db():
    db = get_session()
    try:
        yield db
    finally:
        db.close()


def require_store_access(user_id: str, store_id: str, action: str = "read"):
    """Validate user has access to the given store."""
    registry = get_store_registry()
    if not registry.is_valid_store(store_id):
        raise HTTPException(
            status_code=404,
            detail=f"Store '{store_id}' not found. Available: {list(registry.active_stores.keys())}",
        )
    # Simple RBAC: Master has full access
    rbac = RBACManager()
    if action == "read":
        if not rbac.check_read_access(user_id, store_id):
            raise HTTPException(status_code=403, detail=f"User '{user_id}' cannot read store '{store_id}'")
    elif action == "write":
        if not rbac.check_write_access(user_id, store_id):
            raise HTTPException(status_code=403, detail=f"User '{user_id}' cannot write to store '{store_id}'")


# ─── Routes ───

@app.get("/")
async def root():
    from auth import get_store_registry
    try:
        stores = list(get_store_registry().active_stores.keys())
    except:
        stores = []
    return {
        "service": "Amazon Operations Intelligence Platform",
        "version": "1.0.0",
        "stores": stores,
        "endpoints": [
            "POST /query — Natural language query",
            "POST /action/propose — Propose action",
            "POST /action/approve — Approve/reject action",
            "GET /actions/pending — List pending approvals",
            "POST /users/create — Create sub-admin (Master only)",
            "GET /users — List all users (Master only)",
            "DELETE /users/{user_id} — Deactivate user (Master only)",
            "GET /dashboard/{store_id} — Store dashboard",
            "POST /finances/refunds — Fetch refunds from Finances API",
            "POST /finances/reconcile — Reconcile loss events",
            "GET /finances/loss/{store_id} — Profit leakage analysis",
            "GET /finances/summary/{store_id} — Return/refund summary",
            "GET /pld/leakage/{store_id} — Profit leakage by SKU",
            "GET /pld/sku/{store_id}/{sku} — True profit for one SKU",
            "GET /pld/scan/{store_id} — Scan all SKUs (min_loss filter)",
            "GET /pld/patterns/{store_id}/{sku} — AI pattern detection",
            "POST /pld/refresh-fba-rate/{store_id} — Refresh FBA fee rate per SKU",
            "GET /pld/daily-profit/{store_id} — Daily estimated P&L with settlement correction",
            "POST /rds/scan — Scan for reimbursement opportunities",
            "GET /rds/queue/{store_id} — Recovery queue",
            "GET /rds/summary/{store_id} — Total recoverable value",
            "GET /rds/evidence/{store_id}/{opp_id} — Evidence pack",
            "POST /rds/file/{opp_id} — Mark opportunity as filed",
            "GET /voc/dashboard/{store_id} — VoC intelligence dashboard",
            "GET /voc/profit-impact/{store_id} — Complaints → profit correlation",
            "GET /voc/trend/{store_id} — Monthly complaint trends",
            "GET /voc/by-source/{store_id} — Feedback sources ranked by tier",
            "POST /ccis/process — Classify + draft + approve buyer message",
            "GET /ccis/dashboard/{store_id} — CCIS intelligence dashboard",
            "GET /ccis/classify — Quick message classification",
            "POST /ccis/playbook — Create support playbook template",        ],
    }


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


@app.get("/scheduler-status")
async def scheduler_status():
    """Check if scheduler is alive and what time it thinks it is."""
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    cst = _dt.now(_tz(_td(hours=8)))
    return {"scheduler_time_cst": cst.strftime("%Y-%m-%d %H:%M:%S"), "utc": _dt.now(_tz.utc).strftime("%H:%M")}


@app.get("/test/ads-poll")
async def test_ads_poll():
    """Clean single test: CUCZUUS US sp_campaigns with marketplace filter"""
    import os as _os, httpx, time, asyncio, gzip, json
    cid=_os.environ['STORE_02_ADS_CLIENT_ID'];csec=_os.environ['STORE_02_ADS_CLIENT_SECRET'];ref=_os.environ['STORE_02_ADS_REFRESH_TOKEN']
    r=httpx.post('https://api.amazon.com/auth/o2/token',json={'grant_type':'refresh_token','client_id':cid,'client_secret':csec,'refresh_token':ref},timeout=15)
    tk=r.json()['access_token']
    pid='3465892858543553';host='advertising-api.amazon.com'
    h={'Authorization':'Bearer '+tk,'Amazon-Advertising-API-ClientId':cid,'Amazon-Advertising-API-Scope':pid,'Content-Type':'application/json'}
    
    cols='cost,campaignName,campaignStatus,sales1d,clicks,impressions,campaignBudgetAmount,campaignBiddingStrategy'
    body={'startDate':'2026-06-29','endDate':'2026-06-29','configuration':{'adProduct':'SPONSORED_PRODUCTS','groupBy':['campaign'],'columns':cols.split(','),'reportTypeId':'spCampaigns','timeUnit':'SUMMARY','format':'GZIP_JSON'}}
    
    # Create report
    rr=httpx.post(f'https://{host}/reporting/reports',headers=h,json=body,timeout=15)
    sc=rr.status_code
    result={'create_status':sc}
    if sc==425:
        result['note']='425 duplicate'
        # Extract reportId from detail message
        try:
            import re
            msg=rr.json().get('detail','')
            m=re.search(r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})',msg)
            rid=m.group(1) if m else None
        except:rid=None
        if not rid:result['error']='425 no reportId';return result
    elif sc!=200:
        result['error']=rr.text[:300];return result
    else:
        rid=rr.json().get('reportId')
    if not rid:result['error']='no reportId';return result
    result['report_id']=rid
    
    # Exponential backoff polling: 6,12,24,48,96,120...
    interval=3;polls=[]
    for i in range(15):
        await asyncio.sleep(interval)
        rp=httpx.get(f'https://{host}/reporting/reports/{rid}',headers=h,timeout=10)
        d=rp.json()
        s=d.get('status','?')
        polls.append({'poll':i,'status':s,'interval':interval})
        if s=='COMPLETED':
            url=d['url']
            raw=gzip.decompress(httpx.get(url,timeout=60).content).decode()
            data=json.loads(raw)
            rows=data if isinstance(data,list) else (data[0] if isinstance(data[0],list) else [])
            # Show marketplace breakdown
            markets={}
            for r2 in rows:
                if isinstance(r2,dict):
                    mid=r2.get('campaignMarketplaceId','unknown')
                    markets[mid]=markets.get(mid,0)+1
            result['completed']=True;result['total_rows']=len(rows);result['markets']=markets;result['polls']=polls
            # Filter US only
            us_rows=[r2 for r2 in rows if isinstance(r2,dict) and r2.get('campaignMarketplaceId')=='ATVPDKIKX0DER']
            result['us_rows']=len(us_rows);result['us_cost']=sum(float(r2.get('cost',0)) for r2 in us_rows)
            return result
        elif s=='FAILURE':
            result['failed']=True;result['polls']=polls;return result
        interval=min(interval*2,120)
    result['timeout']=True;result['polls']=polls;return result


@app.get("/debug/ingest-log")
async def ingest_log():
    """Read the ingest log file."""
    try:
        with open("/tmp/ingest.log") as f:
            return {"log": f.read()[-2000:]}
    except:
        return {"log": "No log yet"}


@app.get("/admin/cleanup-ads")
async def cleanup_ads():
    """Remove duplicate ad rows, keep latest per (store,market,type,date)."""
    import psycopg2, os as _os
    dsn=_os.environ.get("DATABASE_URL","")
    conn=psycopg2.connect(dsn);cur=conn.cursor()
    cur.execute("SELECT COUNT(*) FROM ads_daily")
    before=cur.fetchone()[0]
    cur.execute("""DELETE FROM ads_daily WHERE id NOT IN (
        SELECT MAX(id) FROM ads_daily GROUP BY store, market, report_type, date
    )""")
    deleted=cur.rowcount;conn.commit()
    cur.execute("SELECT COUNT(*) FROM ads_daily")
    after=cur.fetchone()[0];conn.close()
    return {"status":"ok","before":before,"deleted":deleted,"after":after}

@app.get("/admin/db-check")
async def db_check(store:str="CUCZUUS",market:str="US",rtype:str="sp_campaigns",date:str="2026-06-29"):
    """Direct DB check for specific row."""
    import psycopg2, os as _os
    dsn=_os.environ.get("DATABASE_URL","")
    conn=psycopg2.connect(dsn)
    cur=conn.cursor()
    cur.execute("SELECT * FROM ads_daily WHERE store=%s AND market=%s AND report_type=%s AND date=%s",(store,market,rtype,date))
    rows=cur.fetchall()
    # Also count total by market
    cur.execute("SELECT market, COUNT(*) FROM ads_daily WHERE date=%s GROUP BY market",(date,))
    by_market=dict(cur.fetchall())
    cur.execute("SELECT date, market, COUNT(*) FROM ads_daily GROUP BY date, market ORDER BY date, market")
    all_markets=cur.fetchall()
    conn.close()
    return {"query":{"store":store,"market":market,"type":rtype,"date":date},"found":len(rows),"by_market_for_date":by_market,"all_dates_markets":[[str(d),m,c] for d,m,c in all_markets]}

@app.get("/admin/data-status")
async def data_status():
    """Check data + scheduler health."""
    try:
        import psycopg2, os as _os
        from datetime import datetime as _dt
        dsn = _os.environ.get("DATABASE_URL", "")
        conn = psycopg2.connect(dsn)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM ads_daily")
        total=cur.fetchone()[0]
        # Count by market
        cur.execute("SELECT market, COUNT(*) FROM ads_daily GROUP BY market ORDER BY market")
        by_market=dict(cur.fetchall())
        # Count by date
        cur.execute("SELECT date, COUNT(*) FROM ads_daily GROUP BY date ORDER BY date")
        by_date=dict(cur.fetchall())
        cur.execute("SELECT store, market, report_type, date, cost, sales, data, pulled_at FROM ads_daily ORDER BY pulled_at DESC LIMIT 500")
        rows = []
        for r in cur.fetchall():
            rows.append({"store":r[0],"market":r[1],"type":r[2],"date":str(r[3]),"cost":float(r[4]),"sales":float(r[5]),"data":r[6] if r[6] else "[]","pulled":str(r[7])})
        conn.close()
        return {"status":"ok","db_total":total,"by_market":by_market,"by_date":{str(k):v for k,v in by_date.items()},"shown":len(rows),"rows":rows[:100],"app_uptime": str(_dt.now())}
    except Exception as e:
        return {"status":"error","message":str(e)[:200]}


@app.post("/admin/run-ingestion")
async def run_ingestion(user_id: str = "master", force: str = "0"):
    """Trigger ingestion. force=1 to skip DB dedup and re-pull all."""
    import importlib, sys as _sys2, asyncio, threading
    from pathlib import Path as _Path
    BASE3 = _Path(__file__).parent.parent
    _sys2.path.insert(0, str(BASE3))
    
    def _run():
        import engines.ads_quick_ingest as mod
        if force == "1":
            # Clear DB for yesterday to force fresh pull
            import psycopg2, os as _os
            from datetime import datetime, timezone, timedelta
            yesterday=(datetime.now(timezone(timedelta(hours=8)))-timedelta(days=1)).strftime("%Y-%m-%d")
            dsn=_os.environ.get("DATABASE_URL","")
            if dsn:
                conn=psycopg2.connect(dsn);cur=conn.cursor()
                cur.execute("DELETE FROM ads_daily WHERE date=%s",(yesterday,))
                conn.commit();conn.close()
                print(f"[INGEST] Force: cleared {cur.rowcount} rows for {yesterday}",flush=True)
        mod.run()
        print("[INGEST] Complete",flush=True)
    threading.Thread(target=_run,daemon=True).start()
    return {"status":"started","force":force=="1","message":"Ingestion running"}


@app.post("/admin/run-orders")
async def run_orders(user_id: str = "master"):
    """Pull orders from SP-API Reports API."""
    import asyncio, threading, sys as _sys2
    from pathlib import Path as _Path
    BASE3 = _Path(__file__).parent.parent
    print(f"[ORDERS] Starting SP-API order pull", flush=True)
    def _run():
        try:
            _sys2.path.insert(0, str(BASE3))
            from engines.order_pull import load_env, init_db, run
            load_env();init_db();run()
            print(f"[ORDERS] Complete", flush=True)
        except Exception as e:
            print(f"[ORDERS] ERROR: {e}", flush=True)
            import traceback;traceback.print_exc()
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started", "message": "Order pull running"}

@app.post("/admin/run-revenue")
async def run_revenue(user_id: str = "master", date: str = None):
    """Revenue report combining ads (DB) + orders (DB) in USD."""
    import asyncio, threading, json as _json
    from datetime import datetime, timedelta, timezone
    
    if date is None:
        date = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=1)).strftime("%Y-%m-%d")
    
    FX = {"USD":1.0,"CAD":0.2059,"AUD":0.2102,"JPY":0.0063,"EUR":0.95}
    
    def _run():
        import psycopg2, os as _os
        dsn = _os.environ.get("DATABASE_URL","")
        conn = psycopg2.connect(dsn)
        cur = conn.cursor()
        
        # Ads spend
        cur.execute("SELECT store, market, report_type, cost, sales, data FROM ads_daily WHERE date = %s AND report_type = 'sp_campaigns'",(date,))
        ads = cur.fetchall()
        
        # Orders
        cur.execute("SELECT store, market, order_count, revenue, shipped, pending FROM orders_daily WHERE date = %s",(date,))
        orders = cur.fetchall()
        conn.close()
        
        # Combine
        report = {}
        for s,m,rt,cost,sales,data in ads:
            key = f"{s}/{m}"
            if key not in report: report[key] = {"ads_cost":0,"ads_sales":0,"orders":0,"order_rev":0,"order_rev_usd":0}
            report[key]["ads_cost"] += float(cost or 0)
            report[key]["ads_sales"] += float(sales or 0)
        for s,m,oc,rev,shipped,pending in orders:
            key = f"{s}/{m}"
            if key not in report: report[key] = {"ads_cost":0,"ads_sales":0,"orders":0,"order_rev":0,"order_rev_usd":0}
            fx = FX.get(m,1.0)
            report[key]["orders"] += oc or 0
            report[key]["order_rev"] += float(rev or 0)
            report[key]["order_rev_usd"] += float(rev or 0) * fx
        
        print(f"Revenue Report — {date}", flush=True)
        total_ads = total_order_count = 0
        for key in sorted(report.keys()):
            d = report[key]
            store, mkt = key.split("/")
            acos = d['ads_sales'] and (d['ads_cost']/d['ads_sales']*100) or 0
            cur = {"US":"USD","CA":"CAD","AU":"AUD","JP":"JPY","DE":"EUR"}.get(mkt,"?")
            print(f"  {key}: Ads ${d['ads_cost']:.2f} USD | Orders {d['orders']} = {cur}{d['order_rev']:,.0f} | ACOS {acos:.0f}%", flush=True)
            total_ads += d['ads_cost']
            total_order_count += d['orders']
        print(f"  TOTAL: Ads ${total_ads:,.2f} USD | {total_order_count} orders", flush=True)
        print(f"[REVENUE] Complete", flush=True)
    
    threading.Thread(target=_run, daemon=True).start()
    return {"status":"started","date":date,"message":"Revenue report running"}


async def _startup_ingest():
    """Run ingestion on startup if not done today."""
    import asyncio
    print("[STARTUP-INGEST] Waiting 30s...", flush=True)
    await asyncio.sleep(30)  # Let DB and auth initialize
    from datetime import datetime, timezone, timedelta
    from pathlib import Path as _Path
    import os as _os, sys as _sys
    
    today = datetime.now(timezone(timedelta(hours=8)))
    yesterday = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    today_str = today.strftime("%Y-%m-%d")
    
    # Check if yesterday's data exists — skip if >= 8 profiles already done
    try:
        import psycopg2
        dsn = _os.environ.get("DATABASE_URL", "")
        if dsn:
            conn = psycopg2.connect(dsn)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(DISTINCT store||market) FROM ads_daily WHERE date = %s", (yesterday,))
            profiles = cur.fetchone()[0]
            conn.close()
            if profiles >= 8:
                print(f"[STARTUP] {profiles} profiles have data for {yesterday}, skipping ingest", flush=True)
                return
            print(f"[STARTUP] Only {profiles}/13 profiles for {yesterday}, running ingest", flush=True)
    except Exception as e:
        print(f"[STARTUP] DB check failed: {e}, running ingest anyway", flush=True)
    BASE3 = _Path(__file__).parent.parent
    path = BASE3 / "engines" / "ads_quick_ingest.py"
    if path.exists():
        import sys as _sys2, threading
        _sys2.path.insert(0, str(BASE3))
        print(f"[STARTUP] Running ingestion INLINE", flush=True)
        def _run_inline():
            try:
                from engines.ads_quick_ingest import run
                run()
                print(f"[INGEST] Complete", flush=True)
            except Exception as e:
                print(f"[INGEST] ERROR: {e}", flush=True)
                import traceback;traceback.print_exc()
        threading.Thread(target=_run_inline, daemon=True).start()
    else:
        print(f"[STARTUP] Ingest script not found at {path}", flush=True)


@app.get("/test/deepseek")
async def test_deepseek():
    """Test DeepSeek API connectivity."""
    import os as _os, httpx as _httpx
    key = _os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        return {"status": "error", "message": "DEEPSEEK_API_KEY not set"}
    try:
        r = _httpx.post("https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "Say OK"}], "max_tokens": 5},
            timeout=15)
        if r.status_code == 200:
            msg = r.json()["choices"][0]["message"]["content"]
            return {"status": "ok", "reply": msg}
        return {"status": "error", "http": r.status_code, "detail": r.text[:200]}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_consent():
    """Amazon Ads API privacy consent page — publicly accessible"""
    return """
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Privacy Consent — Amazon Ads API</title></head>
<body>
<h1>Privacy Consent for Amazon Ads API</h1>
<p>I, acting on behalf of Megapower, hereby consent to Amazon processing
our advertising data through the Amazon Ads API for the purpose of
analyzing ad performance and optimizing advertising campaigns.</p>
<p>This consent is effective as of June 8, 2026 and shall remain in effect
until revoked in writing.</p>
<p>Contact: TeaFruitix@outlook.com</p>
</body>
</html>
"""


@app.get("/privacy/heliumx", response_class=HTMLResponse)
async def privacy_heliumx():
    """Heliumx — Amazon Ads API privacy consent page"""
    return """
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Privacy Consent — Amazon Ads API (Heliumx)</title></head>
<body>
<h1>Privacy Consent for Amazon Ads API — Heliumx</h1>
<p>I, acting on behalf of Heliumx, hereby consent to Amazon processing
our advertising data through the Amazon Ads API for the purpose of
analyzing ad performance and optimizing advertising campaigns.</p>
<p>This consent is effective as of June 22, 2026 and shall remain in effect
until revoked in writing.</p>
<p>Contact: TeaFruitix@outlook.com</p>
</body>
</html>
"""


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    """Login page with form."""
    return """
<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Amazon Operations Platform</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);min-height:100vh;display:flex;justify-content:center;align-items:center}
.card{background:#fff;padding:48px 40px;border-radius:16px;box-shadow:0 20px 60px rgba(0,0,0,.3);width:400px;text-align:center}
h1{font-size:24px;color:#1a1a2e;margin-bottom:8px}
.sub{color:#888;font-size:14px;margin-bottom:32px}
.input-group{margin-bottom:20px;text-align:left}
.input-group label{display:block;font-size:13px;color:#555;margin-bottom:6px;font-weight:500}
.input-group input{width:100%;padding:12px 16px;border:2px solid #e0e0e0;border-radius:8px;font-size:15px;transition:border-color .2s;outline:none}
.input-group input:focus{border-color:#667eea}
.btn{width:100%;padding:14px;background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;border:none;border-radius:8px;font-size:16px;font-weight:600;cursor:pointer;transition:opacity .2s}
.btn:hover{opacity:.9}
.result{margin-top:24px;padding:16px;background:#f8f9ff;border-radius:8px;text-align:left;font-size:13px;display:none;border:1px solid #e8ecff}
.result .name{font-size:16px;font-weight:600;color:#1a1a2e}
.result .badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;margin-right:4px}
.badge-admin{background:#ffd700;color:#333}
.badge-read{background:#e0e0e0;color:#555}
.badge-write{background:#c8e6c9;color:#2e7d32}
.badge-store{background:#e3f2fd;color:#1565c0}
.token{font-family:monospace;font-size:11px;color:#aaa;margin-top:8px;word-break:break-all}
</style></head>
<body>
<div class="card">
<h1>📊 Amazon Operations</h1>
<p class="sub">Enter your User ID to access</p>
<div class="input-group">
<label>User ID</label>
<input id="uid" placeholder="sub_cuczuus" autofocus>
</div>
<button class="btn" onclick="login()">Sign In</button>
<div id="result" class="result"></div>
</div>
<script>
async function login(){
  const uid=document.getElementById('uid').value;
  if(!uid)return;
  const res=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:uid})});
  if(!res.ok){document.getElementById('result').style.display='block';document.getElementById('result').innerHTML='<span style=color:red>❌ Invalid User ID</span>';return}
  const d=await res.json();
  const r=document.getElementById('result');
  r.style.display='block';
  const roleBadge=d.role==='master_admin'?'<span class="badge badge-admin">Admin</span>':d.role==='read_admin'?'<span class="badge badge-read">Read Only</span>':'<span class="badge badge-read">Reader</span>';
  const writeBadge=d.write_allowed?'<span class="badge badge-write">Write</span>':'';
  const stores=d.stores.map(s=>'<span class="badge badge-store">'+s+'</span>').join(' ');
  r.innerHTML='<div class="name">👤 '+d.username+' '+roleBadge+' '+writeBadge+'</div><div style="margin-top:8px">'+stores+'</div><div class="token">Token: '+d.token.substring(0,32)+'...</div>';
}
document.getElementById('uid').addEventListener('keydown',function(e){if(e.key==='Enter')login()});
</script>
</body>
</html>
"""


@app.post("/login")
async def login(request: Request):
    """Login with user_id. Returns token + permissions."""
    import json as j
    users_file = Path(__file__).parent.parent / "auth" / "admin_users.json"
    with open(users_file) as f:
        users = j.load(f)
    
    body = await request.json()
    user_id = body.get("user_id", "")
    
    user = users.get(user_id)
    if not user or not user.get("is_active"):
        raise HTTPException(401, "Invalid user")
    
    rbac = RBACManager()
    token = hashlib.sha256(f"{user_id}:{datetime.utcnow()}".encode()).hexdigest()
    
    return {
        "token": token,
        "user_id": user_id,
        "username": user["username"],
        "role": user["role"],
        "stores": user.get("assigned_stores", []) if user["role"] == "sub_admin" else ["CUCZUUS", "BOOLUU", "Heliumx"],
        "write_allowed": user.get("write_allowed", user["role"] in ("master_admin", "write_admin")),
    }


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest, db: DBSession = Depends(get_db)):
    """Natural language query. RBAC-checked per store."""
    require_store_access(req.user_id, req.store_id, "read")
    engine = QueryEngine(db, req.store_id)
    result = engine.query(req.query)
    return QueryResponse(data={**result, "requested_by": req.user_id})


@app.post("/action/propose", response_model=ActionResponse)
async def propose_action(req: ActionRequest, db: DBSession = Depends(get_db)):
    """Propose an action. Requires write access. All writes need Level 2 approval."""
    require_store_access(req.user_id, req.store_id, "write")
    workflow = ApprovalWorkflow(db, req.store_id, actioned_by=req.user_id)
    result = workflow.propose(
        action_type=req.action_type,
        message=req.message,
        confidence=req.confidence,
        metadata=req.metadata,
    )
    return ActionResponse(status=result["status"], data={**result, "requested_by": req.user_id})


@app.post("/action/approve", response_model=ActionResponse)
async def approve_action(req: ApproveRequest, db: DBSession = Depends(get_db)):
    """Approve or reject a pending action. Master Admin only."""
    rbac = RBACManager()
    if not rbac.check_manage_users(req.actioned_by):
        raise HTTPException(status_code=403, detail="Only Master Admin can approve actions.")
    require_store_access(req.actioned_by, req.store_id, "write")
    workflow = ApprovalWorkflow(db, req.store_id, actioned_by=req.actioned_by)
    result = workflow.approve(
        action_id=req.action_id,
        approved=req.approved,
        notes=req.notes,
    )
    return ActionResponse(status=result.get("status", "ok"), data=result)


@app.get("/actions/pending")
async def pending_actions(store_id: str, user_id: str = "master",
                          db: DBSession = Depends(get_db)):
    """List pending actions for a store the user can access."""
    require_store_access(user_id, store_id, "read")
    workflow = ApprovalWorkflow(db, store_id)
    actions = workflow.get_pending()
    return {"status": "ok", "actions": actions, "user_id": user_id}


# ─── User Management (Master Admin Only) ───

@app.post("/users/create")
async def create_user(req: CreateUserRequest):
    """Create a sub-admin user. Master Admin only."""
    rbac = RBACManager()
    if not rbac.check_manage_users(req.created_by):
        raise HTTPException(status_code=403, detail="Only Master Admin can create users.")

    # Validate stores exist
    registry = get_store_registry()
    for s in req.assigned_stores:
        if not registry.is_valid_store(s):
            raise HTTPException(status_code=400, detail=f"Store '{s}' does not exist.")

    try:
        user = rbac.create_sub_admin(
            user_id=req.user_id,
            username=req.username,
            assigned_stores=req.assigned_stores,
            created_by=req.created_by,
        )
        return {"status": "ok", "user": user.model_dump()}
    except (ValueError, PermissionError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/users")
async def list_users(user_id: str = "master"):
    """List all users. Master Admin only."""
    rbac = RBACManager()
    if not rbac.check_manage_users(user_id):
        raise HTTPException(status_code=403, detail="Only Master Admin can list users.")
    users = rbac.list_users()
    return {
        "status": "ok",
        "users": [u.model_dump() for u in users],
        "requested_by": user_id,
    }


@app.delete("/users/{target_user_id}")
async def deactivate_user(target_user_id: str, actioned_by: str = "master"):
    """Deactivate a user. Master Admin only."""
    rbac = RBACManager()
    if not rbac.check_manage_users(actioned_by):
        raise HTTPException(status_code=403, detail="Only Master Admin can deactivate users.")
    try:
        result = rbac.deactivate_user(target_user_id, actioned_by)
        return {"status": "ok", "deactivated": target_user_id, "actioned_by": actioned_by}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


# ─── Dashboard ───

@app.get("/dashboard/{store_id}")
async def dashboard(store_id: str, user_id: str = "master",
                    db: DBSession = Depends(get_db)):
    """Store dashboard KPIs. RBAC-checked."""
    require_store_access(user_id, store_id, "read")

    inv_engine = InventoryEngine(db, store_id)
    alerts = inv_engine.analyze()
    critical = len([a for a in alerts if a["severity"] == "critical"])
    warnings = len([a for a in alerts if a["severity"] == "warning"])

    disc_engine = DiscrepancyEngine(db, store_id)
    discrepancies = disc_engine.scan_shipments()
    total_recovery = sum(d["estimated_loss"] for d in discrepancies)

    ads_engine = AdsEngine(db, store_id)
    try:
        tacos = ads_engine.calculate_tacos()
    except Exception:
        tacos = {"error": "No ad data"}

    voc = VoCEngine(db, store_id)
    try:
        complaints = voc.top_complaints(days=30, limit=3)
    except Exception:
        complaints = []

    return {
        "store_id": store_id,
        "requested_by": user_id,
        "inventory": {"critical_alerts": critical, "warning_alerts": warnings, "total": len(alerts)},
        "reimbursement": {"open": len(discrepancies), "estimated_recovery": round(total_recovery, 2)},
        "ads": tacos,
        "voc": {"top_complaints": complaints},
    }


# ─── Return & Refund Engine Routes ───


class FetchRefundsRequest(BaseModel):
    user_id: str = "master"
    store_id: str
    posted_after: Optional[str] = None
    posted_before: Optional[str] = None
    max_results: int = 100


@app.post("/finances/refunds")
async def fetch_refunds(req: FetchRefundsRequest):
    """Pull refund events from SP-API Finances API."""
    require_store_access(req.user_id, req.store_id, "read")
    engine = ReturnRefundEngine(req.store_id)
    events = engine.fetch_refunds_from_finances(
        posted_after=req.posted_after,
        posted_before=req.posted_before,
        max_results=req.max_results,
    )
    stored = engine.store_refund_events(events)
    return {
        "status": "ok",
        "fetched": len(events),
        "stored": stored,
        "store_id": req.store_id,
        "requested_by": req.user_id,
    }


@app.post("/finances/reconcile")
async def reconcile_losses(store_id: str, user_id: str = "master"):
    """Reconcile refunds → returns → loss events for AI analysis."""
    require_store_access(user_id, store_id, "write")
    engine = ReturnRefundEngine(store_id)
    reconciled = engine.reconcile_loss_events()
    return {
        "status": "ok",
        "reconciled": reconciled,
        "store_id": store_id,
        "requested_by": user_id,
    }


@app.get("/finances/loss/{store_id}")
async def get_profit_leakage(store_id: str, sku: Optional[str] = None, user_id: str = "master"):
    """Get profit leakage analysis by SKU."""
    require_store_access(user_id, store_id, "read")
    engine = ReturnRefundEngine(store_id)
    leakage = engine.get_profit_leakage(sku=sku)
    return {
        "status": "ok",
        "leakage": leakage,
        "store_id": store_id,
        "requested_by": user_id,
    }


@app.get("/finances/summary/{store_id}")
async def get_finance_summary(store_id: str, user_id: str = "master"):
    """Get high-level return/refund summary for dashboard."""
    require_store_access(user_id, store_id, "read")
    engine = ReturnRefundEngine(store_id)
    summary = engine.get_summary()
    return {
        "status": "ok",
        "summary": summary,
        "store_id": store_id,
        "requested_by": user_id,
    }


# ─── Profit Leakage Detection Routes ───


@app.get("/pld/leakage/{store_id}")
async def get_profit_leakage_sku(store_id: str, days: int = 30, user_id: str = "master"):
    """Get profit leakage breakdown by SKU for a store."""
    require_store_access(user_id, store_id, "read")
    engine = ProfitLeakageEngine(store_id)
    results = engine.scan_all_skus(days=days, min_loss=100)
    return {
        "status": "ok",
        "leakage": results,
        "total_leakage": sum(s.get("leakage", 0) for s in results),
        "store_id": store_id,
        "requested_by": user_id,
    }


@app.get("/pld/sku/{store_id}/{sku}")
async def get_sku_profit(store_id: str, asin: str, days: int = 30, user_id: str = "master"):
    """Full true profit + leakage breakdown for one SKU."""
    require_store_access(user_id, store_id, "read")
    engine = ProfitLeakageEngine(store_id)
    result = engine.calculate_sku_profit(sku, days=days)
    return {
        "status": "ok",
        "sku_analysis": result,
        "store_id": store_id,
        "requested_by": user_id,
    }


@app.get("/pld/scan/{store_id}")
async def scan_sku_leakage(store_id: str, days: int = 30, min_loss: float = 100.0, user_id: str = "master"):
    """Scan all SKUs and rank by profit leakage."""
    require_store_access(user_id, store_id, "read")
    engine = ProfitLeakageEngine(store_id)
    results = engine.scan_all_skus(days=days, min_loss=min_loss)
    return {
        "status": "ok",
        "leakage_ranked": results,
        "total_skus": len(results),
        "total_leakage": round(sum(s.get("leakage", 0) for s in results), 2),
        "store_id": store_id,
        "requested_by": user_id,
    }


@app.get("/pld/patterns/{store_id}/{sku}")
async def get_profit_patterns(store_id: str, asin: str, days: int = 90, user_id: str = "master"):
    """AI pattern detection for profit leakage.
    Returns: return reason clusters, wasted ad keywords, unclaimed FBA reimbursements.
    """
    require_store_access(user_id, store_id, "read")
    engine = ProfitLeakageEngine(store_id)
    patterns = engine.detect_patterns(sku, days=days)
    return {
        "status": "ok",
        "patterns": patterns,
        "sku": sku,
        "store_id": store_id,
        "requested_by": user_id,
    }


# ─── FBA Reimbursement Detection Routes ───


@app.get("/rds/scan/{store_id}")
async def scan_reimbursements(store_id: str, days_back: int = 90, user_id: str = "master"):
    """Scan for FBA reimbursement opportunities."""
    require_store_access(user_id, store_id, "read")
    engine = ReimbursementEngine(store_id)
    opportunities = engine.scan_all(days_back=days_back)
    return {
        "status": "ok",
        "opportunities": opportunities,
        "total": len(opportunities),
        "total_value": round(sum(o.get("estimated_value", 0) for o in opportunities), 2),
        "store_id": store_id,
        "requested_by": user_id,
    }


@app.get("/rds/queue/{store_id}")
async def get_recovery_queue(store_id: str, min_value: float = 100.0, user_id: str = "master"):
    """Get pending recovery opportunities sorted by value."""
    require_store_access(user_id, store_id, "read")
    engine = ReimbursementEngine(store_id)
    queue = engine.get_recovery_queue(min_value=min_value)
    return {
        "status": "ok",
        "queue": queue,
        "store_id": store_id,
        "requested_by": user_id,
    }


@app.get("/rds/summary/{store_id}")
async def get_recovery_summary(store_id: str, user_id: str = "master"):
    """Total recoverable value by category."""
    require_store_access(user_id, store_id, "read")
    engine = ReimbursementEngine(store_id)
    summary = engine.get_recovery_summary()
    return {
        "status": "ok",
        "summary": summary,
        "store_id": store_id,
        "requested_by": user_id,
    }


class EvidenceRequest(BaseModel):
    user_id: str = "master"
    store_id: str
    opportunity: dict  # previously scanned opportunity


@app.post("/rds/evidence")
async def generate_evidence(req: EvidenceRequest):
    """Generate an evidence pack for a specific opportunity."""
    require_store_access(req.user_id, req.store_id, "read")
    engine = ReimbursementEngine(req.store_id)
    pack = engine.generate_evidence_pack(req.opportunity)
    return {
        "status": "ok",
        "evidence_pack": pack,
        "store_id": req.store_id,
        "requested_by": req.user_id,
    }


@app.post("/rds/file/{opp_id}")
async def file_opportunity(opp_id: int, store_id: str, user_id: str = "master"):
    """Mark an opportunity as filed (after human approval)."""
    require_store_access(user_id, store_id, "write")
    engine = ReimbursementEngine(store_id)
    success = engine.file_opportunity(opp_id, filed_by=user_id)
    return {
        "status": "ok" if success else "error",
        "opportunity_id": opp_id,
        "store_id": store_id,
        "requested_by": user_id,
    }


# ─── Enhanced VoC Routes ───


@app.get("/voc/dashboard/{store_id}")
async def voc_intelligence(store_id: str, days: int = 90, user_id: str = "master",
                           db: DBSession = Depends(get_db)):
    """Complete VoC intelligence dashboard."""
    require_store_access(user_id, store_id, "read")
    engine = VoCEngine(db, store_id)
    return {
        "status": "ok",
        **engine.intelligence_dashboard(days=days),
        "store_id": store_id,
        "requested_by": user_id,
    }


@app.get("/voc/profit-impact/{store_id}")
async def voc_profit_impact(store_id: str, days: int = 90, user_id: str = "master",
                            db: DBSession = Depends(get_db)):
    """Correlate complaints with estimated profit loss."""
    require_store_access(user_id, store_id, "read")
    engine = VoCEngine(db, store_id)
    impact = engine.complaint_profit_impact(days=days)
    return {
        "status": "ok",
        "profit_impact": impact,
        "store_id": store_id,
        "requested_by": user_id,
    }


@app.get("/voc/trend/{store_id}")
async def voc_trend(store_id: str, topic: Optional[str] = None, weeks: int = 8,
                    user_id: str = "master", db: DBSession = Depends(get_db)):
    """Week-over-week complaint trends."""
    require_store_access(user_id, store_id, "read")
    engine = VoCEngine(db, store_id)
    trend = engine.complaint_trend(topic=topic, weeks=weeks)
    return {
        "status": "ok",
        "trend": trend,
        "store_id": store_id,
        "requested_by": user_id,
    }


@app.get("/voc/by-source/{store_id}")
async def voc_by_source(store_id: str, days: int = 30, user_id: str = "master",
                        db: DBSession = Depends(get_db)):
    """Feedback volume by source tier."""
    require_store_access(user_id, store_id, "read")
    engine = VoCEngine(db, store_id)
    by_source = engine.feedback_by_source(days=days)
    return {
        "status": "ok",
        "by_source": by_source,
        "store_id": store_id,
        "requested_by": user_id,
    }


# ─── Customer Communication Intelligence Routes ───


@app.post("/ccis/process")
async def ccis_process_message(store_id: str, body: str, subject: str = "",
                                amazon_order_id: str = "", asin: str = "",
                                user_id: str = "master"):
    """Full pipeline: classify → risk → draft → approval."""
    require_store_access(user_id, store_id, "write")
    engine = CCISEngine(store_id)
    result = engine.process_message(body, subject, amazon_order_id, sku)
    return {"status": "ok", **result, "store_id": store_id, "requested_by": user_id}


@app.get("/ccis/dashboard/{store_id}")
async def ccis_dashboard(store_id: str, days: int = 30, user_id: str = "master"):
    """CCIS dashboard: top complaints, risk, pending approvals."""
    require_store_access(user_id, store_id, "read")
    engine = CCISEngine(store_id)
    dashboard = engine.get_dashboard(days=days)
    return {"status": "ok", "dashboard": dashboard, "store_id": store_id, "requested_by": user_id}


@app.get("/ccis/classify")
async def ccis_classify(store_id: str, body: str, subject: str = "",
                        user_id: str = "master"):
    """Just classify a message without storing it."""
    require_store_access(user_id, store_id, "read")
    engine = CCISEngine(store_id)
    classification = engine.classify_message(body, subject)
    return {"status": "ok", "classification": classification, "store_id": store_id}


@app.post("/ccis/playbook")
async def ccis_create_playbook(store_id: str, issue_type: str, risk_level: str,
                                approved_resolution: str, template_body: str,
                                template_subject: str = "",
                                requires_approval: bool = True,
                                user_id: str = "master"):
    """Create a support playbook template."""
    require_store_access(user_id, store_id, "write")
    from db.connection import get_session
    from db.models import SupportPlaybook
    session = get_session()
    try:
        pb = SupportPlaybook(
            store_id=store_id, issue_type=issue_type, risk_level=risk_level,
            approved_resolution=approved_resolution, template_body=template_body,
            template_subject=template_subject, requires_approval=requires_approval,
            created_by=user_id)
        session.add(pb)
        session.commit()
        return {"status": "ok", "playbook_id": pb.id, "store_id": store_id}
    finally:
        session.close()


# ─── Two-Tier P&L: Daily Estimate + Settlement Correction ───


@app.get("/pld/fba-rate/{store_id}")
async def get_fba_rate(store_id: str, user_id: str = "master"):
    """Get historical FBA fee rate per SKU for daily profit estimation."""
    require_store_access(user_id, store_id, "read")
    from db.connection import get_engine
    from sqlalchemy import text
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT sku, COUNT(*) as tx_count, 
                   ABS(SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END)) as fees,
                   SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) as revenue
            FROM financial_transactions 
            WHERE store_id = :sid AND transaction_type = 'refund'
            GROUP BY sku
        """), {"sid": store_id}).fetchall()
    rates = {}
    for r in rows:
        if r.revenue and r.revenue > 0:
            rates[r.sku] = {
                "fba_fee_rate": round(r.fees / r.revenue, 4),
                "transactions": r.tx_count,
            }
    return {"status": "ok", "sku_fba_rates": rates, "store_id": store_id, "requested_by": user_id}


@app.post("/pld/estimate-daily/{store_id}")
async def estimate_daily_profit(store_id: str, revenue: float, asin: str = "",
                                 cogs_rate: float = 0.5, ads_rate: float = 0.03,
                                 user_id: str = "master"):
    """Estimate daily profit using historical FBA fee rates.
    Formula: Est. Profit = Revenue * (1 - FBA_rate - COGS_rate - ads_rate)
    """
    require_store_access(user_id, store_id, "read")
    from db.connection import get_engine
    from sqlalchemy import text
    engine = get_engine()
    
    # Get historical FBA rate for this SKU
    fba_rate = 0.50  # default if no data
    if sku:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT ABS(SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END)) / 
                       GREATEST(SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END), 1) as rate
                FROM financial_transactions 
                WHERE store_id = :sid AND sku = :sku AND transaction_type = 'refund'
            """), {"sid": store_id, "sku": sku}).fetchone()
            if row and row.rate:
                fba_rate = float(row.rate)
    
    estimated_fba = revenue * fba_rate
    estimated_cogs = revenue * cogs_rate
    estimated_ads = revenue * ads_rate
    estimated_profit = revenue - estimated_fba - estimated_cogs - estimated_ads
    margin_pct = estimated_profit / revenue if revenue > 0 else 0
    
    return {
        "status": "ok",
        "revenue": round(revenue, 2),
        "fba_rate": round(fba_rate, 4),
        "estimated_fba": round(estimated_fba, 2),
        "estimated_cogs": round(estimated_cogs, 2),
        "estimated_ads": round(estimated_ads, 2),
        "estimated_profit": round(estimated_profit, 2),
        "estimated_margin": round(margin_pct, 4),
        "note": "Weekly settlement will correct these estimates with actual fees",
        "store_id": store_id,
    }



@app.get("/fba/expected-fee")
async def expected_fba_fee(weight_lbs: float = None, price: float = None,
                            category: str = "standard", is_apparel: bool = False):
    """Calculate expected FBA fulfillment fee using Amazon's formula.
    
    Returns the expected fee and compares with any actual fee you provide.
    """
    # Amazon US FBA fulfillment fee schedule (2025-2026)
    # Size tiers based on weight and dimensions
    if weight_lbs and weight_lbs <= 0.5:
        expected = 3.50  # Small Standard
        tier = "Small Standard"
    elif weight_lbs and weight_lbs <= 1.0:
        expected = 4.50  # Large Standard (up to 1 lb)
        tier = "Large Standard ≤1lb"
    elif weight_lbs and weight_lbs <= 2.0:
        expected = 5.50  # Large Standard (1-2 lb)
        tier = "Large Standard ≤2lb"
    elif weight_lbs and weight_lbs <= 3.0:
        expected = 6.50  # Large Standard (2-3 lb)
        tier = "Large Standard ≤3lb"
    elif weight_lbs and weight_lbs <= 21.0:
        expected = 9.50 + (weight_lbs - 3) * 0.42  # Small Oversize
        tier = "Small Oversize"
        if weight_lbs > 10:
            expected = 15.00 + (weight_lbs - 10) * 0.50  # Large Oversize
            tier = "Large Oversize"
    else:
        expected = 25.00  # Extra Large
        tier = "Extra Large"
    
    if is_apparel:
        expected += 1.50  # Apparel surcharge
        tier += " + Apparel"
    
    return {
        "status": "ok",
        "expected_fee": round(expected, 2),
        "size_tier": tier,
        "weight_lbs": weight_lbs,
        "note": "Compare with actual fee from settlements to detect overcharges",
    }


@app.get("/fba/fee-variance/{store_id}")
async def fba_fee_variance(store_id: str, days: int = 90, user_id: str = "master"):
    """Compare expected vs actual FBA fees to detect overcharges."""
    require_store_access(user_id, store_id, "read")
    from db.connection import get_engine
    from sqlalchemy import text
    engine = get_engine()
    
    with engine.connect() as conn:
        # Get all ItemFees from financial_transactions (actual fees)
        rows = conn.execute(text("""
            SELECT sku, COUNT(*) as tx_count, 
                   SUM(ABS(amount)) as actual_fees,
                   AVG(ABS(amount)) as avg_fee_per_tx
            FROM financial_transactions 
            WHERE store_id = :sid 
              AND transaction_type = 'refund'
              AND ABS(amount) > 0
              AND posted_date >= NOW() - INTERVAL ':days days'
            GROUP BY sku
            ORDER BY actual_fees DESC
            LIMIT 20
        """), {"sid": store_id, "days": days}).fetchall()
    
    results = []
    for r in rows:
        # For each SKU, estimate expected fee
        weight_lbs = 2.0  # default assumption
        if "FT29" in r.sku: weight_lbs = 4.2
        elif "FT22" in r.sku: weight_lbs = 3.5
        elif "FT19" in r.sku: weight_lbs = 3.0
        elif "Round" in r.sku: weight_lbs = 2.5
        
        # Calculate expected fee
        if weight_lbs <= 1.0:
            expected = 4.50
        elif weight_lbs <= 2.0:
            expected = 5.50
        elif weight_lbs <= 3.0:
            expected = 6.50
        else:
            expected = 9.50 + (weight_lbs - 3) * 0.42
        
        variance = float(r.avg_fee_per_tx) - expected
        
        results.append({
            "sku": r.sku,
            "transactions": r.tx_count,
            "actual_avg_fee": round(float(r.avg_fee_per_tx), 2),
            "expected_fee": round(expected, 2),
            "variance": round(variance, 2),
            "variance_pct": round(variance / expected * 100, 1) if expected > 0 else 0,
            "flag": abs(variance) > 1.0,  # Flag if >$1 difference
        })
    
    total_variance = sum(r["variance"] * r["transactions"] for r in results)
    flagged = [r for r in results if r["flag"]]
    
    return {
        "status": "ok",
        "fee_variance": results,
        "flagged_count": len(flagged),
        "flagged": flagged,
        "total_estimated_overcharge": round(-total_variance, 2) if total_variance < 0 else 0,
        "note": "Negative variance = Amazon charging more than expected formula",
    }
async def consolidate_po(asin: str, days: int = 90, user_id: str = "master"):
    """Consolidate PO across all stores for one ASIN."""
    require_store_access(user_id, "", "read")
    from engines.cost_engine import CostEngine
    from engines.product_catalog_engine import ProductCatalogEngine
    from datetime import date, timedelta
    
    # All stores that could have this ASIN
    store_info = {
        "01": {"name": "Heliumx", "monthly": 222},
        "02": {"name": "CUCZZUS", "monthly": 450},
        "03": {"name": "BOOLUU", "monthly": 200},
    }
    lead_time_days = 75  # factory 30 + sea 45
    
    results = []
    for sid, info in store_info.items():
        stock = 0
        from db.connection import get_engine
        from sqlalchemy import text
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT SUM(sellable_units) FROM inventory_snapshot 
                WHERE store_id = :sid AND asin = :asin
            """), {"sid": sid, "asin": asin}).fetchone()
            if row and row[0]:
                stock = int(row[0])
        
        monthly = info["monthly"]
        daily = monthly / 30
        cover = round(stock / max(daily, 1), 1) if stock > 0 else 0
        
        if cover < lead_time_days:
            risk = "critical" if cover < lead_time_days * 0.7 else "warning"
        else:
            risk = "safe"
        
        results.append({
            "store": info["name"],
            "stock": stock,
            "monthly_sales": monthly,
            "days_of_cover": cover,
            "risk": risk,
        })
    
    total_monthly = sum(r["monthly_sales"] for r in results)
    total_stock = sum(r["stock"] for r in results)
    daily_total = total_monthly / 30
    reorder_point = int(lead_time_days * daily_total)
    recommended_po = max(0, int(lead_time_days * daily_total * 1.2) - total_stock)
    
    return {
        "status": "ok",
        "asin": asin,
        "consolidation": results,
        "total_daily_demand": round(daily_total, 1),
        "total_stock": total_stock,
        "lead_time_days": lead_time_days,
        "reorder_point": reorder_point,
        "recommended_po_qty": recommended_po,
        "action": "Order now" if recommended_po > 0 else "Stock sufficient",
    }



# ─── Master Product Catalog Routes ───


@app.post("/catalog/product")
async def catalog_upsert_product(sku: str, product_name: str = None,
                                   category: str = None,
                                   length_cm: float = None, width_cm: float = None,
                                   height_cm: float = None, weight_kg: float = None,
                                   manufacturing_cny: float = None,
                                   packaging_cny: float = None,
                                   user_id: str = "master"):
    """Create or update a master product (dimensions, COGS)."""
    engine = ProductCatalogEngine()
    result = engine.upsert_product(sku, product_name=product_name, category=category,
                                    length_cm=length_cm, width_cm=width_cm,
                                    height_cm=height_cm, weight_kg=weight_kg,
                                    manufacturing_cny=manufacturing_cny,
                                    packaging_cny=packaging_cny)
    return {"status": "ok", **result}


@app.get("/catalog/product/{sku}")
async def catalog_get_product(sku: str, user_id: str = "master"):
    """Get product details with all store mappings."""
    engine = ProductCatalogEngine()
    result = engine.get_product(sku)
    return {"status": "ok", **result}


@app.get("/catalog/products")
async def catalog_list_products(user_id: str = "master"):
    """List all master products."""
    engine = ProductCatalogEngine()
    products = engine.list_products()
    return {"status": "ok", "products": products, "count": len(products)}


@app.post("/catalog/mapping")
async def catalog_add_mapping(sku: str, store_id: str, marketplace_name: str,
                               asin: str, marketplace_id: str = "ATVPDKIKX0DER",
                               selling_price: float = 0, currency: str = "USD",
                               freight_per_unit_cny: float = 0,
                               user_id: str = "master"):
    """Map a product to a store + marketplace with freight."""
    engine = ProductCatalogEngine()
    result = engine.add_mapping(sku, store_id, marketplace_id, marketplace_name, asin,
                                 selling_price=selling_price, currency=currency,
                                 freight_per_unit_cny=freight_per_unit_cny)
    return {"status": "ok", **result}


@app.get("/catalog/mappings")
async def catalog_list_mappings(store_id: str = None, user_id: str = "master"):
    """List all product↔store mappings."""
    engine = ProductCatalogEngine()
    mappings = engine.get_mappings(store_id=store_id)
    return {"status": "ok", "mappings": mappings, "count": len(mappings)}


@app.get("/catalog/true-cost/{sku}")
async def catalog_true_cost(sku: str, store_id: str = None, marketplace: str = "US",
                             user_id: str = "master"):
    """Get true per-unit cost in USD for a product."""
    engine = ProductCatalogEngine()
    cost = engine.get_true_cost(sku, store_id=store_id, marketplace=marketplace)
    return {"status": "ok", **cost}


@app.get("/catalog/margin/{sku}")
async def catalog_margin(sku: str, selling_price: float, store_id: str = None,
                          user_id: str = "master"):
    """Get true profit margin for a product."""
    engine = ProductCatalogEngine()
    margin = engine.get_margin(sku, selling_price, store_id=store_id)
    return {"status": "ok", **margin}


@app.post("/catalog/bulk-import")
async def catalog_bulk_import(data: dict, user_id: str = "master"):
    """Bulk import products and mappings."""
    engine = ProductCatalogEngine()
    result = engine.bulk_import(data.get("products", []))
    return {"status": "ok", **result}


@app.post("/listings/create")
async def create_listing(data: dict, user_id: str = "master"):
    """Create or update an Amazon product listing. Master + listing_write sub admins."""
    from auth.rbac import RBACManager
    rbac = RBACManager()
    user = rbac.get_user(user_id)
    if not user:
        raise HTTPException(403, "Unknown user")
    if user.role not in ("master_admin", "write_admin") and not user.listing_write:
        raise HTTPException(403, "No listing write permission")
    
    store = data.get("store", "02")
    sku = data.get("sku")
    listing_data = data.get("listing", {})
    
    if not sku or not listing_data:
        raise HTTPException(400, "sku and listing data required")
    
    # Sub admins: restrict to their assigned store
    if user.store and user.store != store:
        raise HTTPException(403, f"You can only manage {user.store} listings")
    
    try:
        from engines.listings_engine import ListingsEngine
        engine = ListingsEngine()
        result = engine.create_or_update(store, sku, listing_data)
        return {"status": "ok", "result": result}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/listings/{store}/{sku}")
async def get_listing(store: str, sku: str):
    """Get listing details."""
    try:
        from engines.listings_engine import ListingsEngine
        engine = ListingsEngine()
        result = engine.get_listing(store, sku)
        return {"status": "ok", "listing": result}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/admin/upload-ratecard")
async def upload_ratecard(data: dict, user_id: str = "master"):
    """Upload FBA rate card — stores as JSON in DB."""
    from auth import require_admin
    require_admin(user_id, "write")
    
    rate_data = data.get("rates", {})
    market = data.get("market", "")
    
    if not market or not rate_data:
        raise HTTPException(400, "market and rates required")
    
    try:
        from db.connection import get_db
        from sqlalchemy import text
        db = next(get_db())
        db.execute(text("""
            INSERT INTO rate_cards (market, data, updated_at)
            VALUES (:market, :data, NOW())
            ON CONFLICT (market) DO UPDATE SET data = :data, updated_at = NOW()
        """), {"market": market, "data": json.dumps(rate_data)})
        db.commit()
        return {"status": "ok", "market": market, "tiers": len(rate_data)}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/admin/ratecard/{market}")
async def get_ratecard(market: str):
    """Get FBA rate card for a market."""
    try:
        from db.connection import get_db
        from sqlalchemy import text
        db = next(get_db())
        result = db.execute(text("SELECT data, updated_at FROM rate_cards WHERE market = :market"), {"market": market}).fetchone()
        if not result:
            raise HTTPException(404, f"No rate card for {market}")
        return {"market": market, "data": result[0], "updated": str(result[1])}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


async def _run_scheduler():
    """Background scheduler — checks every 60s, runs jobs at scheduled times."""
    import asyncio, subprocess, sys as _sys
    from datetime import datetime, timezone, timedelta
    from pathlib import Path as _Path
    
    TZ = timezone(timedelta(hours=8))  # Asia/Shanghai
    
    BASE2 = _Path(__file__).parent.parent if __file__.endswith('api/app.py') else _Path.cwd()
    SCHEDULE = [
        (8, 0, None, "engines/ads_health_scanner.py", "Ads Health"),
        (8, 0, None, "engines/inbox_monitor.py", "Inbox"),
        (8, 0, 1, "engines/discrepancy_scanner.py", "Discrepancy Mon"),
        (8, 0, 5, "engines/discrepancy_scanner.py", "Discrepancy Fri"),
         (15, 5, None, "engines/ads_quick_ingest.py", "Ads Ingestion"),
        (14, 0, None, "engines/negative_keyword_engine.py", "Negative Keywords"),
        (16, 0, None, "engines/order_pull.py", "Order Pull (Reports API)"),
        (17, 0, None, "daily_revenue_report.py", "Revenue Report"),
    ]
    
    logger.info("Scheduler started with %d jobs", len(SCHEDULE))
    print(f"[SCHEDULER] Started with {len(SCHEDULE)} jobs", flush=True)
    last_run = {}
    
    while True:
        try:
            now = datetime.now(TZ)
            if now.minute % 5 == 0 and now.second < 60:
                logger.info("[Scheduler] alive at %s", now.strftime("%H:%M"))
                print(f"[SCHEDULER] alive at {now.strftime('%H:%M')}", flush=True)
            for hour, minute, dow, script, name in SCHEDULE:
                key = (hour, minute, dow or 0, script)
                if now.hour == hour and now.minute == minute:
                    if dow is not None and now.isoweekday() != dow:
                        continue
                    if key in last_run and (now - last_run[key]).seconds < 90:
                        continue
                    last_run[key] = now
                    path = BASE2 / script
                    if path.exists():
                        logger.info("[%s] JOB TRIGGERED at %s", name, now.strftime("%H:%M"))
                        import os as _os
                        cmd = f"{_sys.executable} {path}"
                        loop = asyncio.get_running_loop()
                        result = await loop.run_in_executor(None, lambda: _os.popen(cmd).read())
                        logger.info("[%s] JOB DONE at %s: %s", name, datetime.now(TZ).strftime("%H:%M"), result[:200] if result else 'no output')
            await asyncio.sleep(60)
        except Exception as e:
            logger.error("Scheduler loop error: %s", e)
            await asyncio.sleep(60)


@app.on_event("startup")
async def startup():
    # Start scheduler FIRST — it must always run
    import asyncio as _asyncio
    _asyncio.create_task(_run_scheduler())
    
    # Run ingestion check (skips if enough data exists)
    _asyncio.create_task(_startup_ingest())
    
    try:
        from auth import get_store_registry
        registry = get_store_registry()
        rbac = RBACManager()
        logger.info("API started. Stores: %s | Users: %s",
                     list(registry.active_stores.keys()) if registry.active_stores else "none",
                     [u.user_id for u in rbac.list_users()])
    except Exception as e:
        logger.warning("Startup with limited config: %s", e)


@app.on_event("shutdown")
async def shutdown():
    from auth import close as close_auth
    await close_auth()
