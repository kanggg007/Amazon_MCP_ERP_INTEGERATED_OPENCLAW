"""
FBA Restock Advisor — combines sales velocity + inventory + in-transit + lead times
to suggest what needs to be reordered.

Endpoint: GET /admin/inbound/suggest-restock?store=CUCZUUS
"""

import os, psycopg2
from datetime import datetime, timedelta

TRANSIT_TIMES = {'US': 25, 'CA': 25, 'AU': 15, 'JP': 10, 'DE': 35, 'MX': 20, 'BR': 30}
PRODUCTION_WEEKS = 4  # Default manufacturing lead time
BUFFER_WEEKS = 2       # Safety buffer
WEEKS_TO_COVER = 12    # How many weeks of stock to send at once

def suggest_restock(store=None):
    """
    Analyze all ASINs and suggest restock quantities.
    
    Returns list of recommendations:
    {
        "asin": "B0FXGKT7D8",
        "store": "CUCZUUS",
        "marketplace": "US",
        "weekly_sales": 12.5,
        "in_warehouse": 45,
        "in_transit": 100,
        "arriving_soon": 50,          # will arrive within 2 weeks
        "weeks_of_stock": 3.6,
        "transit_weeks": 3.6,         # China → US = ~25 days
        "total_lead_weeks": 9.6,      # transit + production + buffer
        "is_critical": False,         # stock < buffer
        "is_low": True,               # stock < lead time
        "suggested_order": 120,       # units to send now
        "urgency": "ORDER_NOW"        # OK | ORDER_SOON | ORDER_NOW | CRITICAL
    }
    """
    dsn = os.environ.get('DATABASE_URL', '')
    if not dsn:
        # Try .env file
        try:
            BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            env_path = os.path.join(BASE, '.env')
            if os.path.exists(env_path):
                for line in open(env_path).read().splitlines():
                    if 'DATABASE_URL' in line:
                        dsn = line.split('=', 1)[1].strip()
        except: pass
    if not dsn:
        return {"error": "no DB"}
    
    conn = psycopg2.connect(dsn, connect_timeout=10)
    cur = conn.cursor()
    
    # 1. Get active ASINs per store/market from orders
    where_store = f"AND oa.store = '{store}'" if store else ""
    cur.execute(f"""
        SELECT oa.store, oa.marketplace, oi.asin,
               COUNT(*) as order_count,
               SUM(oi.quantity) as units_sold,
               MAX(oa.purchase_date) as last_order,
               AVG(oi.unit_price) as avg_price
        FROM orders_active oa
        JOIN order_items_active oi ON oa.order_id = oi.order_id
        WHERE oa.purchase_date > NOW() - INTERVAL '28 days'
        {where_store}
        GROUP BY oa.store, oa.marketplace, oi.asin
        HAVING SUM(oi.quantity) >= 3  -- At least 3 units sold in 28 days
        ORDER BY oa.store, oa.marketplace, units_sold DESC
    """)
    
    products = [
        {'store': r[0], 'marketplace': r[1], 'asin': r[2],
         'order_count': r[3], 'units_28d': r[4], 'last_order': r[5], 'avg_price': float(r[6] or 0)}
        for r in cur.fetchall()
    ]
    
    if not products:
        conn.close()
        return {"suggestions": [], "message": "No active products found"}
    
    # 2. Get FBA inventory levels
    cur.execute("""
        SELECT asin, marketplace, SUM(total_qty) as total, SUM(available_qty) as available
        FROM inventory_health
        GROUP BY asin, marketplace
    """)
    inventory = {}
    for r in cur.fetchall():
        inventory[(r[0], r[1])] = {'total': r[2] or 0, 'available': r[3] or 0}
    
    # 3. Get in-transit shipments via inbound tracker
    in_transit = {}
    arriving_soon = {}
    try:
        from engines.inbound_tracker import track_inbound_shipments
        tracking = track_inbound_shipments(store)
        for s in tracking.get('in_transit', []):
            asin = s.get('asin', '?')
            if asin == '?' or not asin:
                continue
            mkt = s.get('marketplace', '')
            st = s.get('store', '')
            qty = s.get('units', 0)
            key = (asin, mkt, st)
            in_transit[key] = in_transit.get(key, 0) + qty
            
            # Arriving soon: within 2 weeks
            if s.get('alert') in ('ARRIVING_SOON', 'OVERDUE'):
                arriving_soon[key] = arriving_soon.get(key, 0) + qty
    except Exception as e:
        print(f"  (inbound tracker: {e})", flush=True)
    
    # 4. Get product lead times from product_cost_master
    cur.execute("""
        SELECT asin, marketplace, COALESCE(lead_time_days, 30) as lead_time,
               COALESCE(transit_time_days, 0) as transit_time,
               COALESCE(safety_buffer_days, 7) as buffer
        FROM product_cost_master
    """)
    lead_times = {}
    for r in cur.fetchall():
        lead_times[(r[0], r[1])] = {'lead_days': r[2], 'transit_days': r[3], 'buffer_days': r[4]}
    
    conn.close()
    
    # 5. Compute recommendations for each product
    suggestions = []
    
    for p in products:
        store_name = p['store']
        mkt = p['marketplace']
        asin = p['asin']
        
        weekly_sales = p['units_28d'] / 4.0  # 28 days → weekly
        
        # Inventory
        inv = inventory.get((asin, mkt), {'total': 0, 'available': 0})
        in_warehouse = inv['available']
        
        # In transit
        key = (asin, mkt, store_name)
        transit_qty = in_transit.get(key, 0)
        arriving_qty = arriving_soon.get(key, 0)
        
        # Lead times
        lt = lead_times.get((asin, mkt), {})
        transit_days = lt.get('transit_days') or TRANSIT_TIMES.get(mkt, 25)
        production_days = lt.get('lead_days') or (PRODUCTION_WEEKS * 7)
        buffer_days = lt.get('buffer_days') or (BUFFER_WEEKS * 7)
        
        transit_weeks = transit_days / 7.0
        production_weeks = production_days / 7.0
        buffer_weeks = buffer_days / 7.0
        
        # Total lead time (from order to arriving at warehouse)
        total_lead_weeks = production_weeks + transit_weeks + buffer_weeks
        
        # Effective stock = in warehouse + arriving within 2 weeks
        effective_stock = in_warehouse + arriving_qty
        
        # Weeks of stock at current sales rate
        weeks_of_stock = effective_stock / weekly_sales if weekly_sales > 0 else 999
        
        # Decision logic
        if weekly_sales <= 0:
            urgency = "NO_SALES"
            suggested = 0
        elif weeks_of_stock <= buffer_weeks:
            urgency = "CRITICAL"
            suggested = round(weekly_sales * (total_lead_weeks + WEEKS_TO_COVER))  # replenish + cover 12 weeks
        elif weeks_of_stock <= total_lead_weeks:
            urgency = "ORDER_NOW"
            suggested = round(weekly_sales * (total_lead_weeks + WEEKS_TO_COVER))
        elif weeks_of_stock <= total_lead_weeks * 2:
            urgency = "ORDER_SOON"
            suggested = round(weekly_sales * WEEKS_TO_COVER)
        else:
            urgency = "OK"
            suggested = 0
        
        suggestions.append({
            'asin': asin,
            'store': store_name,
            'marketplace': mkt,
            'weekly_sales': round(weekly_sales, 1),
            'in_warehouse': in_warehouse,
            'in_transit_total': transit_qty,
            'arriving_soon': arriving_qty,
            'effective_stock': effective_stock,
            'weeks_of_stock': round(weeks_of_stock, 1),
            'transit_weeks': round(transit_weeks, 1),
            'production_weeks': round(production_weeks, 1),
            'buffer_weeks': round(buffer_weeks, 1),
            'total_lead_weeks': round(total_lead_weeks, 1),
            'suggested_order': suggested,
            'is_critical': urgency == 'CRITICAL',
            'is_low': urgency in ('ORDER_NOW', 'CRITICAL'),
            'urgency': urgency,
            'avg_price': p['avg_price'],
            'last_order': str(p['last_order']) if p['last_order'] else '',
        })
    
    # Sort by urgency
    urgency_order = {'CRITICAL': 0, 'ORDER_NOW': 1, 'ORDER_SOON': 2, 'OK': 3, 'NO_SALES': 4}
    suggestions.sort(key=lambda x: (urgency_order.get(x['urgency'], 5), -x['weekly_sales']))
    
    # Summary
    by_store = {}
    for s in suggestions:
        st = s['store']
        if st not in by_store:
            by_store[st] = {'total': 0, 'critical': 0, 'order_now': 0, 'order_soon': 0, 'ok': 0}
        by_store[st]['total'] += 1
        if s['urgency'] in by_store[st]:
            by_store[st][s['urgency'].lower() if s['urgency'] != 'ORDER_NOW' and s['urgency'] != 'ORDER_SOON' else 
                       'critical' if s['urgency'] == 'CRITICAL' else 
                       'order_now' if s['urgency'] == 'ORDER_NOW' else 'order_soon'] += 1
    
    # Fix key mapping
    for st in by_store:
        counts = by_store[st]
        # Manual counting since urgency key names differ from couht keys
        c = sum(1 for s in suggestions if s['store'] == st and s['urgency'] == 'CRITICAL')
        on_ = sum(1 for s in suggestions if s['store'] == st and s['urgency'] == 'ORDER_NOW')
        os_ = sum(1 for s in suggestions if s['store'] == st and s['urgency'] == 'ORDER_SOON')
        ok_ = sum(1 for s in suggestions if s['store'] == st and s['urgency'] == 'OK')
        by_store[st] = {'total': len([s for s in suggestions if s['store'] == st]),
                        'critical': c, 'order_now': on_, 'order_soon': os_, 'ok': ok_}
    
    return {
        "suggestions": [s for s in suggestions if s['urgency'] != 'NO_SALES'],
        "summary": by_store,
        "generated_at": datetime.now().isoformat()
    }


if __name__ == '__main__':
    import json
    result = suggest_restock()
    
    # Print urgent ones
    urgent = [s for s in result['suggestions'] if s['urgency'] in ('CRITICAL', 'ORDER_NOW')]
    if urgent:
        print(f"\n{'='*80}")
        print(f"🚨 URGENT RESTOCK NEEDED ({len(urgent)} products)")
        print(f"{'='*80}")
        for s in urgent:
            print(f"  {s['store']}/{s['marketplace']} {s['asin']}: "
                  f"stock={s['in_warehouse']} sales={s['weekly_sales']}/wk "
                  f"→ order {s['suggested_order']} units ({s['urgency']})")
    
    ok = [s for s in result['suggestions'] if s['urgency'] == 'OK']
    print(f"\n✅ OK: {len(ok)} products healthy")
    print(json.dumps(result.get('summary', {}), indent=2))
