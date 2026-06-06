"""
Direct Finances API + Admin endpoints for Railway deployment.
"""
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


@router.post("/admin/ingest-all/{store_id}")
async def ingest_all(store_id: str = "02", background_tasks=None):
    """Ingest all data into DB using background tasks."""
    from fastapi import BackgroundTasks
    import os as os_mod, httpx, hashlib, hmac, time as time_mod, json as json_mod, uuid
    from urllib.parse import urlparse, quote
    from datetime import datetime
    from db.connection import get_session
    from db.models import FinancialTransaction, Refund, Return, Order, OrderItem
    
    AK = os_mod.environ.get("STORE_01_AWS_ACCESS_KEY_ID", "")
    AS = os_mod.environ.get("STORE_01_AWS_SECRET_ACCESS_KEY", "")
    store_prefix = f"STORE_{store_id.upper()}"
    cid = os_mod.environ.get(f"{store_prefix}_LWA_CLIENT_ID", "")
    csec = os_mod.environ.get(f"{store_prefix}_LWA_CLIENT_SECRET", "")
    ref = os_mod.environ.get(f"{store_prefix}_REFRESH_TOKEN_AMERICAS", "")
    
    if not cid or not ref:
        return {"error": "Missing credentials"}
    
    def _sign(m, u, h, b):
        p = urlparse(u); ad = time_mod.strftime("%Y%m%dT%H%M%SZ", time_mod.gmtime()); ds = time_mod.strftime("%Y%m%d", time_mod.gmtime())
        h["x-amz-date"] = ad; h["host"] = p.hostname
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
    
    results = {"store_id": store_id}
    
    with httpx.Client(timeout=60.0) as c:
        # LWA
        r = c.post("https://api.amazon.com/auth/o2/token", json={
            "grant_type": "refresh_token", "client_id": cid, "client_secret": csec, "refresh_token": ref})
        if r.status_code != 200:
            return {"error": f"LWA failed: {r.status_code}"}
        tk = r.json()["access_token"]
        
        # 1. Ingest refunds from Finances API
        h = {"x-amz-access-token": tk}
        h = _sign("GET", "https://sellingpartnerapi-na.amazon.com/finances/v0/financialEvents?PostedAfter=2026-05-01T00:00:00Z&MaxResults=100", h, "")
        r2 = c.get("https://sellingpartnerapi-na.amazon.com/finances/v0/financialEvents?PostedAfter=2026-05-01T00:00:00Z&MaxResults=100", headers=h)
        
        stored = 0
        if r2.status_code == 200:
            fe = r2.json().get("payload", {}).get("FinancialEvents", {})
            session = get_session()
            try:
                for ev in fe.get("RefundEventList", []):
                    oid = ev.get("AmazonOrderId", "")
                    posted = ev.get("PostedDate", "")
                    for item in ev.get("ShipmentItemAdjustmentList", []):
                        sku = item.get("SellerSKU", "")
                        total = 0
                        for charge in item.get("ItemChargeAdjustmentList", []):
                            try: total += abs(float(charge.get("ChargeAmount", {}).get("CurrencyAmount", 0)))
                            except: pass
                        if total == 0: continue
                        rid_val = f"REF-{oid}-{sku}-{uuid.uuid4().hex[:8]}"
                        ft = FinancialTransaction(
                            store_id=store_id, marketplace_id="ATVPDKIKX0DER",
                            transaction_id=rid_val, amazon_order_id=oid, sku=sku,
                            transaction_type="refund", amount=total, currency="USD",
                            posted_date=posted, raw_data=ev)
                        session.add(ft)
                        stored += 1
                session.commit()
            except Exception as e:
                session.rollback()
            finally:
                session.close()
        results["refunds_stored"] = stored
        
        # 2. Get orders from existing report
        report_ids = {"02": "99403020609"}
        rid = report_ids.get(store_id)
        if rid:
            h2 = {"x-amz-access-token": tk}
            h2 = _sign("GET", f"https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/reports/{rid}", h2, "")
            r3 = c.get(f"https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/reports/{rid}", headers=h2)
            if r3.status_code == 200 and "reportDocumentId" in r3.json():
                doc = r3.json()["reportDocumentId"]
                h3 = {"x-amz-access-token": tk}
                h3 = _sign("GET", f"https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/documents/{doc}", h3, "")
                r4 = c.get(f"https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/documents/{doc}", headers=h3)
                doc_data = r4.json()
                doc_url = doc_data.get("url", doc_data.get("documentUrl", ""))
                if not doc_url:
                    results["orders_error"] = "no url"
                else:
                    r5 = c.get(doc_url)
                    lines = r5.text.split("\n")
                    us_orders, us_rev = 0, 0.0
                for line in lines[1:]:
                    if line.strip():
                        cols = line.split("\t")
                        if len(cols) < 16: continue
                        try: price = float(cols[15])
                        except: continue
                        if "Amazon.com" in cols[6]:
                            us_orders += 1; us_rev += price
                results["orders_count"] = us_orders
                results["orders_revenue"] = round(us_rev, 2)
        
        # 3. Ingest settlement reports
        h4 = {"x-amz-access-token": tk}
        h4 = _sign("GET", "https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/reports?reportTypes=GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_V2&processingStatuses=DONE&pageSize=5", h4, "")
        r6 = c.get("https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/reports?reportTypes=GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_V2&processingStatuses=DONE&pageSize=5", headers=h4)
        reports = r6.json().get("reports", [])
        
        total_rev, total_fees, total_ads, total_promos = 0.0, 0.0, 0.0, 0.0
        for rpt in reports:
            sid = rpt.get("reportId", "")
            h5 = {"x-amz-access-token": tk}
            h5 = _sign("GET", f"https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/reports/{sid}", h5, "")
            r7 = c.get(f"https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/reports/{sid}", headers=h5)
            if "reportDocumentId" not in r7.json(): continue
            doc2 = r7.json()["reportDocumentId"]
            h6 = {"x-amz-access-token": tk}
            h6 = _sign("GET", f"https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/documents/{doc2}", h6, "")
            r8 = c.get(f"https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/documents/{doc2}", headers=h6)
            doc_data2 = r8.json()
            doc_url2 = doc_data2.get("url", doc_data2.get("documentUrl", ""))
            if not doc_url2: continue
            r9 = c.get(doc_url2)
            for line in r9.text.split("\n")[1:]:
                if line.strip():
                    cols = line.split("\t")
                    if len(cols) > 14:
                        desc = cols[12].strip()
                        try: amt = float(cols[14].replace(",", "."))
                        except: continue
                        if desc == "ItemPrice": total_rev += amt
                        elif desc == "ItemFees": total_fees += abs(amt)
                        elif desc == "Cost of Advertising": total_ads += abs(amt)
                        elif desc == "Promotion": total_promos += abs(amt)
        
        results["settlement_revenue"] = round(total_rev, 2)
        results["fba_fees"] = round(total_fees, 2)
        results["ads_spend"] = round(total_ads, 2)
        results["promotions"] = round(total_promos, 2)
    
    results["status"] = "ok"
    return results

