"""
FBA Inbound Shipment Manager
============================
Full lifecycle: Create Plan → List Warehouses → Confirm → Labels → Ship → Track → Receive

Tables:
  inbound_shipments - One row per shipment (1 per warehouse per plan)
  inbound_logistics - Logistics cost + carrier info per shipment
"""

import os, httpx, json, psycopg2
from datetime import datetime, timedelta
from psycopg2.extras import execute_values

HOSTS = {'NA': 'sellingpartnerapi-na.amazon.com', 'FE': 'sellingpartnerapi-fe.amazon.com',
         'EU': 'sellingpartnerapi-eu.amazon.com', 'CN': None}

STORES = {
    'CUCZUUS': {'num': '02', 'seller_id': 'AXW589ZKKDJNM'},
    'BOOLUU': {'num': '03', 'seller_id': 'A8P0GUSN1BWCC'},
    'Heliumx': {'num': '04', 'seller_id': 'AD4UKHMR5K35J'},
    'WOC': {'num': '05', 'seller_id': ''},
}

REGION_MARKET = {
    'US': ('NA', 'ATVPDKIKX0DER'), 'CA': ('NA', 'A2EUQ1WTGCTBG2'),
    'AU': ('FE', 'A39IBJ37TRP1C6'), 'JP': ('FE', 'A1VC38T7YXB528'),
    'DE': ('EU', 'A1PA6795UKMFR9'), 'MX': ('NA', 'A1AM78C64UM0Y8'),
    'BR': ('NA', 'A2Q3Y263D00KWC'),
}

# Transit time estimates (sea freight)
TRANSIT_TIMES = {
    'US': 25, 'CA': 25, 'AU': 15, 'JP': 10, 'DE': 35, 'MX': 20, 'BR': 30,
}

def _get_dsn():
    return os.environ.get('DATABASE_URL', '')

