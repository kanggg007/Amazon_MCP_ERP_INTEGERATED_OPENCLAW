# SP-API Store Setup Guide

## Prerequisites
- AWS Account: **977574654028**
- IAM Role: **SP-API-ROLE** (already created)
- IAM User: **sp-api-user** (already created)
- AWS Access Key: Already configured in `.env`

## Steps for Each New Store

### Step 1: Register as Developer
1. Go to **https://developer.amazonservices.com**
2. Sign in with the seller account
3. Register as a developer (if not already)
4. Fill in the application details
5. Wait for **Amazon approval** (1-2 business days)

### Step 2: Create SP-API App
1. Go to **https://sellercentral.amazon.com/sellingpartner/developerconsole**
2. Click **"Add new app client"**
3. Fill in:
   - **App name:** `Megapower Ops - [Store Name]`
   - **Type:** ✅ **Production** (NOT Sandbox!)
   - **IAM ARN:** `arn:aws:iam::977654028:role/SP-API-ROLE`
   - **Roles:** Select at minimum: Orders, Inventory, FBA
4. Save → Get **LWA Client ID + Client Secret**

### Step 3: Authorize & Get Refresh Token
1. On the app page, click **Authorize** or use this URL:
   ```
   https://sellercentral.amazon.com/apps/authorize/consent?application_id=YOUR_CLIENT_ID
   ```
2. Select ONE marketplace region at a time to authorize
3. Copy the **Refresh Token** for that region
4. Repeat for each marketplace region

### ⚠️ Each Marketplace Region = Separate Token
| Region | Marketplaces | Separate Token? |
|---|---|---|
| **Americas** | US, CA, MX | **Single token** (one authorization covers all 3) |
| **Europe** | UK, DE, FR, IT, ES | **Single token** (one authorization covers all EU) |
| **Australia** | AU only | **Separate token** |
| **Japan** | JP only | **Separate token** |

So for each store, you'll have up to **3 refresh tokens**: US/CA/MX + EU + AU

### Step 4: Add to `.env`
Add a new block to `.env`:
```
STORE_02_LWA_CLIENT_ID=amzn1.application-oa2-client.xxx
STORE_02_LWA_CLIENT_SECRET=amzn1.oa2-cs.v1.xxx
STORE_02_REFRESH_TOKEN=Atzr|...
STORE_02_AWS_IAM_ROLE_ARN=arn:aws:iam::977574654028:role/SP-API-ROLE
STORE_02_AWS_ACCESS_KEY_ID=AKIA... (same as Store 01)
STORE_02_AWS_SECRET_ACCESS_KEY=... (same as Store 01)
STORE_02_MARKETPLACE_IDS=ATVPDKIKX0DER,A2EUQ1WTGCTBG2
STORE_02_NAME=Store Name
```

### Notes
- **Same IAM keys** work for all stores — they're tied to your AWS account, not individual stores
- Each store gets its own **LWA Client ID + Secret + Refresh Token**
- The refresh token for US/CA/MX can be a single multi-marketplace token
- AU and EU each get their own token (separate authorization steps)
