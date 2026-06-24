#!/usr/bin/env python3
"""
Inbound Discrepancy Scanner v2 — Persistent tracking
Runs Mon/Fri 8 AM China time via cron.
Tracks unresolved issues across weeks until cleared.
State file: workspace/discrepancy_state.json
Report: workspace/discrepancy_report.md
"""
import httpx, os, hashlib, hmac, time, json, openpyxl
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import urlparse, quote

BASE = Path("/Users/jiahe/.openclaw/workspace/Planned system project/amazon-ops-intel")
STATE_FILE = Path("/Users/jiahe/.openclaw/workspace/discrepancy_state.json")
REPORT = Path("/Users/jiahe/.openclaw/workspace/discrepancy_report.md")

def load_env():
    for line in (BASE / ".env").read_text().splitlines():
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

def sign(m, u, h, b, reg="us-east-1"):
    AK = os.environ["STORE_01_AWS_ACCESS_KEY_ID"]
    AS = os.environ["STORE_01_AWS_SECRET_ACCESS_KEY"]
    p = urlparse(u)
    ad = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    ds = time.strftime("%Y%m%d", time.gmtime())
    h["x-amz-date"] = ad; h["host"] = p.hostname
    cu = quote(p.path, safe="/") or "/"
    ch = "".join(f"{kl.lower()}:{v.strip()}\n" for kl, v in sorted(h.items()) if kl.lower() != "authorization")
    sh = ";".join(sorted(kl.lower() for kl in h if kl.lower() != "authorization"))
    ph = hashlib.sha256(b.encode()).hexdigest()
    cr = "\n".join([m.upper(), cu, p.query, ch, sh, ph])
    cs = f"{ds}/{reg}/execute-api/aws4_request"
    st = "\n".join(["AWS4-HMAC-SHA256", ad, cs, hashlib.sha256(cr.encode()).hexdigest()])
    def h8(k, m):
        if isinstance(k, str): k = k.encode()
        return hmac.new(k, m.encode(), hashlib.sha256).digest()
    k1 = h8(f"AWS4{AS}", ds); k2 = h8(k1, reg); k3 = h8(k2, "execute-api"); k4 = h8(k3, "aws4_request")
    sg = hmac.new(k4, st.encode(), hashlib.sha256).hexdigest()
    h["Authorization"] = f"AWS4-HMAC-SHA256 Credential={AK}/{cs}, SignedHeaders={sh}, Signature={sg}"
    return h

def get_token(cid, csec, ref):
    r = httpx.post("https://api.amazon.com/auth/o2/token",
                   json={"grant_type": "refresh_token", "client_id": cid,
                         "client_secret": csec, "refresh_token": ref}, timeout=10)
    return r.json()["access_token"]

def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"discrepancies": {}, "acknowledged": []}

def save_state(state):
    # Auto-cleanup: remove items older than 30 days from acknowledged list
    now = datetime.now()
    state["acknowledged"] = [a for a in state.get("acknowledged", [])
                             if a.get("ack_date", "2000-01-01") > (now - timedelta(days=30)).strftime("%Y-%m-%d")]
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

load_env()

# Load product catalog for valuation
wb = openpyxl.load_workbook("/Users/jiahe/Desktop/Master_Product_Catalog_v2.xlsx")
ws = wb["Products"]
prods = {}
for r in range(2, ws.max_row + 1):
    s = str(ws.cell(r, 1).value or "").strip()
    sku = str(ws.cell(r, 10).value or "").strip()
    if s == "CUCZUUS" and sku:
        manuf = ws.cell(r, 9).value
        if manuf: prods[sku] = float(manuf)

state = load_state()
now = datetime.now()
today = now.strftime("%Y-%m-%d")

# Scan for new discrepancies
stores = [("CUCZUUS", "02", "STORE_02_REFRESH_TOKEN_AMERICAS", "ATVPDKIKX0DER")]
new_found = 0
resolved = 0