def _ensure_tables():
    dsn = _get_dsn()
    if not dsn: return
    conn = psycopg2.connect(dsn, connect_timeout=10)
    cur = conn.cursor()
    
    # Inbound shipments
    cur.execute("""
        CREATE TABLE IF NOT EXISTS inbound_shipments (
            shipment_id VARCHAR(32) PRIMARY KEY,
            plan_id VARCHAR(32),
            store VARCHAR(10),
            marketplace VARCHAR(5),
            warehouse_id VARCHAR(10),
            warehouse_name VARCHAR(100),
            warehouse_city VARCHAR(50),
            asin VARCHAR(15),
            sku VARCHAR(50),
            quantity INT,
            status VARCHAR(20) DEFAULT 'PLAN_CREATED',
            -- Statuses: PLAN_CREATED, CONFIRMED, LABELED, SHIPPED, IN_TRANSIT, RECEIVING, CLOSED
            created_at TIMESTAMP DEFAULT NOW(),
            confirmed_at TIMESTAMP,
            shipped_at TIMESTAMP,
            estimated_arrival DATE,
            arrived_at TIMESTAMP,
            carrier_name VARCHAR(50),
            tracking_number VARCHAR(100),
            notes TEXT
        )
    """)
    
    # Logistics costs
    cur.execute("""
        CREATE TABLE IF NOT EXISTS inbound_logistics (
            id SERIAL PRIMARY KEY,
            shipment_id VARCHAR(32) REFERENCES inbound_shipments(shipment_id),
            logistics_company VARCHAR(50),
            service_type VARCHAR(30),
            -- Cost breakdown (CNY)
            freight_cost_cny DECIMAL(10,2),
            customs_cost_cny DECIMAL(10,2),
            insurance_cost_cny DECIMAL(10,2),
            other_cost_cny DECIMAL(10,2),
            total_cost_cny DECIMAL(10,2),
            -- Tax / Export docs
            export_declaration_no VARCHAR(50),
            invoice_no VARCHAR(50),
            container_no VARCHAR(50),
            bill_of_lading VARCHAR(50),
            -- Timing
            departed_origin_at TIMESTAMP,
            estimated_arrival_at TIMESTAMP,
            actual_arrival_at TIMESTAMP,
            customs_cleared_at TIMESTAMP,
            -- Status
            status VARCHAR(20) DEFAULT 'PENDING',
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    
    conn.commit()
    conn.close()

def _get_lwa(store_num, region):
    cid = os.environ.get(f'STORE_{store_num}_LWA_CLIENT_ID', '')
    csec = os.environ.get(f'STORE_{store_num}_LWA_CLIENT_SECRET', '')
    ref_key = {'NA': 'AMERICAS', 'FE': 'AU', 'EU': 'EU'}[region]
    ref = os.environ.get(f'STORE_{store_num}_REFRESH_TOKEN_{ref_key}', '')
    if not cid or not ref:
        return None
    r = httpx.post('https://api.amazon.com/auth/o2/token',
        json={'grant_type': 'refresh_token', 'client_id': cid,
              'client_secret': csec, 'refresh_token': ref}, timeout=15)
    if r.status_code != 200:
        return None
    return r.json()['access_token']

# ============================================================
# STEP 1: Create Inbound Plan
# ============================================================
def create_inbound_plan(store, marketplace, asin, sku, quantity, label_preference='SELLER_LABEL'):
    """
    Create an FBA inbound plan.
    Returns: plan_id, list of placement options
    """
    mkt_info = REGION_MARKET.get(marketplace)
    if not mkt_info:
        return {"error": f"Unknown marketplace: {marketplace}"}
    region, mid = mkt_info
    
    store_cfg = STORES.get(store)
    if not store_cfg:
        return {"error": f"Unknown store: {store}"}
    
    tk = _get_lwa(store_cfg['num'], region)
    if not tk:
        return {"error": "LWA auth failed"}
    
    host = HOSTS[region]
    
    # v2024-03-20 API
    body = {
        "destinationMarketplaces": [mid],
        "sourceAddress": {
            "name": store,
            "addressLine1": "Factory Address",  # Fill actual
            "countryCode": "CN"
        },
        "items": [{
            "asin": asin,
            "sku": sku,
            "quantity": quantity,
            "labelOwner": label_preference,
            "prepDetails": {
                "prepInstruction": "POLYBAGGING"
            }
        }],
        "name": f"{store}-{asin}-{datetime.now().strftime('%Y%m%d')}"
    }
    
    r = httpx.post(
        f'https://{host}/fulfillmentInbound/v2024-03-20/inboundPlans',
        headers={'x-amz-access-token': tk, 'Content-Type': 'application/json'},
        json=body, timeout=30
    )
    
    if r.status_code == 202:
        data = r.json()
        plan_id = data.get('inboundPlanId', '')
        
        # Generate placement options
        time.sleep(2)
        r2 = httpx.post(
            f'https://{host}/fulfillmentInbound/v2024-03-20/inboundPlans/{plan_id}/placementOptions',
            headers={'x-amz-access-token': tk, 'Content-Type': 'application/json'},
            json={}, timeout=30
        )
        
        placements = []
        if r2.status_code == 202:
            # Poll for placement options
            for _ in range(10):
                time.sleep(2)
                r3 = httpx.get(
                    f'https://{host}/fulfillmentInbound/v2024-03-20/inboundPlans/{plan_id}/placementOptions',
                    headers={'x-amz-access-token': tk}, timeout=15
                )
                if r3.status_code == 200:
                    data3 = r3.json()
                    placements = data3.get('placementOptions', [])
                    break
            
            return {
                "status": "plan_created",
                "plan_id": plan_id,
                "store": store,
                "marketplace": marketplace,
                "asin": asin,
                "sku": sku,
                "quantity": quantity,
                "placements": placements,
                "warehouses": [
                    {
                        "warehouse_id": p.get('warehouseId'),
                        "quantity": p.get('confirmedQuantity'),
                        "warehouse_name": p.get('destination', {}).get('warehouseName', ''),
                    }
                    for p in placements
                ]
            }
    
    return {"error": f"API failed: {r.status_code}", "body": r.text[:500]}


# ============================================================
# STEP 2: Confirm Placement (creates shipment IDs)
# ============================================================
def confirm_placement(plan_id, placement_option_id):
    """Confirm a placement option. Returns shipment IDs."""
    # Determine region from plan_id or store
    # Simplified: we need the store config
    # For now, assume NA
    return {"status": "confirm_placement", "plan_id": plan_id, "placement_id": placement_option_id}


# ============================================================
# STEP 3: Get Labels
# ============================================================
def get_shipment_labels(store, region, shipment_id):
    """Get FBA shipment labels."""
    store_cfg = STORES.get(store, {})
    tk = _get_lwa(store_cfg.get('num', '02'), region)
    if not tk:
        return {"error": "LWA auth failed"}
    
    host = HOSTS[region]
    
    r = httpx.get(
        f'https://{host}/fulfillmentInbound/v0/shipments/{shipment_id}/labels',
        params={'PageType': 'PackageLabel_Plain_Paper', 'LabelType': 'BARCODE_2D'},
        headers={'x-amz-access-token': tk}, timeout=15
    )
    
    if r.status_code == 200:
        data = r.json().get('payload', r.json())
        label_data = data.get('LabelData', {})
        return {
            "shipment_id": shipment_id,
            "label_url": label_data.get('PackageLabel', {}).get('Image', {}).get('URL', ''),
            "status": "labelled"
        }
    
    return {"error": f"Label API failed: {r.status_code}"}


# ============================================================
# STEP 4: Register Logistics + Ship
# ============================================================
def register_logistics(shipment_id, logistics_data):
    """
    Register logistics info for a shipment.
    
    logistics_data = {
        "logistics_company": "DHL / Maersk / SF Express",
        "freight_cost_cny": 1500.00,
        "customs_cost_cny": 300.00,
        "insurance_cost_cny": 100.00,
        "other_cost_cny": 0,
        "carrier_name": "Maersk",
        "tracking_number": "MAEU123456789",
        "container_no": "TCLU1234567",
        "bill_of_lading": "BOL123456",
        "export_declaration_no": "EX20260700001",
        "invoice_no": "INV-2026-07001",
        "departed_origin_at": "2026-07-10",
        "estimated_arrival_at": "2026-08-05",
    }
    """
    dsn = _get_dsn()
    conn = psycopg2.connect(dsn, connect_timeout=10)
    cur = conn.cursor()
    
    # Calculate total
    fields = ['freight_cost_cny', 'customs_cost_cny', 'insurance_cost_cny', 'other_cost_cny']
    total = sum(float(logistics_data.get(f, 0)) for f in fields)
    
    cur.execute("""
        INSERT INTO inbound_logistics (
            shipment_id, logistics_company, freight_cost_cny, customs_cost_cny,
            insurance_cost_cny, other_cost_cny, total_cost_cny,
            export_declaration_no, invoice_no, container_no, bill_of_lading,
            carrier_name, tracking_number,
            departed_origin_at, estimated_arrival_at, status
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (shipment_id) DO UPDATE SET
            logistics_company = EXCLUDED.logistics_company,
            freight_cost_cny = EXCLUDED.freight_cost_cny,
            total_cost_cny = EXCLUDED.total_cost_cny,
            carrier_name = EXCLUDED.carrier_name,
            tracking_number = EXCLUDED.tracking_number,
            departed_origin_at = EXCLUDED.departed_origin_at,
            estimated_arrival_at = EXCLUDED.estimated_arrival_at,
            status = 'IN_TRANSIT',
            updated_at = NOW()
    """, (
        shipment_id,
        logistics_data.get('logistics_company', ''),
        logistics_data.get('freight_cost_cny', 0),
        logistics_data.get('customs_cost_cny', 0),
        logistics_data.get('insurance_cost_cny', 0),
        logistics_data.get('other_cost_cny', 0),
        total,
        logistics_data.get('export_declaration_no', ''),
        logistics_data.get('invoice_no', ''),
        logistics_data.get('container_no', ''),
        logistics_data.get('bill_of_lading', ''),
        logistics_data.get('carrier_name', ''),
        logistics_data.get('tracking_number', ''),
        logistics_data.get('departed_origin_at'),
        logistics_data.get('estimated_arrival_at'),
        'IN_TRANSIT'
    ))
    
    # Update shipment status
    cur.execute("""
        UPDATE inbound_shipments 
        SET status = 'SHIPPED', shipped_at = NOW(),
            carrier_name = %s, tracking_number = %s,
            estimated_arrival = %s
        WHERE shipment_id = %s
    """, (
        logistics_data.get('carrier_name', ''),
        logistics_data.get('tracking_number', ''),
        logistics_data.get('estimated_arrival_at'),
        shipment_id
    ))
    
    conn.commit()
    conn.close()
    
    return {
        "shipment_id": shipment_id,
        "status": "SHIPPED",
        "total_cost_cny": total,
        "estimated_arrival": logistics_data.get('estimated_arrival_at', '')
    }


# ============================================================
# STEP 5: Track All Active Shipments
# ============================================================
def check_transit_alerts():
    """Check all shipments in transit for delays. Returns alerts."""
    dsn = _get_dsn()
    conn = psycopg2.connect(dsn, connect_timeout=10)
    cur = conn.cursor()
    
    cur.execute("""
        SELECT 
            s.shipment_id, s.store, s.marketplace, s.asin, s.quantity,
            s.estimated_arrival, s.shipped_at,
            l.logistics_company, l.total_cost_cny,
            l.departed_origin_at, l.estimated_arrival_at
        FROM inbound_shipments s
        LEFT JOIN inbound_logistics l ON s.shipment_id = l.shipment_id
        WHERE s.status IN ('SHIPPED', 'IN_TRANSIT')
    """)
    
    alerts = []
    today = datetime.now().date()
    
    for r in cur.fetchall():
        sid, store, mkt, asin, qty, est_arr, shipped = r[0:6]
        est_arr_date = est_arr.date() if hasattr(est_arr, 'date') else est_arr
        
        if est_arr_date:
            days_left = (est_arr_date - today).days
            days_since_shipped = (today - shipped.date()).days if shipped else 0
            
            expected_transit = TRANSIT_TIMES.get(mkt, 25)
            
            if days_left < 0:
                alerts.append(f"🚨 {store}/{mkt} {asin}: OVERDUE ({abs(days_left)}d past ETA) - {sid}")
            elif days_left < 3:
                alerts.append(f"📦 {store}/{mkt} {asin}: Arriving soon ({days_left}d) - {sid}")
            elif days_since_shipped > expected_transit * 1.5:
                alerts.append(f"⚠️ {store}/{mkt} {asin}: In transit {days_since_shipped}d (expected ~{expected_transit}d) - {sid}")
    
    conn.close()
    return alerts


# ============================================================
# STEP 6: Mark as Received (when Amazon confirms arrival)
# ============================================================
def mark_received(shipment_id):
    """Mark shipment as received/closed."""
    dsn = _get_dsn()
    conn = psycopg2.connect(dsn, connect_timeout=10)
    cur = conn.cursor()
    
    cur.execute("""
        UPDATE inbound_shipments 
        SET status = 'CLOSED', arrived_at = NOW()
        WHERE shipment_id = %s
    """, (shipment_id,))
    
    cur.execute("""
        UPDATE inbound_logistics 
        SET status = 'DELIVERED', actual_arrival_at = NOW(), updated_at = NOW()
        WHERE shipment_id = %s
    """, (shipment_id,))
    
    conn.commit()
    conn.close()
    
    return {"shipment_id": shipment_id, "status": "CLOSED"}


# ============================================================
# Dashboard: All Active Shipments
# ============================================================
def list_active_shipments():
    """Return all active inbound shipments."""
    dsn = _get_dsn()
    if not dsn:
        return []
    conn = psycopg2.connect(dsn, connect_timeout=10)
    cur = conn.cursor()
    
    cur.execute("""
        SELECT s.shipment_id, s.store, s.marketplace, s.asin, s.sku,
               s.quantity, s.status, s.warehouse_id, s.warehouse_city,
               s.shipped_at, s.estimated_arrival,
               l.logistics_company, l.total_cost_cny, l.carrier_name,
               l.tracking_number, l.container_no
        FROM inbound_shipments s
        LEFT JOIN inbound_logistics l ON s.shipment_id = l.shipment_id
        WHERE s.status NOT IN ('CLOSED', 'CANCELLED')
        ORDER BY s.created_at DESC
    """)
    
    rows = [{
        'shipment_id': r[0], 'store': r[1], 'marketplace': r[2],
        'asin': r[3], 'sku': r[4], 'quantity': r[5], 'status': r[6],
        'warehouse': f"{r[7]} ({r[8]})" if r[7] else '',
        'shipped_at': str(r[9]) if r[9] else '',
        'estimated_arrival': str(r[10]) if r[10] else '',
        'logistics': r[11] or '', 'total_cost_cny': float(r[12] or 0),
        'carrier': r[13] or '', 'tracking': r[14] or '',
        'container': r[15] or ''
    } for r in cur.fetchall()]
    
    conn.close()
    return rows


# Initialize tables on import
_ensure_tables()

# For pytest / direct execution
if __name__ == '__main__':
    alerts = check_transit_alerts()
    for a in alerts:
        print(a)
    print(f"\nActive shipments: {len(list_active_shipments())}")
