"""
Inbound Shipment Tracker - monitors FBA shipments in transit
and alerts when they're overdue or arriving soon.

Dependencies: inbound_shipments table on Railway (pre-existing schema)
"""

import os, psycopg2
from datetime import datetime, timedelta

def track_inbound_shipments(store=None):
    """
    Check all in-transit shipments and return status + alerts.

    Returns:
    {
        "in_transit": [...],   # Currently in transit
        "arriving_soon": [...], # Arriving within 7 days
        "overdue": [...],      # Past estimated arrival
        "received_recently": [...], # Received in last 7 days
    }
    """
    dsn = os.environ.get('DATABASE_URL', '')
    if not dsn:
        # Try .env
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

    where = f"AND store_id = '{store}'" if store else ""
    today = datetime.now().date()
    week_ago = today - timedelta(days=7)
    in_2_weeks = today + timedelta(days=14)
    in_7_days = today + timedelta(days=14)  # "arriving soon" window

    # In transit shipments
    cur.execute(f"""
        SELECT id, store_id, marketplace_id, shipment_id, shipment_name,
               shipment_status, destination_fulfillment_center,
               shipped_date, estimated_arrival_date,
               total_units_shipped, total_units_received,
               COALESCE(override_eta, estimated_arrival_date) as effective_eta,
               packed_quantity, logistics_company, container_no, bill_of_lading,
               raw_data->>'asin' as asin, raw_data->>'sku' as sku
        FROM inbound_shipments
        WHERE shipment_status IN ('WORKING', 'SHIPPED', 'IN_TRANSIT')
        {where}
        ORDER BY estimated_arrival_date
    """)

    in_transit = []
    overdue = []
    arriving_soon = []

    for r in cur.fetchall():
        sid, store_id, mkt, ship_id, name, status, fc, ship_date, est_arr, units, rcvd, eff_eta, packed, log_co, container, bol, asin, sku = r + (None,)*(18-len(r))

        item = {
            'id': sid,
            'store': store_id,
            'marketplace': mkt,
            'shipment_id': ship_id,
            'name': name,
            'status': status,
            'warehouse': fc,
            'shipped_date': str(ship_date)[:10] if ship_date else '',
            'estimated_arrival': str(est_arr)[:10] if est_arr else '',
            'effective_eta': str(eff_eta)[:10] if eff_eta else '',
            'units': units or 0,
            'received': rcvd or 0,
            'packed_quantity': packed or 0,
            'asin': asin or '?',
            'sku': sku or '?',
            'logistics': log_co or '',
            'container': container or '',
            'bill_of_lading': bol or '',
        }

        # Use effective_eta for alerts (override_eta if set, else Amazon's estimate)
        eta_to_check = eff_eta if eff_eta else est_arr
        if eta_to_check:
            eta_date = eta_to_check.date() if hasattr(eta_to_check, 'date') else eta_to_check
            eta_date = eta_date if isinstance(eta_date, type(today)) else today
            try: days_left = (eta_date - today).days
            except: days_left = 999

            if days_left < 0:
                item['alert'] = 'OVERDUE'
                item['days_overdue'] = abs(days_left)
                overdue.append(item)
            elif days_left <= 7:
                item['alert'] = 'ARRIVING_SOON'
                item['days_left'] = days_left
                arriving_soon.append(item)
            else:
                item['alert'] = 'IN_TRANSIT'
                item['days_left'] = days_left

        in_transit.append(item)

    # Recently received
    cur.execute(f"""
        SELECT id, store_id, marketplace_id, shipment_id, shipment_name,
               shipment_status, destination_fulfillment_center,
               shipped_date, estimated_arrival_date, received_date,
               total_units_shipped, total_units_received
        FROM inbound_shipments
        WHERE shipment_status = 'CLOSED'
          AND received_date >= %s
        {where}
        ORDER BY received_date DESC
    """, (week_ago,))

    received_recently = [{
        'id': r[0], 'store': r[1], 'marketplace': r[2],
        'shipment_id': r[3], 'name': r[4],
        'warehouse': r[6],
        'shipped_date': str(r[7])[:10] if r[7] else '',
        'estimated_arrival': str(r[8])[:10] if r[8] else '',
        'received_date': str(r[9])[:10] if r[9] else '',
        'units_shipped': r[10] or 0, 'units_received': r[11] or 0,
        'discrepancy': (r[10] or 0) - (r[11] or 0),
    } for r in cur.fetchall()]

    conn.close()

    return {
        "in_transit": in_transit,
        "overdue": overdue,
        "arriving_soon": arriving_soon,
        "received_recently": received_recently,
        "summary": {
            "total_in_transit": len(in_transit),
            "overdue": len(overdue),
            "arriving_soon": len(arriving_soon),
            "received_recently": len(received_recently),
        },
        "checked_at": datetime.now().isoformat()
    }


