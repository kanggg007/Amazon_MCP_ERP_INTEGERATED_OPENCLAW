# P&L Investigation — Handoff for New Session

## What we've done
- Pulled CUCZZUS orders from SP-API (all markets: US, CA, AU, JP)
- Matched COGS from Master_Product_Catalog_v2.xlsx
- Calculated FBA fees using published rate cards per size tier
- FBA returns data pulled for US and CA

## What Kang says is WRONG
- The P&L numbers don't match what he actually sees in his real data
- "everything is wrong. all the marketplace are wrong"

## Questions to investigate in new session
1. What are the ACTUAL Amazon fee statements showing? (Kang has real data)
2. Are the settlement reports we pulled complete? (only May 13-27 coverage)
3. Is the orders report accurate? (orders vs settlements don't perfectly align)
4. Are the product dimensions correct in the spreadsheet?
5. Are the freight costs correct? (CBM vs per-kg, exchange rate)
6. Is the exchange rate 0.14 CNY→USD correct?

## Data sources available
- Spreadsheet: /Users/jiahe/Desktop/Master_Product_Catalog_v2.xlsx
- SP-API credentials in .env
- Settlement data from SP-API Reports API
- Orders data from SP-API Reports API
- FBA returns from SP-API Reports API
- Ads spend from Finances API (ProductAdsPaymentEventList: $500 May)

## Key corrections learned
1. CAD prices → multiply by 0.76, NOT treat as USD
2. FBA fees = size tier fulfillment fee (from rate card) + 15% referral
3. AU prices × 0.66, JP prices × 0.0067
4. Medium Oversize in CA has crazy high fulfillment (~$38+ CAD)
5. Returns: unsellable units lose COGS + freight

## Start the new session by saying:
"I need to audit my CUCZZUS P&L. Let me show you the real settlement data I have. 
Start by comparing the spreadsheet costs to what I actually pay."
