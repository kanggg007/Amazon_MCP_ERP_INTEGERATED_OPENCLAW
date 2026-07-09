"""AMS Registration + Subscription with SigV4"""
import httpx, os, hashlib, hmac, time, urllib.parse, json, uuid, boto3

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = {}
for line in open(os.path.join(BASE, '.env')).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

AK = env['STORE_02_AWS_ACCESS_KEY_ID']
SK = env['STORE_02_AWS_SECRET_ACCESS_KEY']
cid = env['STORE_02_ADS_CLIENT_ID']
csec = env['STORE_02_ADS_CLIENT_SECRET']

# LWA token
r = httpx.post('https://api.amazon.com/auth/o2/token',
    data={'grant_type': 'refresh_token', 'client_id': cid, 'client_secret': csec, 'refresh_token': env['STORE_02_ADS_REFRESH_TOKEN']}, timeout=15)
tk = r.json()['access_token']
print('LWA OK')

# SigV4 helper
def sign(method, path, body, headers_to_sign):
    host = 'advertising-api.amazon.com'
    ad = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    ds = time.strftime('%Y%m%d', time.gmtime())
    
    headers_to_sign['host'] = host
    headers_to_sign['x-amz-date'] = ad
    if body:
        headers_to_sign['content-type'] = 'application/json'
    
    ch = ''.join(f'{k.lower()}:{v}\n' for k, v in sorted(headers_to_sign.items()))
    sh = ';'.join(sorted(k.lower() for k in headers_to_sign))
    body_hash = hashlib.sha256(body.encode() if body else b'').hexdigest()
    cr = '\n'.join([method, path, '', ch, sh, body_hash])
    cs = f'{ds}/us-east-1/execute-api/aws4_request'
    st = '\n'.join(['AWS4-HMAC-SHA256', ad, cs, hashlib.sha256(cr.encode()).hexdigest()])
    
    def h(k, m):
        if isinstance(k, str): k = k.encode()
        return hmac.new(k, m.encode() if isinstance(m, str) else m, hashlib.sha256).digest()
    
    ks = h(h(h(h(b'AWS4' + SK.encode(), ds), b'us-east-1'), b'execute-api'), b'aws4_request')
    sig = hmac.new(ks, st.encode(), hashlib.sha256).hexdigest()
    
    all_headers = dict(headers_to_sign)
    all_headers['authorization'] = f'AWS4-HMAC-SHA256 Credential={AK}/{cs},SignedHeaders={sh},Signature={sig}'
    all_headers['x-amz-access-token'] = tk
    return all_headers

# STEP 1: Register
path = '/prod/streams/registrations'
payload = json.dumps({'awsAccountId': '977574654028'})
headers = sign('POST', path, payload, {'Amazon-Advertising-API-ClientId': cid})

r3 = httpx.post(f'https://advertising-api.amazon.com{path}', headers=headers, content=payload, timeout=15)
print(f'Registration: {r3.status_code}')
print(r3.text[:500])

# STEP 2: Subscribe if registration succeeded
if r3.status_code in (200, 201, 204, 409):
    sqs = boto3.client('sqs', aws_access_key_id=AK, aws_secret_access_key=SK, region_name='us-east-1')
    q = sqs.get_queue_url(QueueName='amazon-ads-stream-queue')
    arn = sqs.get_queue_attributes(QueueUrl=q['QueueUrl'], AttributeNames=['QueueArn'])['Attributes']['QueueArn']
    
    # Subscribe also needs SigV4 + LWA
    sub_path = '/prod/streams/subscriptions'
    sub_body = json.dumps({
        'clientRequestToken': str(uuid.uuid4()),
        'dataSetId': 'sp-traffic',
        'destination': {'sqsDestination': {'queueArn': arn}},
        'notes': 'CUCZUUS US sp-traffic'
    })
    sub_headers = sign('POST', sub_path, sub_body, {
        'Amazon-Advertising-API-ClientId': cid,
        'Amazon-Advertising-API-Scope': '3465892858543553'
    })
    
    r4 = httpx.post(f'https://advertising-api.amazon.com{sub_path}', headers=sub_headers, content=sub_body, timeout=15)
    print(f'Subscribe: {r4.status_code}')
    print(r4.text[:500])
