"""
FBA Fee Overcharge Scanner
Compares Amazon's actual FBA fees (from Finances API) against the 2026 rate card.
Identifies overcharges due to unverified package dimensions.

Usage: python3 fba_fee_scanner.py
"""
import json, math, os, sys
import httpx, hashlib, hmac, time
from pathlib import Path
from urllib.parse import urlparse, quote
from collections import defaultdict

# ── Rate Card (2026 US) ────────────────────────────────────────────────────

def us_fee(l_cm, w_cm, h_cm, kg, price=30):
    """
    Calculate expected US FBA fulfillment fee using 2026 rate card.
    Source: 2026年亚马逊各站点配送费标准.xlsx → 新us 计费标准
    """
    lb = kg * 2.20462
    lb_ceil = max(math.ceil(lb), 1)
    longest = max(l_cm, w_cm, h_cm) / 2.54
    median = sorted([l_cm, w_cm, h_cm])[1] / 2.54
    shortest = min(l_cm, w_cm, h_cm) / 2.54
    
    tier = 0 if price < 10 else (2 if price > 50 else 1)
    
    # Small Standard: ≤15×12×0.75in, ≤1lb
    if longest <= 15 and median <= 12 and shortest <= 0.75 and lb <= 1:
        oz = lb * 16
        for cap, rates in [(4, [2.92, 3.74, 4.00]), (8, [3.14, 3.96, 4.22]),
                            (12, [3.38, 4.20, 4.46]), (16, [3.78, 4.60, 4.86])]:
            if oz <= cap: return rates[tier]
    
    # Large Standard: ≤18×14×8in, ≤20lb
    if longest <= 18 and median <= 14 and shortest <= 8 and lb <= 20:
        if lb_ceil <= 3:
            oz = lb * 16
            tiers_3lb = [(4, [2.92, 3.74, 4.00]), (8, [3.14, 3.96, 4.22]),
                         (12, [3.38, 4.20, 4.46]), (16, [3.78, 4.60, 4.86]),
                         (20, [4.22, 5.04, 5.30]), (24, [4.60, 5.42, 5.68]),
                         (28, [4.76, 5.58, 5.84]), (32, [5.02, 5.84, 6.10]),
                         (36, [5.12, 5.94, 6.20]), (40, [5.30, 6.12, 6.38]),
                         (44, [5.47, 6.29, 6.55]), (48, [5.87, 6.69, 6.95])]
            for cap, rates in tiers_3lb:
                if oz <= cap: return rates[tier]
        base = [6.15, 6.97, 7.23][tier]
        extra_4oz = math.ceil(max(0, lb_ceil - 3) * 16 / 4)
        return round(base + extra_4oz * 0.08, 2)
    
    # Small Oversize: ≤37×28×20in, ≤50lb
    if longest <= 37 and median <= 28 and shortest <= 20 and lb <= 50:
        return round([6.78, 7.55, 7.55][tier] + max(0, lb_ceil - 1) * 0.38, 2)
    
    # Large Oversize: ≤59×33×33in, ≤50lb
    if longest <= 59 and median <= 33 and shortest <= 33 and lb <= 50:
        return round([8.58, 9.35, 9.35][tier] + max(0, lb_ceil - 1) * 0.38, 2)
    
    # Special Oversize
    return round([25.56, 26.33, 26.33][tier] + max(0, lb_ceil - 1) * 0.38, 2)


# ── SP-API Auth Helpers ────────────────────────────────────────────────────

def load_env():
    env = Path(__file__).parent / ".env"
    for line in env.read_text().splitlines():
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

