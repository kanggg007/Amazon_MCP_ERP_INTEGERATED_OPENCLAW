"""Inventory Turnover Monitor — calculates days of inventory per ASIN using FBA Inventory API."""
import os, httpx, psycopg2
from datetime import datetime, timedelta

HOSTS = {'NA': 'sellingpartnerapi-na.amazon.com', 'FE': 'sellingpartnerapi-fe.amazon.com', 'EU': 'sellingpartnerapi-eu.amazon.com'}

STORES = {
    'CUCZUUS': {'num': '02', 'seller_id': 'AXW589ZKKDJNM', 'markets': [
        ('NA', 'US', 'ATVPDKIKX0DER'), ('NA', 'CA', 'A2EUQ1WTGCTBG2'),
        ('FE', 'AU', 'A39IBJ37TRP1C6'), ('EU', 'DE', 'A1PA6795UKMFR9'),
        ('FE', 'JP', 'A1VC38T7YXB528'),
    ]},
    'BOOLUU': {'num': '03', 'seller_id': 'A8P0GUSN1BWCC', 'markets': [
        ('NA', 'US', 'ATVPDKIKX0DER'), ('NA', 'CA', 'A2EUQ1WTGCTBG2'),
        ('FE', 'AU', 'A39IBJ37TRP1C6'), ('FE', 'JP', 'A1VC38T7YXB528'),
    ]},
    'Heliumx': {'num': '04', 'seller_id': 'AD4UKHMR5K35J', 'markets': [
        ('NA', 'US', 'ATVPDKIKX0DER'), ('NA', 'CA', 'A2EUQ1WTGCTBG2'),
        ('FE', 'AU', 'A39IBJ37TRP1C6'), ('EU', 'DE', 'A1PA6795UKMFR9'),
    ]},
}

def check_inventory_turnover():
    """Check FBA inventory levels and compute days of supply per ASIN."""
    dsn = os.environ.get('DATABASE_URL', '')
    if not dsn:
        return {"error": "no DB"}
    
    conn = psycopg2.connect(dsn, connect_timeout=10)
    cur = conn.cursor()
    
    # Ensure table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS inventory_health (
            asin VARCHAR(15), marketplace VARCHAR(5), store VARCHAR(10),
            available_qty INT, reserved_qty INT, total_qty INT,
            daily_sales_avg DECIMAL(8,2), days_of_inventory DECIMAL(8,1),
            alert_level VARCHAR(10),
            checked_at TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (asin, marketplace, store)
        )
    """)
    conn.commit()
    
    results = {}
    alerts = []
    
    for store_name, cfg in STORES.items():
        snum = cfg['num']
        cid = os.environ.get(f'STORE_{snum}_LWA_CLIENT_ID', '')
        csec = os.environ.get(f'STORE_{snum}_LWA_CLIENT_SECRET', '')
        if not cid:
            continue
        
        for region, mkt_name, mid in cfg['markets']:
            ref_key = {'NA': 'AMERICAS', 'FE': 'AU', 'EU': 'EU'}[region]
            ref = os.environ.get(f'STORE_{snum}_REFRESH_TOKEN_{ref_key}', '')
            if not ref:
                continue
            if region == 'FE' and mkt_name == 'JP' and not os.environ.get(f'STORE_{snum}_REFRESH_TOKEN_JP'):
                continue  # JP needs separate token
            
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
            
            # Get FBA inventory summaries
            try:
                inv_url = f'https://{host}/fba/inventory/v1/summaries?marketplaceIds={mid}&granularityType=Marketplace&granularityId={mid}'
                r2 = httpx.get(inv_url, headers={'x-amz-access-token': tk}, timeout=30)
                if r2.status_code != 200:
                    continue
                
                data = r2.json()
                summaries = data.get('payload', data).get('inventorySummaries', [])
                
                for item in summaries:
                    asin = item.get('asin', '')
                    if not asin:
                        continue
                    
                    inventory = item.get('inventoryDetails', {})
                    total = inventory.get('totalQuantity', 0) or 0
                    available = inventory.get('fulfillableQuantity', 0) or 0
                    
                    # Get sales velocity from orders DB
                    cutoff = (datetime.now() - timedelta(days=14)).strftime('%Y-%m-%d')
                    cur.execute("""
                        SELECT COUNT(*), COALESCE(SUM(oi.quantity), 0), MAX(oa.purchase_date)
                        FROM orders_active oa JOIN order_items_active oi ON oa.order_id = oi.order_id
                        WHERE oi.asin = %s AND oa.store = %s AND oa.purchase_date > %s
                    """, (asin, store_name, cutoff))
                    r3 = cur.fetchone()
                    order_count = r3[0] if r3 else 0
                    units_sold = r3[1] if r3 else 0
                    daily_sales = units_sold / 14.0 if units_sold > 0 else 0
                    
                    days_of_inventory = total / daily_sales if daily_sales > 0 else (999 if total > 0 else 0)
                    
                    # Alert levels
                    if total == 0:
                        alert = 'OUT_OF_STOCK'
                        alerts.append(f"🚨 {store_name} {mkt_name} {asin}: OUT OF STOCK")
                    elif days_of_inventory < 14:
                        alert = 'LOW'
                        alerts.append(f"⚠️ {store_name} {mkt_name} {asin}: {days_of_inventory:.0f} days left ({total} units)")
                    elif days_of_inventory < 30:
                        alert = 'WATCH'
                    else:
                        alert = 'HEALTHY'
                    
                    cur.execute("""
                        INSERT INTO inventory_health (asin, marketplace, store, available_qty, reserved_qty, total_qty, daily_sales_avg, days_of_inventory, alert_level, checked_at)
                        VALUES (%s, %s, %s, %s, 0, %s, %s, %s, %s, NOW())
                        ON CONFLICT (asin, marketplace, store) DO UPDATE SET
                            available_qty = EXCLUDED.available_qty,
                            total_qty = EXCLUDED.total_qty,
                            daily_sales_avg = EXCLUDED.daily_sales_avg,
                            days_of_inventory = EXCLUDED.days_of_inventory,
                            alert_level = EXCLUDED.alert_level,
                            checked_at = NOW()
                    """, (asin, mkt_name, store_name, available, total,
                          round(daily_sales, 2), round(days_of_inventory, 1), alert))
                    
                    if store_name not in results:
                        results[store_name] = {}
                    results[store_name][f"{mkt_name}/{asin}"] = {
                        'total': total, 'daily_sales': round(daily_sales, 2), 'days': round(days_of_inventory, 1), 'alert': alert
                    }
            except Exception as e:
                print(f"  {store_name}/{mkt_name}: {e}", flush=True)
                continue
    
    conn.commit()
    conn.close()
    
    return {
        "status": "ok",
        "products_checked": sum(len(v) for v in results.values()),
        "alerts": alerts if alerts else ["All products healthy"],
        "summary": {store: {
            'total': len(items),
            'out_of_stock': sum(1 for i in items.values() if i['alert'] == 'OUT_OF_STOCK'),
            'low': sum(1 for i in items.values() if i['alert'] == 'LOW'),
            'healthy': sum(1 for i in items.values() if i['alert'] == 'HEALTHY'),
        } for store, items in results.items()}
    }

# Quick test function  
def quick_check(store, marketplace):
    """Quick check for a single store/marketplace."""
    import json
    result = check_inventory_turnover()
    print(json.dumps(result, indent=2, default=str))
    return result

if __name__ == '__main__':
    quick_check('CUCZUUS', 'US')
