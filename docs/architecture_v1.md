# Amazon AI Operating System Architecture v1

## Core Philosophy
Not dashboards — **profit optimization & money recovery**.

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    DATA LAYER (SP-API + Ads API)            │
├──────────┬──────────┬──────────┬───────────┬───────────────┤
│ Orders   │ Finances │ Returns  │ Inventory │ Ads (pending) │
│ Reports  │ API      │ API      │ Reports   │ Advertising   │
│ API      │          │          │ API       │ API           │
└────┬─────┴────┬─────┴────┬─────┴────┬──────┴──────┬────────┘
     │          │          │          │             │
     ▼          ▼          ▼          ▼             ▼
┌─────────────────────────────────────────────────────────────┐
│                 NORMALIZATION LAYER (SKU + store_id)        │
│          Every record → store_id + marketplace_id           │
└─────────────────────────────────────────────────────────────┘
     │          │          │          │             │
     ▼          ▼          ▼          ▼             ▼
┌─────────────────────────────────────────────────────────────┐
│                    ENGINES (Business Logic)                  │
├──────────┬──────────┬──────────┬───────────┬───────────────┤
│ Return/  │ Profit   │ FBA      │ CCIS      │ Inventory     │
│ Refund   │ Leakage  │ Reimb.   │ Comm.     │ Autopilot     │
│ Engine   │ Engine   │ Engine   │ Intel     │ Engine        │
├──────────┼──────────┼──────────┼───────────┼───────────────┤
│ VoC      │ Seasonal │ Ads      │ Reimb.    │ Forecasting   │
│ Engine   │ Demand   │ Scoring  │ Evidence  │ Engine        │
│          │ Intel    │ Engine   │ Generator │               │
└────┬─────┴────┬─────┴────┬─────┴────┬──────┴──────┬────────┘
     │          │          │          │             │
     ▼          ▼          ▼          ▼             ▼
┌─────────────────────────────────────────────────────────────┐
│              AI DETECTION LAYER (Patterns + Anomalies)      │
│  • Return reason clusters  • Wasted ad keywords            │
│  • FBA reimbursement gaps  • Stockout risk prediction      │
│  • Profit leakage by category  • Customer sentiment trends  │
└─────────────────────────────────────────────────────────────┘
     │          │          │          │             │
     ▼          ▼          ▼          ▼             ▼
┌─────────────────────────────────────────────────────────────┐
│              APPROVAL WORKFLOW (Human-in-the-Loop)          │
│  Level 1: Auto (low risk)                                  │
│  Level 2: Store Admin (medium risk)                        │
│  Level 3: Master Admin (high risk)                         │
└─────────────────────────────────────────────────────────────┘
     │          │          │          │             │
     ▼          ▼          ▼          ▼             ▼
