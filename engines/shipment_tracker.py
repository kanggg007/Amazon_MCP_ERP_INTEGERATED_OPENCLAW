"""
Shipment Tracker — sea freight timeline + Amazon receipt monitoring.
Alerts: overdue shipments, booking ETA outside normal transit window, quantity discrepancies.
"""

import os, psycopg2
from datetime import datetime, timedelta

# ── Transit times from China ──────────────────────────────
WEST_COAST_DAYS = 30    # US West Coast + BC
EAST_COAST_DAYS = 45    # US Central/East + ON/East Canada
TRANSIT_BASE = {
    'AU': 45,           # Australia
    'DE': 60,           # Germany
    'JP': 25,           # Japan
    'MX': 20, 'BR': 30,
}
CA_WEST_DAYS = 40       # Canada West Coast (Vancouver)
GRACE_PERIOD = 7        # Days past expected before alerting

WEST_COAST_WAREHOUSES = {  # US West Coast
    'ONT8','ONT6','ONT9','ONT2','ONT3','ONT4','ONT5','ONT7',
    'LGB8','LGB3','LGB4','LGB6','LGB7','LGB9',
    'LAX9','SNA4','SNA6','SJC7','SMF3','SMF6',
    'OAK3','OAK4','OAK6',
    'SCK1','SCK3','SCK4',
    'PHX3','PHX5','PHX6','PHX7','PHX8','PHX9',
    'RNO4','LAS1','LAS2','RNO1','BOI2','SLC1','SLC2','SLC3','SLC4',
    'PDX6','PDX9','SEA6','SEA8','BFI3','BFI4','BFI7',
    'DEN2','DEN3','DEN4','DEN5','DEN8',
}

CA_WEST_WAREHOUSES = {  # Canada West (Vancouver area)
    'YVR2','YVR3','YVR4','YYC1','YYC2',
}

def _get_dsn():
    dsn = os.environ.get('DATABASE_URL', '')
    if not dsn:
        try:
            BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            env_path = os.path.join(BASE, '.env')
            if os.path.exists(env_path):
                for line in open(env_path).read().splitlines():
                    if 'DATABASE_URL' in line:
                        dsn = line.split('=', 1)[1].strip()
        except: pass
    return dsn

def get_transit_days(marketplace, warehouse):
    """Calculate expected transit days for a given marketplace + warehouse."""
    if marketplace in ('US',):
        if any(warehouse.startswith(w) for w in WEST_COAST_WAREHOUSES if warehouse):
            return WEST_COAST_DAYS
        return EAST_COAST_DAYS
    elif marketplace in ('CA',):
        if any(warehouse.startswith(w) for w in CA_WEST_WAREHOUSES if warehouse):
            return CA_WEST_DAYS
        return EAST_COAST_DAYS
    return TRANSIT_BASE.get(marketplace, 30)


