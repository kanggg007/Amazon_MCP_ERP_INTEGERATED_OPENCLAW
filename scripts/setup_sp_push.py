"""
SP-API Push Notifications Setup
===============================
1. Create SQS queue in AWS
2. Register destination with SP-API Notifications
3. Subscribe to ORDER_CHANGE
4. Start SQS consumer worker

Run: python3 scripts/setup_sp_push.py
"""

import os, sys, json, time, httpx, boto3, hashlib, hmac, urllib.parse, threading

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BASE, '.env')
env = {}
for line in open(ENV_PATH).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

AWS_ACCESS_KEY = env.get('STORE_02_AWS_ACCESS_KEY_ID', '')
AWS_SECRET_KEY = env.get('STORE_02_AWS_SECRET_ACCESS_KEY', '')
AWS_REGION = 'us-east-1'
SQS_QUEUE_NAME = 'amazon-ops-intel-order-changes'

# ============================================================
# PART 1: Create SQS Queue
# ============================================================

print("=" * 60)
print("STEP 1: Creating SQS Queue...")
print("=" * 60)

sqs = boto3.client(
    'sqs',
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
    region_name=AWS_REGION
)

# Create or get queue
try:
    response = sqs.create_queue(
        QueueName=SQS_QUEUE_NAME,
        Attributes={
            'VisibilityTimeout': '300',
            'MessageRetentionPeriod': '86400',  # 1 day
        }
    )
    queue_url = response['QueueUrl']
    print(f"✓ Queue created: {queue_url}")
except sqs.exceptions.QueueNameExists:
    response = sqs.get_queue_url(QueueName=SQS_QUEUE_NAME)
    queue_url = response['QueueUrl']
    print(f"✓ Queue exists: {queue_url}")

# Get queue ARN
attrs = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=['QueueArn'])
queue_arn = attrs['Attributes']['QueueArn']
print(f"  ARN: {queue_arn}")

# ============================================================
# PART 2: Register Destination with SP-API
# ============================================================

print("\n" + "=" * 60)
print("STEP 2: Registering Notification Destination...")
print("=" * 60)

LWA_CLIENT_ID = env['STORE_02_LWA_CLIENT_ID']
LWA_CLIENT_SECRET = env['STORE_02_LWA_CLIENT_SECRET']
RF_NA = env['STORE_02_REFRESH_TOKEN_AMERICAS']
RF_FE = env.get('STORE_02_REFRESH_TOKEN_AU', '')
RF_EU = env['STORE_02_REFRESH_TOKEN_EU', '']

HOSTS = {'NA': 'sellingpartnerapi-na.amazon.com',
         'FE': 'sellingpartnerapi-fe.amazon.com',
         'EU': 'sellingpartnerapi-eu.amazon.com'}

def get_lwa_token(region='NA'):
    ref = {'NA': RF_NA, 'FE': RF_FE, 'EU': RF_EU}[region]
    r = httpx.post('https://api.amazon.com/auth/o2/token',
        json={'grant_type': 'refresh_token', 'client_id': LWA_CLIENT_ID,
              'client_secret': LWA_CLIENT_SECRET, 'refresh_token': ref}, timeout=15)
    return r.json()['access_token']

def sp_api_post(path, body, region='NA', use_grantless=False):
    tk = get_lwa_token(region) if not use_grantless else get_grantless_token()
    host = HOSTS[region]
    r = httpx.post(f'https://{host}{path}',
        headers={'x-amz-access-token': tk, 'Content-Type': 'application/json'},
        json=body, timeout=15)
    return r

def sp_api_get(path, region='NA'):
    tk = get_lwa_token(region)
    host = HOSTS[region]
    r = httpx.get(f'https://{host}{path}',
        headers={'x-amz-access-token': tk}, timeout=15)
    return r

# Create destination (grantless operation for SQS)
print("  Creating destination...")
body = {
    'name': 'AmazonOpsIntel-OrderChanges',
    'resourceSpecification': {
        'sqs': {
            'arn': queue_arn
        }
    }
}

# createDestination is grantless — use sign operation, then POST
def get_grantless_token():
    r = httpx.post('https://api.amazon.com/auth/o2/token',
        json={'grant_type': 'client_credentials', 'scope': 'sellingpartnerapi::notifications',
              'client_id': LWA_CLIENT_ID, 'client_secret': LWA_CLIENT_SECRET}, timeout=15)
    return r.json()['access_token']

r = httpx.post(
    f'https://sellingpartnerapi-na.amazon.com/notifications/v1/destinations',
    headers={'x-amz-access-token': get_grantless_token(), 'Content-Type': 'application/json'},
    json=body, timeout=15
)

print(f"  Status: {r.status_code}")
if r.status_code == 200:
    dest_id = r.json().get('payload', {}).get('destinationId', '')
    print(f"  ✓ Destination created: {dest_id}")
elif r.status_code == 409:
    # Already exists — list destinations
    print("  Destination may already exist. Listing...")
    r2 = httpx.get(
        f'https://sellingpartnerapi-na.amazon.com/notifications/v1/destinations',
        headers={'x-amz-access-token': get_grantless_token()}, timeout=15
    )
    dests = r2.json().get('payload', [])
    dest_id = None
    for d in dests:
        if d.get('name') == 'AmazonOpsIntel-OrderChanges':
            dest_id = d['destinationId']
            print(f"  ✓ Found existing: {dest_id}")
            break
    if not dest_id and dests:
        dest_id = dests[0]['destinationId']
        print(f"  ⚠ Using first available: {dest_id}")
