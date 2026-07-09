"""Setup AMS (Amazon Marketing Stream) subscriptions for all stores"""
import httpx, os, json, uuid

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = {}
for line in open(os.path.join(BASE, '.env')).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

# SQS ARN (from existing setup)
SQS_ARN = 'arn:aws:sqs:us-east-1:977574654028:amazon-ads-stream-queue'

# Store Ads configs
STORES = {
    'CUCZUUS': {
        'client_id': env['STORE_02_ADS_CLIENT_ID'],
        'client_secret': env['STORE_02_ADS_CLIENT_SECRET'],
        'refresh_token': env['STORE_02_ADS_REFRESH_TOKEN'],
    },
    'BOOLUU': {
        'client_id': env['STORE_03_ADS_CLIENT_ID'],
        'client_secret': env['STORE_03_ADS_CLIENT_SECRET'],
        'refresh_token': env['STORE_03_ADS_REFRESH_TOKEN'],
    },
    'Heliumx': {
        'client_id': env['STORE_04_ADS_CLIENT_ID'],
        'client_secret': env['STORE_04_ADS_CLIENT_SECRET'],
        'refresh_token': env['STORE_04_ADS_REFRESH_TOKEN'],
    },
}

# Datasets to subscribe to
DATASETS = ['sp-traffic', 'sp-conversions']

results = {}

for store_name, cfg in STORES.items():
    cid = cfg['client_id']; csec = cfg['client_secret']; ref = cfg['refresh_token']
    
    # Get Ads API token
    r = httpx.post('https://api.amazon.com/auth/o2/token',
        data={'grant_type': 'refresh_token', 'client_id': cid, 'client_secret': csec, 'refresh_token': ref}, timeout=15)
    if r.status_code != 200:
        print(f'{store_name}: LWA failed {r.status_code}')
        continue
    tk = r.json()['access_token']
    
    # Get profiles
    r2 = httpx.get('https://advertising-api.amazon.com/v2/profiles',
        headers={'Amazon-Advertising-API-ClientId': cid, 'Authorization': f'Bearer {tk}'}, timeout=15)
    if r2.status_code != 200:
        print(f'{store_name}: Profiles failed')
        continue
    
    profiles = r2.json()
    print(f'{store_name}: {len(profiles)} profiles')
    results[store_name] = {'profiles': len(profiles), 'subscriptions': {}}
    
    for profile in profiles:
        pid = str(profile['profileId'])
        cc = profile.get('countryCode', '?')
        
        for dataset in DATASETS:
            token = str(uuid.uuid4())
            body = {
                'clientRequestToken': token,
                'destinationArn': SQS_ARN,
                'dataSetId': dataset,
                'notes': f'{store_name} {cc} {dataset}'
            }
            
            r3 = httpx.post('https://advertising-api.amazon.com/streams/subscriptions',
                headers={
                    'Amazon-Advertising-API-ClientId': cid,
                    'Authorization': f'Bearer {tk}',
                    'Amazon-Advertising-API-Scope': pid,
                    'Content-Type': 'application/json'
                }, json=body, timeout=15)
            
            status = '✓' if r3.status_code in (200, 201, 409) else f'✗ {r3.status_code}'
            print(f'  {pid} {cc} {dataset}: {status}')
            if r3.status_code not in (200, 201, 409):
                print(f'    {r3.text[:200]}')
            
            results[store_name]['subscriptions'][f'{cc}_{dataset}'] = status

# Save config
with open(os.path.join(BASE, 'data', 'ams_subscriptions.json'), 'w') as f:
    json.dump(results, f, indent=2)

print(f'\nSaved: data/ams_subscriptions.json')
print(json.dumps(results, indent=2))