def check_shipments(store=None):
    """Monitor all FBA inbound shipments."""
    dsn = _get_dsn()
    if not dsn:
        return {"error": "no DB"}
    
    conn = psycopg2.connect(dsn, connect_timeout=10)
    cur = conn.cursor()
    today = datetime.now().date()
    week_ago = today - timedelta(days=7)
    
    where = f"AND store_id = '{store}'" if store else ""
    
    cur.execute(f"""
        SELECT id, store_id, marketplace_id, shipment_id, shipment_name,
               shipment_status, destination_fulfillment_center,
               shipped_date, estimated_arrival_date,
               total_units_shipped, total_units_received,
               packed_quantity, logistics_company, container_no, bill_of_lading
        FROM inbound_shipments
        WHERE shipment_status = 'SHIPPED'
        {where}
        ORDER BY shipped_date
    """)
    
    at_sea = []; overdue = []
    
    for r in cur.fetchall():
        sid, store_id, mkt, ship_id, name, status, fc, ship_date, est_arr, shipped, rcvd, packed, log_co, ct, bol = r
        
        shipped_qty = packed or shipped or 0
        expected_transit = get_transit_days(mkt, fc or '')
        
        days_at_sea = (today - ship_date.date()).days if ship_date else 0
        expected_arrival = (ship_date + timedelta(days=expected_transit)).date() if ship_date else None
        is_overdue = days_at_sea > (expected_transit + GRACE_PERIOD)
        
        # Booking alert removed per user request — carrier manages ETA
        
        zone = 'West Coast' if (mkt in ('US','CA') and fc and 
            (any(fc.startswith(w) for w in WEST_COAST_WAREHOUSES) or 
             any(fc.startswith(w) for w in CA_WEST_WAREHOUSES))) else 'Central/East'
        
        item = {
            'shipment_id': ship_id,
            'store': store_id, 'marketplace': mkt,
            'warehouse': fc, 'warehouse_zone': zone if mkt in ('US','CA') else mkt,
            'shipped_qty': shipped_qty,
            'received_qty': rcvd or 0,
            'shipped_date': str(ship_date)[:10] if ship_date else '',
            'booking_eta': str(est_arr)[:10] if est_arr else '',
            'expected_arrival': str(expected_arrival) if expected_arrival else '',
            'days_at_sea': days_at_sea,
            'expected_transit_days': expected_transit,
            'container': ct or '', 'bol': bol or '', 'carrier': log_co or '',
        }
        
        at_sea.append(item)
        if is_overdue: overdue.append(item)
    
    # Discrepancies — alert on any mismatch, zero received, or >30% lost
    cur.execute(f"""
        SELECT shipment_id, store_id, marketplace_id,
               total_units_shipped, total_units_received,
               COALESCE(total_units_shipped,0) - COALESCE(total_units_received,0) as diff,
               received_date
        FROM inbound_shipments
        WHERE shipment_status = 'CLOSED'
          AND (
            COALESCE(total_units_shipped,0) != COALESCE(total_units_received,0)
            OR total_units_received IS NULL OR total_units_received = 0
          )
        {where}
        ORDER BY received_date DESC
    """)
    
    discrepancies = []
    for r in cur.fetchall():
        shipped = r[3] or 0
        received = r[4] or 0
        diff = shipped - received
        loss_pct = round(diff / shipped * 100, 1) if shipped > 0 else 0
        
        severity = 'WARN'
        if received == 0:
            severity = 'CRITICAL'  # Nothing received at all
        elif loss_pct > 30:
            severity = 'HIGH'       # >30% loss
        elif diff > 0:
            severity = 'LOW'
        
        discrepancies.append({
            'shipment_id': r[0], 'store': r[1], 'marketplace': r[2],
            'shipped': shipped, 'received': received, 'missing': diff,
            'loss_pct': loss_pct,
            'severity': severity,
            'received_date': str(r[6])[:10] if r[6] else '',
        })
    
    # Recently received
    cur.execute(f"""
        SELECT shipment_id, store_id, marketplace_id,
               total_units_shipped, total_units_received, received_date, shipped_date
        FROM inbound_shipments
        WHERE shipment_status = 'CLOSED' AND received_date >= %s
        {where}
        ORDER BY received_date DESC
    """, (week_ago,))
    
    recently_received = [{
        'shipment_id': r[0], 'store': r[1], 'marketplace': r[2],
        'shipped': r[3] or 0, 'received': r[4] or 0,
        'received_date': str(r[5])[:10] if r[5] else '',
        'shipped_date': str(r[6])[:10] if r[6] else '',
    } for r in cur.fetchall()]
    
    conn.close()
    
    alerts = []
    if overdue:
        alerts.append(f"🚨 {len(overdue)} shipments OVERDUE (> expected transit + {GRACE_PERIOD}d)")
    if discrepancies:
        alerts.append(f"⚠️ {len(discrepancies)} shipments with quantity discrepancy")
    
    return {
        "alerts": alerts,
        "at_sea": at_sea,
        "overdue": overdue,
        "discrepancies": discrepancies,
        "recently_received": recently_received,
        "summary": {
            "at_sea_count": len(at_sea),
            "overdue_count": len(overdue),
            "discrepancy_count": len(discrepancies),
            "recently_received_count": len(recently_received),
        },
        "transit_times": {
            'US_West': f'{WEST_COAST_DAYS}d',
            'US_Central_East': f'{EAST_COAST_DAYS}d',
            'CA_West': f'{CA_WEST_DAYS}d', 'CA_East': f'{EAST_COAST_DAYS}d',
            'AU': '45d', 'DE': '60d', 'JP': '25d',
        },
        "checked_at": datetime.now().isoformat()
    }


if __name__ == '__main__':
    import json
    result = check_shipments()
    for a in result.get('alerts', []): print(a)
    print(f"\nAt sea: {result['summary']['at_sea_count']}")
    if result['overdue']:
        print("\n🚨 OVERDUE:")
        for s in result['overdue']:
            print(f"  {s['shipment_id']} | {s['store']}/{s['marketplace']} {s['warehouse']} | {s['days_at_sea']}d (expected {s['expected_transit_days']}d)")
