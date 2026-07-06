"""Pull merchant listings for ALL stores via Reports API. Saves to desktop Excel."""
import httpx, time, os, openpyxl, gzip, io
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = {}
for line in open(os.path.join(BASE, '.env')).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

STORES = {
    'CUCZUUS': {'num': '02', 'markets': [
        ('NA', 'ATVPDKIKX0DER', 'US'), ('NA', 'A2EUQ1WTGCTBG2', 'CA'),
        ('FE', 'A39IBJ37TRP1C6', 'AU'), ('EU', 'A1PA6795UKMFR9', 'DE'),
        ('FE', 'A1VC38T7YXB528', 'JP'),
    ]},
    'BOOLUU': {'num': '03', 'markets': [
        ('NA', 'ATVPDKIKX0DER', 'US'), ('NA', 'A2EUQ1WTGCTBG2', 'CA'),
        ('FE', 'A39IBJ37TRP1C6', 'AU'), ('FE', 'A1VC38T7YXB528', 'JP'),
    ]},
    'Heliumx': {'num': '04', 'markets': [
        ('NA', 'ATVPDKIKX0DER', 'US'), ('NA', 'A2EUQ1WTGCTBG2', 'CA'),
        ('FE', 'A39IBJ37TRP1C6', 'AU'), ('EU', 'A1PA6795UKMFR9', 'DE'),
    ]},
    'WOC': {'num': '05', 'markets': [
        ('NA', 'ATVPDKIKX0DER', 'US'), ('NA', 'A2EUQ1WTGCTBG2', 'CA'),
        ('NA', 'A1AM78C64UM0Y8', 'MX'), ('NA', 'A2Q3Y263D00KWC', 'BR'),
        ('FE', 'A39IBJ37TRP1C6', 'AU'),
    ]},
}

HOSTS = {'NA': 'sellingpartnerapi-na.amazon.com', 'FE': 'sellingpartnerapi-fe.amazon.com', 'EU': 'sellingpartnerapi-eu.amazon.com'}

wb = openpyxl.Workbook()
wb.remove(wb.active)
total = 0

for store_name, store_cfg in STORES.items():
    snum = store_cfg['num']
    print(f'\n{store_name} (STORE_{snum})')
    
    cid = env.get(f'STORE_{snum}_LWA_CLIENT_ID', '')
    csec = env.get(f'STORE_{snum}_LWA_CLIENT_SECRET', '')
    if not cid:
        print(f'  No creds')
        continue
    
    for region, mid, mkt_name in store_cfg['markets']:
        host = HOSTS[region]
        ref_key = {'NA': 'AMERICAS', 'FE': 'AU', 'EU': 'EU'}[region]
        ref = env.get(f'STORE_{snum}_REFRESH_TOKEN_{ref_key}', '')
        if not ref:
            continue
        
        # LWA
        r = httpx.post('https://api.amazon.com/auth/o2/token',
            json={'grant_type': 'refresh_token', 'client_id': cid,
                  'client_secret': csec, 'refresh_token': ref}, timeout=15)
        if r.status_code != 200:
            print(f'  {mkt_name}: LWA fail')
            continue
        tk = r.json()['access_token']
        
        # Create report
        body = {'reportType': 'GET_MERCHANT_LISTINGS_ALL_DATA', 'marketplaceIds': [mid]}
        r2 = httpx.post(f'https://{host}/reports/2021-06-30/reports',
            headers={'x-amz-access-token': tk, 'Content-Type': 'application/json'}, json=body, timeout=15)
        
        if r2.status_code != 202:
            print(f'  {mkt_name}: report create fail {r2.status_code}')
            continue
        
        rid = r2.json()['reportId']
        print(f'  {mkt_name}: report {rid}...', end=' ', flush=True)
        
        # Poll for completion
        doc_id = None
        for _ in range(30):
            time.sleep(3)
            r3 = httpx.get(f'https://{host}/reports/2021-06-30/reports/{rid}',
                headers={'x-amz-access-token': tk}, timeout=15)
            if r3.status_code != 200:
                break
            status = r3.json().get('processingStatus', '?')
            if status == 'DONE':
                doc_id = r3.json().get('reportDocumentId', '')
                break
            elif status in ('CANCELLED', 'FATAL'):
                break
        
        if not doc_id:
            print('failed/timeout')
            continue
        
        # Download document
        r4 = httpx.get(f'https://{host}/reports/2021-06-30/documents/{doc_id}',
            headers={'x-amz-access-token': tk}, timeout=15)
        if r4.status_code != 200:
            print('doc fail')
            continue
        
        doc = r4.json()
        url = doc.get('url', '')
        r5 = httpx.get(url, timeout=30)
        
        # Decompress and parse TSV
        content = gzip.decompress(r5.content) if doc.get('compressionAlgorithm') == 'GZIP' else r5.content
        lines = content.decode('utf-8', errors='replace').split('\n')
        
        if len(lines) < 2:
            print('0 listings')
            continue
        
        # Parse header
        header = lines[0].split('\t')
        # Find relevant columns
        col_idx = {h.lower().replace('-', ''): i for i, h in enumerate(header) if h}
        
        asin_col = col_idx.get('asin1', col_idx.get('itemid', 0))
        sku_col = col_idx.get('seller-sku', col_idx.get('seller sku', 1))
        title_col = col_idx.get('item-name', col_idx.get('productname', 2))
        price_col = col_idx.get('price', col_idx.get('yourprice', 3))
        qty_col = col_idx.get('quantity', col_idx.get('qty', 4))
        status_col = col_idx.get('status', col_idx.get('open date', 5))
        
        sheet_name = f'{store_name} {mkt_name}'[:31]
        ws = wb.create_sheet(title=sheet_name)
        ws.append(['ASIN', 'Title', 'SKU', 'Price', 'Quantity', 'Status'])
        
        count = 0
        for line in lines[1:]:
            if not line.strip():
                continue
            cols = line.split('\t')
            asin = cols[asin_col].strip() if asin_col < len(cols) else ''
            title = cols[title_col].strip() if title_col < len(cols) else ''
            sku = cols[sku_col].strip() if sku_col < len(cols) else ''
            price = cols[price_col].strip() if price_col < len(cols) else ''
            qty = cols[qty_col].strip() if qty_col < len(cols) else ''
            status = cols[status_col].strip() if status_col < len(cols) else ''
            
            if asin:
                ws.append([asin, title[:200], sku, price, qty, status])
                count += 1
        
        total += count
        print(f'{count} listings')
        time.sleep(2)

path = os.path.expanduser('~/Desktop/merchant_listings_all_stores.xlsx')
wb.save(path)
print(f'\n✅ Saved: {path}')
print(f'   Total: {total} listings across {len(wb.sheetnames)} sheets')