def sign_request(method, url, headers, body, store_key="01", region="us-east-1"):
    ak = os.environ[f"STORE_{store_key}_AWS_ACCESS_KEY_ID"]
    sk = os.environ[f"STORE_{store_key}_AWS_SECRET_ACCESS_KEY"]
    p = urlparse(url)
    amz_date = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    date_stamp = time.strftime("%Y%m%d", time.gmtime())
    headers["x-amz-date"] = amz_date
    headers["host"] = p.hostname
    canonical_uri = quote(p.path, safe="/") or "/"
    canonical_headers = "".join(f"{k.lower()}:{v.strip()}\n" for k, v in sorted(headers.items()) if k.lower() != "authorization")
    signed_headers = ";".join(sorted(k.lower() for k in headers if k.lower() != "authorization"))
    payload_hash = hashlib.sha256(body.encode()).hexdigest()
    canonical_request = "\n".join([method.upper(), canonical_uri, p.query, canonical_headers, signed_headers, payload_hash])
    credential_scope = f"{date_stamp}/{region}/execute-api/aws4_request"
    string_to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, credential_scope, hashlib.sha256(canonical_request.encode()).hexdigest()])
    
    def sign(key, msg):
        if isinstance(key, str): key = key.encode()
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()
    
    k1 = sign(f"AWS4{sk}", date_stamp)
    k2 = sign(k1, region)
    k3 = sign(k2, "execute-api")
    k4 = sign(k3, "aws4_request")
    signature = hmac.new(k4, string_to_sign.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = f"AWS4-HMAC-SHA256 Credential={ak}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"
    return headers


def get_token(client_id, client_secret, refresh_token):
    r = httpx.post("https://api.amazon.com/auth/o2/token", json={
        "grant_type": "refresh_token", "client_id": client_id,
        "client_secret": client_secret, "refresh_token": refresh_token
    }, timeout=10)
    return r.json()["access_token"]


# ── Scanner ────────────────────────────────────────────────────────────────

def pull_actual_fees(store_name, store_num, month="2026-05", host="sellingpartnerapi-na.amazon.com"):
    """Pull actual FBA fees from Finances API for a store/month."""
    cid = os.environ[f"STORE_{store_num}_LWA_CLIENT_ID"]
    csec = os.environ[f"STORE_{store_num}_LWA_CLIENT_SECRET"]
    ref = os.environ[f"STORE_{store_num}_REFRESH_TOKEN_AMERICAS"]
    
    tk = get_token(cid, csec, ref)
    base_url = f"https://{host}/finances/v0/financialEvents"
    params = {"PostedAfter": f"{month}-01T00:00:00Z", "PostedBefore": f"{month}-31T23:59:59Z", "MaxResults": 100}
    
    fee_data, pages = [], 0
    while pages < 30:
        q = "&".join(f"{k}={v}" for k, v in params.items())
        h = sign_request("GET", f"{base_url}?{q}", {"x-amz-access-token": tk}, "")
        rr = httpx.get(f"{base_url}?{q}", headers=h, timeout=30)
        pages += 1
        if rr.status_code != 200: break
        
        evs = rr.json().get("payload", {}).get("FinancialEvents", {})
        for s in evs.get("ShipmentEventList", []):
            mp = s.get("MarketplaceName", "")
            if "amazon.com" not in mp.lower(): continue
            for item in s.get("ShipmentItemList", []):
                sku = item.get("SellerSKU", "").strip()
                fba_fee = 0
                for fee in item.get("ItemFeeList", []):
                    ft = fee.get("FeeType", "")
                    amt = abs(fee.get("FeeAmount", {}).get("CurrencyAmount", 0))
                    if ft in ("FBAPerUnitFulfillmentFee", "FBAWeightBasedFee"):
                        fba_fee += amt
                price = 0
                for ch in item.get("ItemChargeList", []):
                    if ch.get("ChargeType") == "Principal":
                        price = abs(ch.get("ChargeAmount", {}).get("CurrencyAmount", 0))
                        break
                if fba_fee > 0 and sku:
                    fee_data.append({"sku": sku, "fee": fba_fee, "price": price or 30})
        
        nxt = rr.json().get("payload", {}).get("NextToken", "")
        if not nxt: break
        params = {"NextToken": nxt}
        time.sleep(0.3)
    
    print(f"  Pulled {len(fee_data)} fee lines from {pages} pages")
    return fee_data


def scan(store_name, store_num, products, sku_asin_map, month="2026-05"):
    """Run the full scan for one store."""
    print(f"\n{'='*60}")
    print(f"FBA Fee Scanner — {store_name}")
    print(f"{'='*60}")
    
    fee_data = pull_actual_fees(store_name, store_num, month)
    
    # Group by SKU
    by_sku = defaultdict(list)
    for f in fee_data:
        by_sku[f["sku"]].append(f)
    
    # Compare
    results = []
    total_overcharge = 0
    total_units = 0
    
    print(f"\n{'SKU':<25} {'#':>5} {'Exp':>7} {'Act':>7} {'Δ/u':>7} {'Δ/mo':>8}")
    print("-" * 70)
    
    for sku, items in sorted(by_sku.items(), key=lambda x: len(x[1]), reverse=True):
        asin = sku_asin_map.get(sku)
        prod = products.get(asin) if asin else None
        if not prod: continue
        
        avg_price = sum(f['price'] for f in items) / len(items)
        expected = us_fee(prod['l'], prod['w'], prod['h'], prod['kg'], avg_price)
        actuals = [f['fee'] for f in items]
        avg_actual = sum(actuals) / len(actuals)
        
        diff = expected - avg_actual
        mo_overcharge = diff * len(items) if diff < 0 else 0
        
        total_overcharge += mo_overcharge
        total_units += len(items)
        
        flag = "🔴" if diff < -0.05 else "✅"
        print(f"{flag} {sku:<23} {len(items):>5} ${expected:>6.2f} ${avg_actual:>6.2f} ${diff:>+6.2f} ${mo_overcharge:>+7.0f}")
        
        results.append({
            "sku": sku, "asin": asin, "count": len(items),
            "expected": expected, "actual": avg_actual,
            "diff_per_unit": round(diff, 2), "monthly_overcharge": round(mo_overcharge, 2)
        })
    
    print("-" * 70)
    print(f"  Total overcharge: ${total_overcharge:,.0f}/month → ~${total_overcharge*12:,.0f}/year")
    print(f"  Across {total_units} units")
    
    return results, total_overcharge


if __name__ == "__main__":
    load_env()
    print("FBA Fee Overcharge Scanner — ready")
    print("Run scan() from import or extend with store-specific calls")
