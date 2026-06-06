"""Direct Finances API call — no complex dependencies."""
import httpx, os, hashlib, hmac, time, json
from urllib.parse import urlparse, quote
from fastapi import APIRouter

router = APIRouter()

def _sign(m, u, h, b):
    AK = os.environ.get("STORE_01_AWS_ACCESS_KEY_ID", "")
    AS = os.environ.get("STORE_01_AWS_SECRET_ACCESS_KEY", "")
    p = urlparse(u)
    ad = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    ds = time.strftime("%Y%m%d", time.gmtime())
    h["x-amz-date"] = ad
    h["host"] = p.hostname
    cu = quote(p.path, safe="/") or "/"
    ch = "".join(f"{kl.lower()}:{v.strip()}\n" for kl, v in sorted(h.items()) if kl.lower() not in ("authorization",))
    sh = ";".join(sorted(kl.lower() for kl in h if kl.lower() not in ("authorization",)))
    ph = hashlib.sha256(b.encode()).hexdigest()
    cr = "\n".join([m.upper(), cu, p.query, ch, sh, ph])
    cs = f"{ds}/us-east-1/execute-api/aws4_request"
    st = "\n".join(["AWS4-HMAC-SHA256", ad, cs, hashlib.sha256(cr.encode()).hexdigest()])
    def h8(k, m):
        if isinstance(k, str): k = k.encode()
        return hmac.new(k, m.encode(), hashlib.sha256).digest()
    sig = hmac.new(h8(h8(h8(h8(f"AWS4{AS}", ds), "us-east-1"), "execute-api"), "aws4_request"), st.encode(), hashlib.sha256).hexdigest()
    h["Authorization"] = f"AWS4-HMAC-SHA256 Credential={AK}/{cs}, SignedHeaders={sh}, Signature={sig}"
    return h

@router.get("/test/env")
async def test_env():
    """Dump available env vars (safe keys only)."""
    import os
    store_keys = [k for k in os.environ if k.startswith('STORE_')]
    return {'store_env_keys': sorted(store_keys)}

@router.get("/test/finances/{store_env}")
async def test_finances(store_env: str = "STORE_02"):
    """Test: fetch refunds directly from Finances API."""
    cid = os.environ.get(f"{store_env}_LWA_CLIENT_ID", "")
    csec = os.environ.get(f"{store_env}_LWA_CLIENT_SECRET", "")
    ref = os.environ.get(f"{store_env}_REFRESH_TOKEN_AMERICAS", "")
    
    if not cid or not ref:
        return {"error": f"Missing credentials for {store_env}", "env_keys_found": list(os.environ.keys())[:10]}
    
    with httpx.Client(timeout=30.0) as c:
        r = c.post("https://api.amazon.com/auth/o2/token", json={
            "grant_type": "refresh_token", "client_id": cid, "client_secret": csec, "refresh_token": ref,
        })
        if r.status_code != 200:
            return {"error": f"LWA failed: {r.status_code}", "detail": r.text[:100]}
        
        tk = r.json()["access_token"]
        endpoint = "https://sellingpartnerapi-na.amazon.com/finances/v0/financialEvents?PostedAfter=2026-05-01T00:00:00Z&MaxResults=20"
        h = {"x-amz-access-token": tk}
        headers = _sign("GET", endpoint, h, "")
        r2 = c.get(endpoint, headers=headers)
        
        if r2.status_code != 200:
            return {"error": f"Finances API: {r2.status_code}", "detail": r2.text[:200]}
        
        fe = r2.json().get("payload", {}).get("FinancialEvents", {})
        refunds = fe.get("RefundEventList", [])
        total = 0
        for ev in refunds:
            for item in ev.get("ShipmentItemAdjustmentList", []):
                for charge in item.get("ItemChargeAdjustmentList", []):
                    try: total += abs(float(charge.get("ChargeAmount", {}).get("CurrencyAmount", 0)))
                    except: pass
        
        return {
            "status": "ok",
            "store_env": store_env,
            "lwa_status": r.status_code,
            "finances_status": r2.status_code,
            "refund_events": len(refunds),
            "total_refund_amount": round(total, 2),
        }