┌─────────────────────────────────────────────────────────────┐
│                   QUERY & ACTION LAYER                      │
│  • OpenClaw NL queries  • Feishu bot                       │
│  • Automated reports  • Alert engine                       │
└─────────────────────────────────────────────────────────────┘
```

## Modules

### 1. Return & Refund Reconciliation Engine ✅ (LIVE)
- Finances API → `refunds` + `financial_transactions` tables
- Links returns (operational) → refunds (financial)
- CUCZZUS US May: $385.72 refunds across 7 orders
- Endpoints: `/finances/refunds`, `/finances/reconcile`, `/finances/loss`

### 2. Profit Leakage Detection System (PLDS) ✅ (LIVE)
- True Profit = Revenue - COGS - Ads - FBA Fees - Storage - Returns - Refunds - Disposal
- 5 leakage categories: 🟥 Ads 🟧 Returns 🟨 FBA 🟦 Pricing 🟪 Operational
- AI pattern detection: return reason clusters, wasted ad keywords, FBA gaps
- Endpoints: `/pld/leakage`, `/pld/sku`, `/pld/scan`, `/pld/patterns`

### 3. Two-Tier P&L System ✅ (LIVE)
- **Daily estimate**: Revenue × (1 - FBA_rate - COGS_rate - ads_rate)
- **Weekly settlement**: Corrects estimates with actual fees
- P&L locked accurate by 5th of next month
- Endpoints: `/pld/fba-rate`, `/pld/estimate-daily`

### 4. FBA Reimbursement Detection System (RDS) ✅ (LIVE)
- 5 detection rules: inbound shortage, lost inventory, damage, return not received, underpayment
- Evidence pack generator (Seller Central claim drafts)
- Universal recovery queue: "Where's the next $10K?"
- Endpoints: `/rds/scan`, `/rds/queue`, `/rds/summary`, `/rds/evidence`

### 5. Customer Communication Intelligence System (CCIS) ✅ (LIVE)
- 11 message types: shipping, damage, missing_part, installation, warranty, etc.
- Returnless replacement + Amazon disposal flow for glass products
- Customer risk scoring (refund probability, expected loss)
- Support playbooks with approval workflow
- Endpoints: `/ccis/process`, `/ccis/dashboard`, `/ccis/classify`, `/ccis/playbook`

### 6. VoC Intelligence Engine ✅ (LIVE)
- Source tier ranking: Tier 1 Reviews → Tier 5 Social
- Complaint ↔ profit impact correlation
- Weekly trends + monthly comparison
- Endpoints: `/voc/dashboard`, `/voc/profit-impact`, `/voc/trend`, `/voc/by-source`

### 7. Inventory Intelligence ✅ (LIVE)
- Turnover per SKU (months of stock)
- Aged stock flag (>6 months → liquidate)
- Peak season risk flag (Oct-Dec 3x storage fees)
- Endpoint: `/inventory/intel/{store_id}`

### 8. Inventory Autopilot Engine 🔜 (NEXT)
- Supply timeline: manufacturing + logistics + customs = total lead time
- Demand timeline: sales velocity + ads + seasonality + promotions
- 3-stage risk: 🟢 Safe → 🟡 Warning → 🔴 Critical
- Multi-scenario forecasting (base / ads scaling / promo / stockout recovery)
- PO recommendation with stockout date prediction
- Core decision: Days of Cover vs Lead Time

### 9. Seasonal Demand Intelligence Engine 🔜
- Seasonality Index: per-SKU monthly multipliers (Nov: 2.8x, Dec: 3.2x)
- Event spike layer: Prime Day (2.5x), Black Friday (3.0x)
- 3-Mode system: Normal → Pre-Peak → Peak
- Stockout cost model: lost opportunity = (peak demand − inventory) × margin

### 10. Ads Scoring Engine 🔜 (PENDING ADS API)
- Keyword-level efficiency scoring
- Wasted spend detection (spend > sales)
- Auto-pause recommendations (Level 2 approval)
- ACOS/ROAS optimization

## Database: 29 Tables

### Core Tables
| Table | Purpose |
|---|---|
| `stores` | Store registry |
| `products` | SKU catalog |
| `orders` | Amazon orders |
| `order_items` | Order line items |
| `inventory_snapshot` | Daily inventory levels |

### Financial Tables
| Table | Purpose |
|---|---|
| `financial_transactions` | All financial events (fees, ads, refunds) |
| `refunds` | Refund transactions |
| `returns` | Operational return events |
| `return_items` | Granular return breakdown |
| `profit_daily_snapshot` | Daily per-SKU profit |
| `customer_loss_events` | Unified loss analysis for AI |

### FBA Tables
| Table | Purpose |
|---|---|
| `inbound_shipments` | Shipment tracking |
| `shipment_discrepancies` | Lost/damaged discrepancies |
| `inventory_loss_events` | Inventory movement ledger |
| `reimbursement_tracking` | Claim cases |
| `recovery_opportunities` | Universal recovery queue |

### Advertising Tables
| Table | Purpose |
|---|---|
| `ads_campaigns` | Campaign registry |
| `ads_performance` | Daily ad performance |

### Customer Intelligence Tables
| Table | Purpose |
|---|---|
| `customer_feedback` | Reviews + return reasons + Q&A |
| `feedback_topics` | Classified topics |
| `customer_messages` | Buyer messages (CCIS) |
| `support_playbooks` | Approved templates |
| `message_drafts` | Auto-generated replies |
| `support_cases` | End-to-end case tracking |

### System Tables
| Table | Purpose |
|---|---|
| `expenses` | Manual expense tracking |
| `purchase_orders` | Manufacturing batches |
| `recommendation_log` | Every AI recommendation |
| `feedback_loop` | Outcome tracking |
| `audit_log` | Every action logged |

### Future Tables (Designed)
| Table | Purpose |
|---|---|
| `inventory_forecast_daily` | Daily demand + coverage |
| `demand_forecast_adjusted` | Seasonality-adjusted demand |
| `inventory_risk_forecast` | Stockout risk per SKU |
| `seasonality_index` | Monthly demand multipliers |
| `supplier_lead_times` | Manufacturing + logistics timelines |

## Deployment: Railway
- **URL:** `https://amazonmcperpintegeratedopenclaw-production.up.railway.app`
- **DB:** PostgreSQL (25+ tables)
- **GitHub:** `kanggg007/Amazon_MCP_ERP_INTEGERATED_OPENCLAW`
- **40+ API endpoints** live

## Key Metrics (CUCZZUS US May)
| Metric | Value |
|---|---|
| Gross Revenue | $31,552 |
| FBA Fees | $15,882 (50%) |
| Ads Spend | $999.93 |
| Refunds | $385.72 |
| Returns | 142 FBA returns (13% rate) |
| Inventory Units | 1,743 units across 27 SKUs |
| High-Risk SKU | FT22-2: only 4.5 days stock |

## Rules
1. No hardcoded secrets — all via env variables
2. Every record has `store_id` + `marketplace_id`
3. All currency → USD
4. P&L locked by 5th of each month
5. Read/write RBAC with Master/Sub Admin
6. High-risk actions require human approval
7. Cash-based P&L (excl AR/AP)
8. Settlement data: auto-generated, search only (cannot create)

## Roadmap
| Priority | Module | Status |
|---|---|---|
| 1 | Inventory Autopilot Engine | 🔜 Next |
| 2 | Seasonal Demand Intelligence | 🔜 |
| 3 | Ads API Integration + Scoring Engine | ⏳ Pending API Approval |
| 4 | Full Data Ingestion Pipeline (Railway) | 🔄 |
| 5 | Supply Chain Lead Time Tracking | 🔜 |
| 6 | Feishu Bot Integration | 🔜 |
| 7 | CUCZZUS EU Token Re-auth | ⏳ |
| 8 | Xpike + WOC Store Setup | ⏳ |
