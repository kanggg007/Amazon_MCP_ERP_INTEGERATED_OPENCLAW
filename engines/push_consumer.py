"""
SP-API Push Consumer Worker
============================
Continuously polls SQS for ORDER_CHANGE notifications,
updates orders_active table, and optionally triggers
v2026 enrichment for new orders.

Run: python3 engines/push_consumer.py
"""

import os, sys, json, time, threading, traceback
from datetime import datetime, timezone, timedelta

import boto3
import psycopg2
import httpx

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BASE, '.env')
env = {}
for line in open(ENV_PATH).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

# Config
AWS_ACCESS_KEY = env['STORE_02_AWS_ACCESS_KEY_ID']
AWS_SECRET_KEY = env['STORE_02_AWS_SECRET_ACCESS_KEY']
AWS_REGION = 'us-east-1'
DSN = env['DATABASE_URL']

LWA_CLIENT_ID = env['STORE_02_LWA_CLIENT_ID']
LWA_CLIENT_SECRET = env['STORE_02_LWA_CLIENT_SECRET']
RF_AMERICAS = env['STORE_02_REFRESH_TOKEN_AMERICAS']
RF_AU = env.get('STORE_02_REFRESH_TOKEN_AU', '')
RF_EU = env['STORE_02_REFRESH_TOKEN_EU', '')

HOSTS = {'NA': 'sellingpartnerapi-na.amazon.com',
         'FE': 'sellingpartnerapi-fe.amazon.com',
         'EU': 'sellingpartnerapi-eu.amazon.com'}

# Load push config
with open(os.path.join(BASE, 'data', 'push_config.json')) as f:
    config = json.load(f)

QUEUE_URL = config['sqs_queue_url']

# Ensure tables exist
conn = psycopg2.connect(DSN)
cur = conn.cursor()
cur.execute("""
    CREATE TABLE IF NOT EXISTS orders_active (
        order_id VARCHAR(32) PRIMARY KEY,
        store VARCHAR(10),
        marketplace VARCHAR(5),
        marketplace_id VARCHAR(20),
        seller_id VARCHAR(20),
        status VARCHAR(20),
        fulfillment_type VARCHAR(10),
        purchase_date TIMESTAMP,
        last_updated TIMESTAMP DEFAULT NOW()
    )
""")
cur.execute("""
    CREATE TABLE IF NOT EXISTS order_items_active (
        id SERIAL PRIMARY KEY,
        order_id VARCHAR(32) REFERENCES orders_active(order_id),
        order_item_id VARCHAR(32),
        seller_sku VARCHAR(50),
        asin VARCHAR(15),
        quantity INT,
        unit_price DECIMAL(10,2),
        currency VARCHAR(5),
        title VARCHAR(200),
        UNIQUE(order_id, order_item_id)
    )
""")
cur.execute("""
    CREATE TABLE IF NOT EXISTS orders_archive (
        order_id VARCHAR(32),
        order_item_id VARCHAR(32),
        store VARCHAR(10),
        marketplace VARCHAR(5),
        marketplace_id VARCHAR(20),
        asin VARCHAR(15),
        sku VARCHAR(50),
        title VARCHAR(200),
        unit_price DECIMAL(10,2),
        quantity INT,
        revenue DECIMAL(10,3),
        currency VARCHAR(5),
        cogs DECIMAL(10,3),
        sea_freight DECIMAL(10,3),
        fba_fee DECIMAL(10,3),
        referral_fee DECIMAL(10,3),
        ads_spend DECIMAL(10,3),
        acos DECIMAL(6,3),
        ads_attributed_sales DECIMAL(10,3),
        refund_amount DECIMAL(10,3),
        final_status VARCHAR(20),
        purchase_date TIMESTAMP,
        order_date DATE,
        settled_date TIMESTAMP,
        PRIMARY KEY(order_id, order_item_id)
    )
""")
conn.commit()
conn.close()

# LWA token cache
lwa_cache = {}
lwa_lock = threading.Lock()

def get_lwa(store, region):
    """Get LWA token for store+region."""
    key = (store, region)
    with lwa_lock:
        if key in lwa_cache:
            return lwa_cache[key]
    
    # All stores use same LWA for now — expand per store as needed
    STORE_TOKENS = {
        ('CUCZUUS', 'NA'): RF_AMERICAS,
        ('BOOLUU', 'NA'): env.get('STORE_03_REFRESH_TOKEN_AMERICAS', RF_AMERICAS),
        ('Heliumx', 'NA'): env.get('STORE_04_REFRESH_TOKEN_AMERICAS', RF_AMERICAS),
    }
    ref = STORE_TOKENS.get((store, region), RF_AMERICAS)
    
    r = httpx.post('https://api.amazon.com/auth/o2/token',
        json={'grant_type': 'refresh_token', 'client_id': LWA_CLIENT_ID,
              'client_secret': LWA_CLIENT_SECRET, 'refresh_token': ref}, timeout=15)
    if r.status_code != 200:
        return None
    tk = r.json()['access_token']
    with lwa_lock:
        lwa_cache[key] = tk
    return tk