else:
    print(f"  Error: {r.text[:500]}")
    dest_id = None

# Save config
config = {
    'sqs_queue_url': queue_url,
    'sqs_queue_arn': queue_arn,
    'destination_id': dest_id,
}
with open(os.path.join(BASE, 'data', 'push_config.json'), 'w') as f:
    json.dump(config, f, indent=2)
print(f"\n  Config saved to data/push_config.json")

# ============================================================
# PART 3: Subscribe to ORDER_CHANGE
# ============================================================

print("\n" + "=" * 60)
print("STEP 3: Subscribing to ORDER_CHANGE...")
print("=" * 60)

if not dest_id:
    print("  ⚠ No destination ID — skipping subscription")
else:
    sub_body = {
        'payloadVersion': '1.0',
        'destinationId': dest_id,
        'processingDirective': {
            'eventFilter': {
                'eventFilterType': 'ORDER_CHANGE'
            }
        }
    }
    
    r = httpx.post(
        f'https://sellingpartnerapi-na.amazon.com/notifications/v1/subscriptions/ORDER_CHANGE',
        headers={'x-amz-access-token': get_lwa_token('NA'), 'Content-Type': 'application/json'},
        json=sub_body, timeout=15
    )
    print(f"  Status: {r.status_code}")
    if r.status_code in (200, 409):
        sub = r.json().get('payload', {})
        sub_id = sub.get('subscriptionId', '(already exists)')
        print(f"  ✓ Subscription: {sub_id}")
    else:
        print(f"  Response: {r.text[:500]}")

# ============================================================
# PART 4: Subscribe to MFN_INBOUND_MESSAGE (buyer messages)
# ============================================================

print("\n" + "=" * 60)
print("STEP 4: Subscribing to MFN_INBOUND_MESSAGE...")
print("=" * 60)

if not dest_id:
    print("  ⚠ No destination ID — skipping subscription")
else:
    msg_body = {
        'payloadVersion': '1.0',
        'destinationId': dest_id,
    }
    
    r = httpx.post(
        f'https://sellingpartnerapi-na.amazon.com/notifications/v1/subscriptions/MFN_INBOUND_MESSAGE',
        headers={'x-amz-access-token': get_lwa_token('NA'), 'Content-Type': 'application/json'},
        json=msg_body, timeout=15
    )
    print(f"  Status: {r.status_code}")
    if r.status_code in (200, 409):
        sub = r.json().get('payload', {})
        sub_id = sub.get('subscriptionId', '(already exists)')
        print(f"  ✓ Subscription: {sub_id}")
    else:
        print(f"  Response: {r.text[:500]}")

# ============================================================
# PART 5: Subscribe WOC (STORE_05) to ORDER_CHANGE
# ============================================================

print("\n" + "=" * 60)
print("STEP 5: Subscribing WOC to ORDER_CHANGE...")
print("=" * 60)

woc_cid = os.environ.get('STORE_05_LWA_CLIENT_ID', '')
woc_csec = os.environ.get('STORE_05_LWA_CLIENT_SECRET', '')

if not woc_cid or not dest_id:
    print(f"  ⚠ WOC creds missing or no destination")
else:
    woc_sub_body = {
        'payloadVersion': '1.0',
        'destinationId': dest_id,
        'processingDirective': {
            'eventFilter': {
                'eventFilterType': 'ORDER_CHANGE'
            }
        }
    }
    
    for woc_region, woc_host, woc_ref_key in [
        ('NA', 'sellingpartnerapi-na.amazon.com', 'STORE_05_REFRESH_TOKEN_AMERICAS'),
        ('FE', 'sellingpartnerapi-fe.amazon.com', 'STORE_05_REFRESH_TOKEN_AU'),
        ('EU', 'sellingpartnerapi-eu.amazon.com', 'STORE_05_REFRESH_TOKEN_EU'),
    ]:
        woc_ref = os.environ.get(woc_ref_key, '')
        if not woc_ref:
            print(f"  WOC {woc_region}: no refresh token")
            continue
        
        wr = httpx.post('https://api.amazon.com/auth/o2/token',
            json={'grant_type': 'refresh_token', 'client_id': woc_cid,
                  'client_secret': woc_csec, 'refresh_token': woc_ref}, timeout=15)
        if wr.status_code != 200:
            print(f"  WOC {woc_region}: LWA fail {wr.status_code}")
            continue
        wtk = wr.json()['access_token']
        
        wr2 = httpx.post(f'https://{woc_host}/notifications/v1/subscriptions/ORDER_CHANGE',
            headers={'x-amz-access-token': wtk, 'Content-Type': 'application/json'},
            json=woc_sub_body, timeout=15)
        print(f"  WOC {woc_region} ORDER_CHANGE: {wr2.status_code}")
        if wr2.status_code in (200, 409):
            sub = wr2.json().get('payload', {})
            print(f"    ✓ {sub.get('subscriptionId', 'exists')}")
        else:
            print(f"    ✗ {wr2.text[:200]}")

# ============================================================
# Summary
# ============================================================
print("\n" + "=" * 60)
print("SETUP COMPLETE")
print("=" * 60)
print(f"  SQS Queue:     {queue_url}")
print(f"  Destination:   {dest_id}")
print(f"  Subscription:  ORDER_CHANGE → {dest_id}")
print()
print("Next: Run the consumer worker to start processing messages")
print("  python3 engines/push_consumer.py")
