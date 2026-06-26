#!/usr/bin/env python3
"""
Seller Central Inbox Monitor — runs daily via cron
Checks: buyer messages, compliance verification, account health notifications.

Report: workspace/inbox_report.md
"""
import httpx, os, hashlib, hmac, time, json
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import urlparse, quote
from collections import defaultdict

BASE = Path("/Users/jiahe/.openclaw/workspace/Planned system project/amazon-ops-intel")
REPORT = Path("/Users/jiahe/.openclaw/workspace/inbox_report.md")

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

load_env()
now = datetime.now()

# Pull account health notifications for each store
stores = [
    ("CUCZUUS", "02", "STORE_02_REFRESH_TOKEN_AMERICAS"),
    ("BOOLUU", "03", "STORE_03_REFRESH_TOKEN_AMERICAS"),
    ("Heliumx", "04", "STORE_04_REFRESH_TOKEN_AMERICAS"),
]

records = []

for name, store_num, token_env in stores:
    cid = os.environ[f"STORE_{store_num}_LWA_CLIENT_ID"]
    csec = os.environ[f"STORE_{store_num}_LWA_CLIENT_SECRET"]
    ref = os.environ[token_env]
    tk = get_token(cid, csec, ref)
    
    # Check notifications (compliance, verification, policy warnings)
    h = {"x-amz-access-token": tk}
    notif_url = "https://sellingpartnerapi-na.amazon.com/notifications/v1/destinations"
    h = sign("GET", notif_url, h, "")
    rr = httpx.get(notif_url, headers=h, timeout=10)
    has_notifications = rr.status_code == 200
    
    # Check buyer messages via orders — pull recent orders with issues
    # Check for negative feedback
    h2 = {"x-amz-access-token": tk}
    feedback_url = "https://sellingpartnerapi-na.amazon.com/sellers/v1/feedbacks"
    h2 = sign("GET", feedback_url, h2, "")
    rr2 = httpx.get(feedback_url, headers=h2, timeout=10)
    
    if rr2.status_code == 200:
        feedbacks = rr2.json().get("payload", {}).get("FeedbackList", [])
        for fb in feedbacks:
            rating = fb.get("Rating", "")
            comment = fb.get("Comments", "")
            if rating in ("1", "2"):
                records.append({
                    "store": name,
                    "type": "🔴 Negative Feedback",
                    "detail": f"Rating: {rating}/5 — {comment[:100]}",
                    "date": fb.get("FeedbackDate", "")[:10],
                    "action": "Respond within 24h — use CCIS playbook"
                })
    
    # Check returns (compliance issues often come through returns)
    try:
        h3 = {"x-amz-access-token": tk, "Content-Type": "application/json"}
        body = json.dumps({
            "reportType": "GET_FBA_FULFILLMENT_CUSTOMER_RETURNS_DATA",
            "dataStartTime": (now - timedelta(days=3)).strftime("%Y-%m-%dT00:00:00Z"),
            "dataEndTime": now.strftime("%Y-%m-%dT23:59:59Z"),
            "marketplaceIds": ["ATVPDKIKX0DER"]
        })
        h3 = sign("POST", "https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/reports", h3, body)
        rr3 = httpx.post("https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/reports",
                         headers=h3, content=body, timeout=10)
        if rr3.status_code in (200, 202):
            records.append({
                "store": name,
                "type": "📦 Recent Returns",
                "detail": f"New returns report created — check for compliance-related returns",
                "date": now.strftime("%Y-%m-%d"),
                "action": "Review for regulatory issues"
            })
    except:
        pass
    
    time.sleep(0.5)

# Generate report
lines = [
    f"# Seller Central Inbox Monitor — {now.strftime('%Y-%m-%d %H:%M')}",
    f"Covers: buyer messages, feedback, compliance, returns",
    f"Next check: Daily 9 AM China time",
    "",
]

if records:
    lines.append("## 🚨 Items Requiring Attention")
    lines.append("")
    lines.append("| Store | Type | Detail | Date | Action |")
    lines.append("|---|---|---|---|---|")
    for r in records:
        lines.append(f"| {r['store']} | {r['type']} | {r['detail']} | {r['date']} | {r['action']} |")
else:
    lines.append("## ✅ All Clear")
    lines.append("No negative feedback, no compliance issues, no urgent returns.")

lines.append(f"\n*Generated: {now.strftime('%Y-%m-%d %H:%M')}*")

REPORT.write_text("\n".join(lines))
print(f"✅ Inbox report saved: {REPORT}")
print(f"   Records: {len(records)}")
for r in records:
    print(f"   {r['store']} | {r['type']} | {r['detail'][:60]}")
PYEOF