def enrich_order(order_id, store, marketplace_id, seller_id):
    """Pull v2026 for this order to get ASIN, price, SKU."""
    # Determine region from marketplace_id
    if marketplace_id in ('ATVPDKIKX0DER', 'A2EUQ1WTGCTBG2'):
        region = 'NA'
    elif marketplace_id in ('A39IBJ37TRP1C6', 'A1VC38T7YXB528'):
        region = 'FE'
    elif marketplace_id == 'A1PA6795UKMFR9':
        region = 'EU'
    else:
        region = 'NA'
    
    # Determine marketplace code
    mkt_code = {
        'ATVPDKIKX0DER': 'US', 'A2EUQ1WTGCTBG2': 'CA',
        'A39IBJ37TRP1C6': 'AU', 'A1VC38T7YXB528': 'JP',
        'A1PA6795UKMFR9': 'DE'
    }.get(marketplace_id, 'US')
    
    tk = get_lwa(store, region)
    if not tk:
        return []
    
    host = HOSTS[region]
    try:
        r = httpx.get(
            f'https://{host}/orders/2026-01-01/orders',
            params={'orderIds': order_id},
            headers={'x-amz-access-token': tk},
            timeout=15
        )
        if r.status_code != 200:
            print(f"  ⚠ v2026 pull failed for {order_id}: {r.status_code}")
            return []
        
        orders = r.json().get('orders', [])
        items = []
        for o in orders:
            for it in o.get('orderItems', []):
                p = it.get('product', {})
                items.append({
                    'order_item_id': it.get('orderItemId', ''),
                    'asin': p.get('asin', ''),
                    'sku': p.get('sellerSku', ''),
                    'title': p.get('title', '')[:200] if p.get('title') else '',
                    'price': float(p.get('price', {}).get('unitPrice', {}).get('amount', 0) or 0),
                    'currency': p.get('price', {}).get('unitPrice', {}).get('currencyCode', 'USD'),
                    'qty': int(it.get('quantityOrdered', 1) or 1),
                    'marketplace': mkt_code,
                })
        return items
    except Exception as e:
        print(f"  ⚠ Error enriching {order_id}: {e}")
        return []

def process_notification(msg):
    """Process a single SP-API notification."""
    body = json.loads(msg['Body'])
    notification_type = body.get('NotificationType', '')
    
    if notification_type == 'ORDER_CHANGE':
        return process_order_change(body)
    elif notification_type == 'MFN_INBOUND_MESSAGE':
        return process_buyer_message(body)
    else:
        print(f"  ⚠ Unknown notification type: {notification_type}")
        return