# Shipment registration (when you create an FBA shipment via Seller Central or API)
def register_shipment(store, marketplace, shipment_id, shipment_name,
                      warehouse, units, estimated_arrival=None, override_eta=None,
                      asin=None, sku=None, packed_quantity=None,
                      logistics_company=None, container_no=None, bill_of_lading=None):
    """
    Register an FBA inbound shipment for tracking.
    Call this after creating the shipment in Seller Central.
    """
    dsn = os.environ.get('DATABASE_URL', '')
    if not dsn:
        return {"error": "no DB"}

    conn = psycopg2.connect(dsn, connect_timeout=10)
    cur = conn.cursor()

    raw = {'asin': asin, 'sku': sku} if asin or sku else None

    cur.execute("""
        INSERT INTO inbound_shipments (
            store_id, marketplace_id, shipment_id, shipment_name,
            shipment_status, destination_fulfillment_center,
            total_units_shipped, estimated_arrival_date, override_eta,
            packed_quantity, logistics_company, container_no, bill_of_lading, raw_data
        ) VALUES (%s, %s, %s, %s, 'WORKING', %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (shipment_id) DO UPDATE SET
            shipment_status = EXCLUDED.shipment_status,
            estimated_arrival_date = COALESCE(EXCLUDED.estimated_arrival_date, inbound_shipments.estimated_arrival_date),
            override_eta = COALESCE(EXCLUDED.override_eta, inbound_shipments.override_eta),
            total_units_shipped = EXCLUDED.total_units_shipped,
            packed_quantity = COALESCE(EXCLUDED.packed_quantity, inbound_shipments.packed_quantity),
            logistics_company = COALESCE(EXCLUDED.logistics_company, inbound_shipments.logistics_company),
            container_no = COALESCE(EXCLUDED.container_no, inbound_shipments.container_no),
            bill_of_lading = COALESCE(EXCLUDED.bill_of_lading, inbound_shipments.bill_of_lading),
            raw_data = COALESCE(EXCLUDED.raw_data, inbound_shipments.raw_data)
    """, (store, marketplace, shipment_id, shipment_name,
          warehouse, units, estimated_arrival, override_eta,
          packed_quantity, logistics_company, container_no, bill_of_lading,
          psycopg2.extras.Json(raw) if raw else None))

    conn.commit()
    conn.close()

    return {"status": "registered", "shipment_id": shipment_id}


def update_shipment_status(shipment_id, status, received_units=None, received_date=None):
    """Update shipment status (SHIPPED / RECEIVING / CLOSED)."""
    dsn = os.environ.get('DATABASE_URL', '')
    if not dsn:
        return {"error": "no DB"}

    conn = psycopg2.connect(dsn, connect_timeout=10)
    cur = conn.cursor()

    if status == 'SHIPPED':
        cur.execute("""
            UPDATE inbound_shipments
            SET shipment_status = 'SHIPPED', shipped_date = NOW()
            WHERE shipment_id = %s
        """, (shipment_id,))
    elif status in ('CLOSED', 'RECEIVED'):
        cur.execute("""
            UPDATE inbound_shipments
            SET shipment_status = 'CLOSED',
                received_date = COALESCE(%s, NOW()),
                total_units_received = COALESCE(%s, total_units_shipped),
                discrepancy_units = total_units_shipped - COALESCE(%s, total_units_shipped)
            WHERE shipment_id = %s
        """, (received_date, received_units, received_units, shipment_id))
    else:
        cur.execute("""
            UPDATE inbound_shipments SET shipment_status = %s WHERE shipment_id = %s
        """, (status, shipment_id))

    conn.commit()
    conn.close()

    return {"status": "updated", "shipment_id": shipment_id, "new_status": status}


# Quick local test
if __name__ == '__main__':
    result = track_inbound_shipments()
    print(f"Total in transit: {result['summary']['total_in_transit']}")
    print(f"Overdue: {result['summary']['overdue']}")
    print(f"Arriving soon: {result['summary']['arriving_soon']}")
    print(f"Recently received: {result['summary']['received_recently']}")

    if result['overdue']:
        print("\n🚨 OVERDUE:")
        for s in result['overdue']:
            print(f"  {s['store']}/{s['marketplace']} {s['shipment_id']}: {s['units']} units, {s['days_overdue']}d late")

    if result['arriving_soon']:
        print("\n📦 ARRIVING SOON:")
        for s in result['arriving_soon']:
            print(f"  {s['store']}/{s['marketplace']} {s['shipment_id']}: {s['units']} units, {s['days_left']}d left")
    print("\nDone")