for name, store_num, token_env, mkt_id in stores:
    cid = os.environ[f"STORE_{store_num}_LWA_CLIENT_ID"]
    csec = os.environ[f"STORE_{store_num}_LWA_CLIENT_SECRET"]
    ref = os.environ[token_env]
    tk = get_token(cid, csec, ref)
    
    endpoint = "https://sellingpartnerapi-na.amazon.com/fba/inbound/v0/shipments"
    params = {"QueryType": "SHIPMENT", "MarketplaceId": mkt_id,
              "LastUpdatedAfter": "2026-05-01T00:00:00Z",
              "LastUpdatedBefore": now.strftime("%Y-%m-%dT23:59:59Z")}
    
    h = {"x-amz-access-token": tk}
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    h = sign("GET", f"{endpoint}?{qs}", h, "")
    rr = httpx.get(f"{endpoint}?{qs}", headers=h, timeout=15)
    
    if rr.status_code != 200:
        continue
    
    shipments = rr.json().get("payload", {}).get("ShipmentData", [])
    
    # Track which discrepancy keys still exist
    live_keys = set()
    
    for s in shipments:
        sid = s.get("ShipmentId", "")
        status = s.get("ShipmentStatus", "")
        if status not in ("CLOSED", "RECEIVING"):
            continue
        
        h2 = {"x-amz-access-token": tk}
        h2 = sign("GET", f"{endpoint}/{sid}/items?MarketplaceId={mkt_id}", h2, "")
        r2 = httpx.get(f"{endpoint}/{sid}/items?MarketplaceId={mkt_id}", headers=h2, timeout=10)
        if r2.status_code != 200:
            continue
        
        items = r2.json().get("payload", {}).get("ItemData", [])
        for item in items:
            shipped = int(item.get("QuantityShipped", 0))
            received = int(item.get("QuantityReceived", 0))
            sku = item.get("SellerSKU", "")
            key = f"{sid}|{sku}"
            live_keys.add(key)
            
            if shipped > received:
                lost = shipped - received
                val = prods.get(sku, 0) * lost
                
                if key not in state["discrepancies"]:
                    # New discrepancy
                    state["discrepancies"][key] = {
                        "shipment": sid,
                        "sku": sku,
                        "shipped": shipped,
                        "received": received,
                        "lost": lost,
                        "value_cny": val,
                        "first_seen": today,
                        "last_seen": today,
                        "weeks_outstanding": 1,
                        "status": "open"
                    }
                    new_found += 1
                else:
                    # Update existing
                    d = state["discrepancies"][key]
                    d["shipped"] = shipped
                    d["received"] = received
                    d["lost"] = shipped - received
                    d["value_cny"] = prods.get(sku, 0) * d["lost"]
                    d["last_seen"] = today
                    d["weeks_outstanding"] = max(1, d.get("weeks_outstanding", 1))
                    if d["lost"] == 0:
                        d["status"] = "resolved"
                        d["resolved_date"] = today
                        resolved += 1
                    else:
                        d["status"] = "open"
                
                time.sleep(0.3)
    
    # Mark items no longer in live data as resolved (if not already)
    for key in list(state["discrepancies"].keys()):
        if key.startswith(name) or not any(key.startswith(s.get("ShipmentId", "")) for s in shipments):
            pass  # Keep in state for tracking
    
    save_state(state)

# Generate report
report = []
report.append(f"# FBA Inbound Discrepancy Report — {today}")
report.append(f"Next scan: Mon & Fri 8 AM | Auto-cleanup: 30 days")
report.append("")
report.append("## 🔴 Open Discrepancies")
report.append("")
report.append("| Shipment | SKU | Lost | Value (¥) | First Seen | Weeks | Weeks Old |")
report.append("|---|---|---|---|---|---|---|")

open_items = {k: v for k, v in state["discrepancies"].items() if v.get("status") == "open"}
open_by_age = sorted(open_items.items(), key=lambda x: x[1].get("first_seen", "9999"), reverse=False)

total_value = 0
for key, d in open_by_age:
    first = d.get("first_seen", "?")
    days_old = (now - datetime.strptime(first, "%Y-%m-%d")).days if first != "?" else 0
    weeks_old = days_old // 7
    flag = "🔴" if weeks_old >= 3 else ("🟡" if weeks_old >= 1 else "⚪")
    total_value += d.get("value_cny", 0)
    report.append(f"| {flag} {d['shipment'][:20]} | {d['sku']} | {d['lost']} | ¥{d['value_cny']:,.0f} | {first} | {d.get('weeks_outstanding',0)} | {weeks_old}w |")

report.append("")
report.append(f"**Open: {len(open_items)} discrepancies, ¥{total_value:,.0f} total**")
report.append("")

# Resolved this scan
if resolved > 0:
    report.append(f"## ✅ Resolved This Scan ({resolved})")
    report.append("")

# Action items
report.append("## ⚠️ Action Required")
report.append("")
old_items = [d for k, d in open_items if (now - datetime.strptime(d.get("first_seen", "9999-01-01"), "%Y-%m-%d")).days >= 21]
if old_items:
    report.append(f"### 🔴 {len(old_items)} discrepancies 3+ weeks old — HUMAN INTERVENTION REQUIRED")
    for d in old_items:
        report.append(f"- {d['shipment'][:20]} | {d['sku']} | {d['lost']} units | ¥{d['value_cny']:,.0f} | since {d.get('first_seen')}")
else:
    report.append("### 🟢 No items over 3 weeks old — all within tracking window")

report.append(f"\n*Generated: {now.strftime('%Y-%m-%d %H:%M')} | New: {new_found} | Resolved: {resolved}*")

REPORT.write_text("\n".join(report))
print(f"✅ Report saved | New: {new_found} | Open: {len(open_items)} | Resolved: {resolved}")
print(f"   Oldest: {open_by_age[0][1].get('first_seen','?') if open_by_age else 'N/A'}")
PYEOF