def process_buyer_message(body):
    payload = body.get('Payload', {})
    notification = payload.get('OrderChangeNotification', {})
    
    oid = notification.get('AmazonOrderId', '')
    change_type = notification.get('OrderChangeType', '')
    seller_id = notification.get('SellerId', '')
    
    summary = notification.get('Summary', {})
    marketplace_id = summary.get('MarketplaceId', '')
    status = summary.get('OrderStatus', '')
    fulfillment = summary.get('FulfillmentType', '')
    purchase_date = summary.get('PurchaseDate', '')
    order_items = summary.get('OrderItems', [])
    
    # Determine store from seller_id
    SELLER_TO_STORE = {
        'AXW589ZKKDJNM': 'CUCZUUS',
        'A8P0GUSN1BWCC': 'BOOLUU',
        'AD4UKHMR5K35J': 'Heliumx',
        'A3T7S7ZJU0L4UW': 'CUCZUUS',
        'A1OQTH56GU88CL': 'CUCZUUS',
        'A5O26V5UGR14D': 'CUCZUUS',
        'AUNV8PFX5IF0V': 'BOOLUU',
        'A1D6RZ93LW6YJ0': 'BOOLUU',
        'A1J6BBZMNHT0JZ': 'Heliumx',
        'A3K1UQY1P92DWZ': 'Heliumx',
        '3865316191299322': 'Heliumx',
    }
    store = SELLER_TO_STORE.get(seller_id, 'UNKNOWN')
    
    mkt_code = {
        'ATVPDKIKX0DER': 'US', 'A2EUQ1WTGCTBG2': 'CA',
        'A39IBJ37TRP1C6': 'AU', 'A1VC38T7YXB528': 'JP',
        'A1PA6795UKMFR9': 'DE'
    }.get(marketplace_id, '?')
    
    print(f"\n📦 {change_type} | {oid} | {store}/{mkt_code} | {status} | "
          f"{len(order_items)} items | {fulfillment}")
    
    # UPSERT order
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO orders_active (order_id, store, marketplace, marketplace_id, seller_id, status, fulfillment_type, purchase_date, last_updated)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (order_id) DO UPDATE SET
            status = EXCLUDED.status,
            last_updated = NOW()
    """, (oid, store, mkt_code, marketplace_id, seller_id, status, fulfillment, purchase_date))
    conn.commit()
    conn.close()
    
    # Enrich with v2026 for ASIN + price (if not yet enriched)
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM order_items_active WHERE order_id = %s", (oid,))
    already_enriched = cur.fetchone()[0] > 0
    conn.close()
    
    if not already_enriched:
        items = enrich_order(oid, store, marketplace_id, seller_id)
        if items:
            conn = psycopg2.connect(DSN)
            cur = conn.cursor()
            for it in items:
                cur.execute("""
                    INSERT INTO order_items_active (order_id, order_item_id, seller_sku, asin, quantity, unit_price, currency, title)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (order_id, order_item_id) DO NOTHING
                """, (oid, it['order_item_id'], it['sku'], it['asin'],
                       it['qty'], it['price'], it['currency'], it['title']))
                print(f"  ➕ {it['asin']} {it['sku']} ${it['price']} x{it['qty']}")
            
            # Compute live profit for each item
            try:
                from engines.live_profit import compute_profit
                for it in items:
                    profit_result = compute_profit(
                        order_id=oid, store=store, marketplace=it['marketplace'],
                        sku=it['sku'], quantity=it['qty'],
                        order_date=str(purchase_date)[:10] if purchase_date else None
                    )
                    if profit_result and 'error' not in profit_result:
                        print(f"  💰 profit=${profit_result['profit']} ({profit_result['margin']}%)")
            except Exception as e:
                print(f"  ⚠️ profit compute error: {e}")
            conn.commit()
            conn.close()

def main():
    print("=" * 60)
    print("SP-API Push Consumer Worker")
    print("=" * 60)
    print(f"  Queue:   {QUEUE_URL}")
    print(f"  Region:  {AWS_REGION}")
    print(f"  Time:    {datetime.now(timezone.utc).isoformat()}")
    print()
    print("Listening for ORDER_CHANGE notifications...")
    print("-" * 60)
    
    sqs = boto3.client(
        'sqs',
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
        region_name=AWS_REGION
    )
    
    while True:
        try:
            response = sqs.receive_message(
                QueueUrl=QUEUE_URL,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=20,  # Long polling
                VisibilityTimeout=300,
            )
            
            messages = response.get('Messages', [])
            for msg in messages:
                try:
                    process_notification(msg)
                    # Delete processed message
                    sqs.delete_message(
                        QueueUrl=QUEUE_URL,
                        ReceiptHandle=msg['ReceiptHandle']
                    )
                except Exception as e:
                    print(f"  ❌ Failed: {e}")
                    traceback.print_exc()
            
            if not messages:
                sys.stdout.write('.')
                sys.stdout.flush()
        except KeyboardInterrupt:
            print("\nShutting down...")
            break
        except Exception as e:
            print(f"\n  ⚠ Poll error: {e}")
            time.sleep(10)

def archive_settled_orders():
    """Move orders > 7 days old from active to archive. Returns count."""
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO orders_archive (
            order_id, order_item_id, store, marketplace, marketplace_id,
            asin, sku, title, unit_price, quantity, revenue, currency,
            cogs, sea_freight, fba_fee, referral_fee, ads_spend, acos,
            ads_attributed_sales, refund_amount, final_status,
            purchase_date, order_date, settled_date
        )
        SELECT 
            oa.order_id, oi.order_item_id,
            oa.store, oa.marketplace, oa.marketplace_id,
            oi.asin, oi.seller_sku, oi.title,
            oi.unit_price, oi.quantity,
            oi.unit_price * oi.quantity as revenue, oi.currency,
            COALESCE(pcm.cogs, 0) * oi.quantity as cogs,
            COALESCE(pcm.freight, 0) * oi.quantity as sea_freight,
            COALESCE(fba.fba_fee, 0) * oi.quantity as fba_fee,
            (oi.unit_price * oi.quantity * 0.15) as referral_fee,
            0 as ads_spend, 0 as acos, 0 as ads_attributed_sales,
            0 as refund_amount,
            oa.status as final_status,
            oa.purchase_date,
            oa.purchase_date::date as order_date,
            NOW() as settled_date
        FROM orders_active oa
        JOIN order_items_active oi ON oa.order_id = oi.order_id
        LEFT JOIN product_cost_master pcm ON oi.asin = pcm.asin AND oa.marketplace = pcm.marketplace
        WHERE oa.purchase_date < NOW() - INTERVAL '7 days'
          AND oa.status NOT IN ('Pending', 'Canceled')
          AND NOT EXISTS (
              SELECT 1 FROM orders_archive arc 
              WHERE arc.order_id = oa.order_id AND arc.order_item_id = oi.order_item_id
          )
        ON CONFLICT (order_id, order_item_id) DO NOTHING
    """)
    n = cur.rowcount or 0
    if n > 0:
        cur.execute("""
            DELETE FROM order_items_active oi
            USING orders_active oa
            WHERE oi.order_id = oa.order_id
              AND oa.purchase_date < NOW() - INTERVAL '7 days'
              AND oa.status NOT IN ('Pending', 'Canceled')
        """)
        cur.execute("""
            DELETE FROM orders_active
            WHERE purchase_date < NOW() - INTERVAL '7 days'
              AND status NOT IN ('Pending', 'Canceled')
        """)
    conn.commit()
    conn.close()
    return n


if __name__ == '__main__':
    main()
