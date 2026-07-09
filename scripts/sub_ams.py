"""Subscribe all store profiles to AMS sp-traffic"""
import httpx, os, uuid, json

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = {}
for line in open(os.path.join(BASE, '.env')).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

ARN = 'arn:aws:sqs:us-east-1:977574654028:amazon-ads-stream'
DATASET = 'sp-traffic'

stores = {
    'CUCZUUS': '02', 'BOOLUU': '03', 'Heliumx': '04'
}

results = {}
for name, snum in stores.items():
    cid = env.get(f'STORE_{snum}_ADS_CLIENT_ID', '')
    csec = env.get(f'STORE_{snum}_ADS_CLIENT_SECRET', '')
    ref = env.get(f'STORE_{snum}_ADS_REFRESH_TOKEN', '')
    if not cid or not ref:
        print(f'{name}: no creds')
        continue
    
    r = httpx.post('https://api.amazon.com/auth/o2/token',
        data={'grant_type': 'refresh_token', 'client_id': cid, 'client_secret': csec, 'refresh_token': ref}, timeout=15)
    if r.status_code != 200:
        print(f'{name}: LWA fail')
        continue
    tk = r.json()['access_token']
    
    r2 = httpx.get('https://advertising-api.amazon.com/v2/profiles',
        headers={'Amazon-Advertising-API-ClientId': cid, 'Authorization': f'Bearer {tk}'}, timeout=15)
    if r2.status_code != 200:
        print(f'{name}: profiles fail')
        continue
    
    results[name] = {}
    for p in r2.json():
        pid = str(p['profileId'])
        cc = p.get('countryCode', '?')
        
        headers = {
            'Amazon-Advertising-API-ClientId': cid,
            'Amazon-Advertising-API-Scope': pid,
            'Content-Type': 'application/vnd.MarketingStreamSubscriptions.StreamSubscriptionResource.v1.0+json',
            'Authorization': f'Bearer {tk}'
        }
        body = {
            'clientRequestToken': str(uuid.uuid4()),
            'dataSetId': DATASET,
            'destinationArn': ARN,
            'notes': f'{name} {cc} {DATASET}'
        }
        r3 = httpx.post('https://advertising-api.amazon.com/streams/subscriptions',
            headers=headers, json=body, timeout=15)
        
        if r3.status_code in (200, 201, 409):
            sub_id = r3.json().get('subscriptionId', 'exists')
            results[name][cc] = '✓'
            print(f'✅ {name:10} {cc:3} → {sub_id[:30]}...')
        else:
            results[name][cc] = r3.json().get('message', 'error')[:60]
            print(f'✗ {name:10} {cc:3} → {results[name][cc]}')

print(f'\nDone! {sum(len(v) for v in results.values())} subscriptions')
