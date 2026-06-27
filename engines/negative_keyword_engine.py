"""
Negative Keyword Engine — Auto-discovers & blocks irrelevant search terms.
Runs after daily ingestion. Only touches AUTO campaigns. Conservative rules.
"""
import os, json, time, httpx
from datetime import datetime, timedelta
from collections import defaultdict

# Product keywords — terms that make a search term RELEVANT (don't negate)
PRODUCT_KEYWORDS = {
    "mirror", "bathroom", "vanity", "led", "light", "backlit", "glass",
    "fish", "tank", "aquarium", "lid", "cover", "hood", "terrarium",
    "soap", "dispenser", "pump", "lotion", "shampoo", "conditioner",
    "gym", "fitness", "workout", "exercise", "yoga", "home", "gym",
    "door", "cabinet", "frame", "frameless", "round", "rectangle",
    "beveled", "polished", "decorative", "wall", "hanging", "mounted",
    "magnifying", "hardware", "iron", "steel", "aluminum",
}

# Minimum spend and days before considering negative
MIN_SPEND = 5.0          # $5 minimum total spend
MIN_DAYS = 7             # Over last 7 days
ZERO_SALES = True        # Must have 0 sales

def load_search_terms():
    """Load search term data from PostgreSQL."""
    import psycopg2
    DSN = os.environ.get("DATABASE_URL", "")
    if not DSN: return []
    
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    
    # Get last 7 days of search terms
    start = (datetime.now() - timedelta(days=MIN_DAYS)).strftime("%Y-%m-%d")
    cur.execute("""
        SELECT store, market, data 
        FROM ads_daily 
        WHERE report_type = 'sp_search_terms' 
        AND date >= %s
    """, (start,))
    
    all_terms = []
    for store, market, data_json in cur.fetchall():
        rows = json.loads(data_json) if isinstance(data_json, str) else data_json
        for r in rows:
            if isinstance(r, dict):
                all_terms.append({
                    "store": store,
                    "market": market,
                    "searchTerm": r.get("searchTerm", ""),
                    "keyword": r.get("keyword", ""),
                    "cost": float(r.get("cost", 0)),
                    "clicks": int(r.get("clicks", 0)),
                    "sales1d": float(r.get("sales1d", 0)),
                    "campaignName": r.get("campaignName", ""),
                })
    
    conn.close()
    return all_terms

def is_negative_candidate(term):
    """Check if a search term should be negatived."""
    st = term["searchTerm"].lower().strip()
    
    # Rule 1: Must have spend over threshold
    if term["cost"] < MIN_SPEND:
        return False
    
    # Rule 2: Must have zero sales
    if ZERO_SALES and term["sales1d"] > 0:
        return False
    
    # Rule 3: No clicks → no engagement, not a real issue yet
    if term["clicks"] == 0:
        return False
    
    # Rule 4: Must NOT contain any product keywords
    has_product_word = False
    for word in PRODUCT_KEYWORDS:
        if word in st:
            has_product_word = True
            break
    
    if has_product_word:
        return False
    
    # Rule 5: Must be from an AUTO campaign
    if "自动" not in term["campaignName"] and "auto" not in term["campaignName"].lower():
        return False
    
    return True

def find_negatives():
    """Find all negative keyword candidates."""
    terms = load_search_terms()
    if not terms:
        return [], "No search term data available"
    
    # Aggregate by search term
    grouped = defaultdict(lambda: {"cost": 0, "clicks": 0, "sales": 0, "campaigns": set(), "markets": set()})
    for t in terms:
        st = t["searchTerm"].strip().lower()
        grouped[st]["cost"] += t["cost"]
        grouped[st]["clicks"] += t["clicks"]
        grouped[st]["sales"] += t["sales1d"]
        grouped[st]["campaigns"].add(t["campaignName"])
        grouped[st]["markets"].add(t["market"])
    
    # Find candidates
    candidates = []
    for st, data in grouped.items():
        t = {
            "searchTerm": st,
            "cost": data["cost"],
            "clicks": data["clicks"],
            "sales1d": data["sales"],
            "campaignName": ", ".join(data["campaigns"]),
        }
        if is_negative_candidate(t):
            candidates.append(t)
    
    # Sort by cost (most wasteful first)
    candidates.sort(key=lambda x: -x["cost"])
    return candidates, None

def add_negatives_via_mcp(store, market, negatives, profile_id, dry_run=True):
    """Add negative keywords via MCP."""
    if dry_run:
        print(f"DRY RUN: Would add {len(negatives)} negatives for {store}/{market}")
        for n in negatives[:10]:
            print(f"  {n['searchTerm']} (${n['cost']:.2f} wasted)")
        return
    
    # Get MCP token
    cid = os.environ[f"STORE_{store}_ADS_CLIENT_ID"]
    csec = os.environ[f"STORE_{store}_ADS_CLIENT_SECRET"]
    ref = os.environ[f"STORE_{store}_ADS_REFRESH_TOKEN"]
    
    r = httpx.post("https://api.amazon.com/auth/o2/token",
        json={"grant_type": "refresh_token", "client_id": cid, "client_secret": csec, "refresh_token": ref}, timeout=10)
    tk = r.json()["access_token"]
    
    hdrs = {
        "Amazon-Ads-ClientId": cid,
        "Authorization": f"Bearer {tk}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json"
    }
    
    # Init MCP session
    init = httpx.post("https://advertising-ai.amazon.com/mcp", headers=hdrs,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "neg-engine", "version": "1"}}}, timeout=15)
    
    sid = init.headers.get("mcp-session-id", "")
    hdrs["mcp-session-id"] = sid
    httpx.post("https://advertising-ai.amazon.com/mcp", headers=hdrs,
        json={"jsonrpc": "2.0", "id": 2, "method": "notifications/initialized", "params": {}}, timeout=15)
    
    added = 0
    for n in negatives[:20]:  # Max 20 at a time
        args = {
            "body": {
                "accessRequestedAccount": {"profileId": profile_id},
                "adProduct": "SPONSORED_PRODUCTS",
                "targetType": "NEGATIVE_KEYWORD",
                "keywordText": n["searchTerm"],
                "matchType": "NEGATIVE_EXACT",
                # Need campaign ID — find from campaign name
            }
        }
        
        r3 = httpx.post("https://advertising-ai.amazon.com/mcp", headers=hdrs,
            json={"jsonrpc": "2.0", "id": 3 + added, "method": "tools/call",
                  "params": {"name": "campaign_management-create_target", "arguments": args}}, timeout=15)
        
        result = r3.json()
        ok = not result.get("result", {}).get("isError", False)
        if ok: added += 1
    
    print(f"Added {added}/{len(negatives)} negative keywords")

if __name__ == "__main__":
    candidates, err = find_negatives()
    if err:
        print(f"No data: {err}")
    elif not candidates:
        print("No negative keyword candidates found — all good!")
    else:
        print(f"Found {len(candidates)} negative keyword candidates:")
        total_waste = sum(c["cost"] for c in candidates)
        print(f"Total wasted spend: ${total_waste:.2f} over {MIN_DAYS} days")
        print()
        for c in candidates[:20]:
            print(f"  {c['searchTerm'][:40]} | ${c['cost']:.2f} | {c['clicks']} clicks | {c['campaignName'][:30]}")
        if len(candidates) > 20:
            print(f"  ... and {len(candidates)-20} more")