@router.get("/admin/ingest-settlements/{store_id}")
async def ingest_settlements(store_id: str = "02"):
    """Ingest all settlement data (fees, ads, promos, refunds)."""
    import os as os_mod, httpx, hashlib, hmac, time as time_mod
    from urllib.parse import urlparse, quote
    from collections import Counter
    from db.connection import get_session
    from db.models import FinancialTransaction
    
    AK = os_mod.environ.get("STORE_01_AWS_ACCESS_KEY_ID", "")
    AS = os_mod.environ.get("STORE_01_AWS_SECRET_ACCESS_KEY", "")
    
    def _sign(m, u, h, b):
        p = urlparse(u); ad = time_mod.strftime("%Y%m%dT%H%M%SZ", time_mod.gmtime()); ds = time_mod.strftime("%Y%m%d", time_mod.gmtime())
        h["x-amz-date"] = ad; h["host"] = p.hostname
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
    
    store_prefix = f"STORE_{store_id.upper()}"
    cid = os_mod.environ.get(f"{store_prefix}_LWA_CLIENT_ID", os_mod.environ.get(f"STORE_0{store_id}_LWA_CLIENT_ID", ""))
    csec = os_mod.environ.get(f"{store_prefix}_LWA_CLIENT_SECRET", os_mod.environ.get(f"STORE_0{store_id}_LWA_CLIENT_SECRET", ""))
    ref = os_mod.environ.get(f"{store_prefix}_REFRESH_TOKEN_AMERICAS", os_mod.environ.get(f"STORE_0{store_id}_REFRESH_TOKEN_AMERICAS", ""))
    
    if not cid or not ref:
        return {"error": f"Missing credentials for store {store_id}"}
    
    parsed = []
    with httpx.Client(timeout=30.0) as c:
        r = c.post("https://api.amazon.com/auth/o2/token", json={
            "grant_type": "refresh_token", "client_id": cid, "client_secret": csec, "refresh_token": ref,
        })
        if r.status_code != 200:
            return {"error": f"LWA failed: {r.status_code}"}
        tk = r.json()["access_token"]
        
        # Search for settlement reports
        h = {"x-amz-access-token": tk}
        h = _sign("GET", "https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/reports?reportTypes=GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_V2&processingStatuses=DONE&pageSize=10", h, "")
        r2 = c.get("https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/reports?reportTypes=GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_V2&processingStatuses=DONE&pageSize=10", headers=h)
        reports = r2.json().get("reports", [])
        
        revenue, fees, ads, promos, refund_total = 0.0, 0.0, 0.0, 0.0, 0.0
        tx_count = 0
        
        for rpt in reports[:5]:
            rid = rpt.get("reportId", "")
            h2 = {"x-amz-access-token": tk}
            h2 = _sign("GET", f"https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/reports/{rid}", h2, "")
            r3 = c.get(f"https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/reports/{rid}", headers=h2)
            data = r3.json()
            if not isinstance(data, dict) or 'reportDocumentId' not in data:
                continue
            h3 = {"x-amz-access-token": tk}
            h3 = _sign("GET", f"https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/documents/{data['reportDocumentId']}", h3, "")
            r4 = c.get(f"https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/documents/{data['reportDocumentId']}", headers=h3)
            r5 = c.get(r4.json()["url"])
            lines = r5.text.split("\n")
            
            for line in lines[1:]:
                if line.strip():
                    cols = line.split("\t")
                    if len(cols) > 14:
                        desc = cols[12].strip()
                        try: amt = float(cols[14].replace(",", "."))
                        except: continue
                        if desc == "ItemPrice": revenue += amt; tx_count += 1
                        elif desc == "ItemFees": fees += abs(amt); tx_count += 1
                        elif desc == "Cost of Advertising": ads += abs(amt); tx_count += 1
                        elif desc == "Promotion": promos += abs(amt); tx_count += 1
                        elif "refund" in desc.lower(): refund_total += abs(amt); tx_count += 1
        
        net = revenue - fees - ads - promos - refund_total
        return {
            "status": "ok",
            "store_id": store_id,
            "settlements_scanned": len(reports),
            "gross_revenue": round(revenue, 2),
            "fba_fees": round(fees, 2),
            "ads_spend": round(ads, 2),
            "promotions": round(promos, 2),
            "refunds": round(refund_total, 2),
            "net_after_all": round(net, 2),
        }

