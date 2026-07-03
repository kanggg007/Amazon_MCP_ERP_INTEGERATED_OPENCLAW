"""Calc margin for B0FXGKT7D8 (CUC-US-FT19-1) in CUCZUUS US."""
import os, httpx, openpyxl

BASE = "/Users/jiahe/.openclaw/workspace/Planned system project/amazon-ops-intel"

# Load catalog
wb = openpyxl.load_workbook(os.path.join(BASE, 'data', 'Master_Product_Catalog_v2.xlsx'), data_only=True)
prod = None
for row in wb.active.iter_rows(min_row=2, values_only=True):
    if str(row[2]) == 'B0FXGKT7D8' and str(row[1]) == 'US':
        l=float(row[4] or 0); w=float(row[5] or 0); h=float(row[6] or 0); k=float(row[7] or 0)
        c=float(row[8] or 0)
        print(f"Found: {row[3]}, L={l} W={w} H={h} kg={k}, Manuf=¥{c}")
        prod = (l,w,h,k,c)
        break

if not prod:
    print("Not in catalog!")
    exit(1)

l,w,h,kg,cogs_cny = prod
CNY = 0.1477
cogs_usd = cogs_cny * CNY

# US freight: vol weight kg rate ¥5/kg
vol_kg = l*w*h/6000
billable_kg = max(kg, vol_kg)
freight_cny = billable_kg * 5
freight_usd = freight_cny * CNY

price = 27.99
ref = price * 0.15

print(f"\nPrice:   ${price:.2f}")
print(f"COGS:    ¥{cogs_cny:.2f} → ${cogs_usd:.2f}")
print(f"Freight: ¥{freight_cny:.2f} (vol={vol_kg:.2f}kg, billable={billable_kg:.2f}kg) → ${freight_usd:.2f}")
print(f"Referral: ${ref:.2f} (15%)")

# Products Fee API
env = {}
for line in open(os.path.join(BASE, '.env')).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k,v = line.split('=',1); env[k.strip()] = v.strip()

r = httpx.post('https://api.amazon.com/auth/o2/token',
    json={'grant_type':'refresh_token','client_id':env['STORE_02_LWA_CLIENT_ID'],
          'client_secret':env['STORE_02_LWA_CLIENT_SECRET'],
          'refresh_token':env['STORE_02_REFRESH_TOKEN_AMERICAS']}, timeout=15)
tk = r.json()['access_token']

r2 = httpx.get('https://sellingpartnerapi-na.amazon.com/products/fees/v0/items/B0FXGKT7D8/feesEstimate',
    params={'MarketplaceId': 'ATVPDKIKX0DER', 'IsAmazonFulfilled': 'true',
            'PriceToEstimateFees.Points.PointsNumber': '0',
            'PriceToEstimateFees.ListingPrice.CurrencyCode': 'USD',
            f'PriceToEstimateFees.ListingPrice.Amount': price},
    headers={'x-amz-access-token': tk}, timeout=15)

fba = None
if r2.status_code == 200:
    data = r2.json()
    fees = data.get('payload', data).get('FeesEstimateResult', {}).get('FeesEstimate', {}).get('FeeDetailList', [])
    for f in fees:
        name = f.get('FeeType', '')
        if name == 'FBAPerUnitFulfillmentFee':
            fba = float(f.get('FeeAmount', {}).get('FeeAmount', 0) or 0)
            break

fba_est = price * 0.25 if not fba else fba
print(f"FBA:     ${fba or 'N/A'}" + (" (estimated)" if not fba else ""))

ads_share = 41.73 / 66 * (price / 40.58)  # rough per-order allocation
print(f"Ads:     ~${ads_share:.2f} (rough allocation)")

profit = price - cogs_usd - freight_usd - ref - fba_est - ads_share
margin = profit / price * 100
print(f"\nProfit:  ${profit:.2f}")
print(f"Margin:  {margin:.1f}%")
print(f"\nCost structure: COGS {cogs_usd/price*100:.1f}% + Frt {freight_usd/price*100:.1f}% + FBA {fba_est/price*100:.1f}% + Ref 15% + Ads {ads_share/price*100:.1f}%")
