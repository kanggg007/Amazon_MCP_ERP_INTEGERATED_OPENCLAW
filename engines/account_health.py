"""Account Health Engine — polls Sellers API for suspended listings per store/marketplace."""
import os, httpx, psycopg2

HOSTS = {'NA': 'sellingpartnerapi-na.amazon.com', 'FE': 'sellingpartnerapi-fe.amazon.com', 'EU': 'sellingpartnerapi-eu.amazon.com'}

STORES = {
    'CUCZUUS': {'num': '02', 'regions': ['NA', 'FE', 'EU']},
    'BOOLUU': {'num': '03', 'regions': ['NA', 'FE']},
    'Heliumx': {'num': '04', 'regions': ['NA', 'FE', 'EU']},
    'WOC': {'num': '05', 'regions': ['NA', 'FE']},
}

def check_all_stores_health():
    """Check all stores for suspended listings. Returns alert string or empty."""
    dsn = os.environ.get('DATABASE_URL', '')
    if not dsn:
        return "no DB"
    
    issues = []
    conn = psycopg2.connect(dsn, connect_timeout=10)
    cur = conn.cursor()
    
    for store_name, cfg in STORES.items():
        snum = cfg['num']
        cid = os.environ.get(f'STORE_{snum}_LWA_CLIENT_ID', '')
        csec = os.environ.get(f'STORE_{snum}_LWA_CLIENT_SECRET', '')
        if not cid:
            continue
        
        for region in cfg['regions']:
            ref_key = {'NA': 'AMERICAS', 'FE': 'AU', 'EU': 'EU'}[region]
            ref = os.environ.get(f'STORE_{snum}_REFRESH_TOKEN_{ref_key}', '')
            if not ref:
                continue
            
            # Get LWA token
            try:
                r = httpx.post('https://api.amazon.com/auth/o2/token',
                    json={'grant_type': 'refresh_token', 'client_id': cid,
                          'client_secret': csec, 'refresh_token': ref}, timeout=15)
                if r.status_code != 200:
                    continue
                tk = r.json()['access_token']
            except:
                continue
            
            host = HOSTS[region]
            try:
                r2 = httpx.get(f'https://{host}/sellers/v1/marketplaceParticipations',
                    headers={'x-amz-access-token': tk}, timeout=15)
                if r2.status_code != 200:
                    continue
                
                for entry in r2.json().get('payload', []):
                    mp = entry.get('marketplace', {})
                    mid = mp.get('id', '')
                    country = mp.get('countryCode', '?')
                    name = mp.get('name', '?')
                    participation = entry.get('participation', {})
                    suspended = participation.get('hasSuspendedListings', False)
                    
                    # Store in DB
                    if mid and 'Non-Amazon' not in name:
                        cur.execute("""
                            INSERT INTO account_health (store, marketplace_id, marketplace_name, country, has_suspended_listings, checked_at)
                            VALUES (%s, %s, %s, %s, %s, NOW())
                        """, (store_name, mid, name, country, suspended))
                        
                        if suspended:
                            issues.append(f"⚠️ {store_name} {country}: SUSPENDED")
                        else:
                            pass  # All good
            except:
                continue
    
    conn.commit()
    conn.close()
    
    if issues:
        return " | ".join(issues)
    return "✅ All stores healthy"

# Ensure table exists on import
def _ensure_table():
    dsn = os.environ.get('DATABASE_URL', '')
    if not dsn:
        return
    try:
        conn = psycopg2.connect(dsn, connect_timeout=5)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS account_health (
                id SERIAL PRIMARY KEY,
                store VARCHAR(10),
                marketplace_id VARCHAR(20),
                marketplace_name VARCHAR(50),
                country VARCHAR(5),
                has_suspended_listings BOOLEAN,
                checked_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()
        conn.close()
    except:
        pass

_ensure_table()