@router.get("/test/finances/{store_env}")
async def test_finances(store_env: str = "STORE_02"):
    """Test: fetch refunds directly from Finances API."""
    cid = os.environ.get(f"{store_env}_LWA_CLIENT_ID", "")
    csec = os.environ.get(f"{store_env}_LWA_CLIENT_SECRET", "")
    ref = os.environ.get(f"{store_env}_REFRESH_TOKEN_AMERICAS", "")
    
    if not cid or not ref:
        return {"error": f"Missing credentials for {store_env}"}
    
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


_NEW_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS returns (
    id BIGSERIAL PRIMARY KEY, store_id VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id VARCHAR(32) DEFAULT 'ATVPDKIKX0DER', return_id VARCHAR(128) NOT NULL,
    amazon_order_id VARCHAR(64), sku VARCHAR(255), asin VARCHAR(32),
    return_request_date TIMESTAMPTZ, return_reason TEXT, return_status VARCHAR(64),
    return_quantity INTEGER DEFAULT 0, return_type VARCHAR(64), resolution VARCHAR(64),
    rma_id VARCHAR(128), a_to_z_claim BOOLEAN, in_policy BOOLEAN, is_prime BOOLEAN,
    refunded_amount NUMERIC(12,2), raw_data JSONB, created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (store_id, marketplace_id, return_id)
);
CREATE TABLE IF NOT EXISTS refunds (
    id BIGSERIAL PRIMARY KEY, store_id VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id VARCHAR(32) DEFAULT 'ATVPDKIKX0DER', refund_id VARCHAR(128) NOT NULL,
    amazon_order_id VARCHAR(64), sku VARCHAR(255), refund_amount NUMERIC(12,2) DEFAULT 0,
    currency VARCHAR(8) DEFAULT 'USD', refund_date TIMESTAMPTZ, refund_reason_code VARCHAR(128),
    refund_type VARCHAR(64), posted_date TIMESTAMPTZ, quantity_refunded INTEGER DEFAULT 0,
    fee_adjustment NUMERIC(12,2) DEFAULT 0, return_shipping NUMERIC(12,2) DEFAULT 0,
    tax_amount NUMERIC(12,2) DEFAULT 0, raw_data JSONB, created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (store_id, marketplace_id, refund_id)
);
CREATE TABLE IF NOT EXISTS financial_transactions (
    id BIGSERIAL PRIMARY KEY, store_id VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id VARCHAR(32) DEFAULT 'ATVPDKIKX0DER', transaction_id VARCHAR(128),
    amazon_order_id VARCHAR(64), sku VARCHAR(255), transaction_type VARCHAR(64) NOT NULL,
    transaction_subtype VARCHAR(128), amount NUMERIC(12,2) NOT NULL,
    currency VARCHAR(8) DEFAULT 'USD', posted_date TIMESTAMPTZ,
    settlement_id VARCHAR(64), fee_type VARCHAR(64), fee_amount NUMERIC(12,2),
    raw_data JSONB, created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS customer_loss_events (
    id BIGSERIAL PRIMARY KEY, store_id VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id VARCHAR(32) DEFAULT 'ATVPDKIKX0DER', return_id VARCHAR(128),
    refund_id VARCHAR(128), amazon_order_id VARCHAR(64), sku VARCHAR(255),
    asin VARCHAR(32), loss_type VARCHAR(32) NOT NULL, amount_loss NUMERIC(12,2) DEFAULT 0,
    currency VARCHAR(8) DEFAULT 'USD', reason TEXT, event_date TIMESTAMPTZ,
    linked_to_return BOOLEAN DEFAULT FALSE, linked_to_refund BOOLEAN DEFAULT FALSE,
    is_anomaly BOOLEAN DEFAULT FALSE, anomaly_reason TEXT,
    raw_data JSONB, created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS inventory_loss_events (
    id BIGSERIAL PRIMARY KEY, store_id VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id VARCHAR(32) DEFAULT 'ATVPDKIKX0DER', sku VARCHAR(255),
    fnsku VARCHAR(64), asin VARCHAR(32), event_type VARCHAR(32) NOT NULL,
    quantity INTEGER DEFAULT 0, estimated_value NUMERIC(12,2) DEFAULT 0,
    event_date DATE NOT NULL, reference_id VARCHAR(128), notes TEXT,
    raw_data JSONB, created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS reimbursement_tracking (
    id BIGSERIAL PRIMARY KEY, store_id VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id VARCHAR(32) DEFAULT 'ATVPDKIKX0DER', loss_event_id BIGINT,
    shipment_id VARCHAR(64), sku VARCHAR(255), case_type VARCHAR(32) NOT NULL,
    expected_amount NUMERIC(12,2) DEFAULT 0, received_amount NUMERIC(12,2) DEFAULT 0,
    status VARCHAR(32) DEFAULT 'pending', claim_deadline DATE,
    confidence_score NUMERIC(5,4), filed_date TIMESTAMPTZ, resolved_date TIMESTAMPTZ,
    notes TEXT, raw_data JSONB, created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS recovery_opportunities (
    id BIGSERIAL PRIMARY KEY, store_id VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id VARCHAR(32) DEFAULT 'ATVPDKIKX0DER', category VARCHAR(32) NOT NULL,
    sku VARCHAR(255), estimated_value NUMERIC(12,2) DEFAULT 0,
    confidence_score NUMERIC(5,4), evidence_pack JSONB,
    status VARCHAR(32) DEFAULT 'pending', assigned_to VARCHAR(128),
    deadline DATE, notes TEXT, raw_data JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""


@router.get("/admin/create-tables")
async def create_new_tables():
    """Create only the new tables (18-25)."""
    try:
        from db.connection import get_engine
        from sqlalchemy import text
        engine = get_engine()
        statements = [s.strip() for s in _NEW_TABLES_SQL.split(";") if s.strip() and len(s.strip()) > 10]
        ok, fail = 0, 0
        with engine.connect() as conn:
            for stmt in statements:
                if stmt.upper().startswith("CREATE"):
                    try:
                        conn.execute(text(stmt + ";"))
                        conn.commit()
                        ok += 1
                    except Exception:
                        conn.rollback()
                        fail += 1
        return {"status": "ok", "message": f"{ok} created, {fail} errors"}
    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}


@router.get("/admin/migrate")
async def run_migration():
    """Run database schema migration."""
    try:
        from db.connection import get_engine
        from sqlalchemy import text
        engine = get_engine()
        with open("db/schema.sql") as f:
            text_content = f.read()
        statements = [s.strip() for s in text_content.split(";") if s.strip()]
        success, errors = 0, 0
        with engine.connect() as conn:
            for stmt in statements:
                if stmt and not stmt.startswith("--") and len(stmt) > 5:
                    if stmt.upper() in ("BEGIN", "COMMIT"):
                        continue
                    if any(kw in stmt.upper() for kw in ["CREATE TABLE", "CREATE INDEX", "CREATE OR REPLACE VIEW", "ALTER", "CREATE TRIGGER", "CREATE FUNCTION"]):
                        try:
                            conn.execute(text(stmt + ";"))
                            conn.commit()
                            success += 1
                        except Exception:
                            conn.rollback()
                            errors += 1
        return {"status": "ok", "message": f"{success} created, {errors} skipped"}
    except Exception as e:
        return {"status": "error", "message": str(e)[:300]}
