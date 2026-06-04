# Database ERD

## Entity Relationship Diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                                STORES                                         │
├──────────────────────────────────────────────────────────────────────────────┤
│  store_id (PK, VARCHAR(64))                                                   │
│  name (VARCHAR(255), NOT NULL)                                                │
│  marketplace_ids (TEXT[], NOT NULL)                                           │
│  is_active (BOOLEAN, DEFAULT TRUE)                                            │
│  metadata (JSONB)                                                             │
│  created_at (TIMESTAMPTZ, DEFAULT NOW())                                      │
│  updated_at (TIMESTAMPTZ, DEFAULT NOW())                                      │
└────────────────────────────────┬─────────────────────────────────────────────┘
          │ 1                    │ 1                    │ 1
          │                      │                      │
          ▼                      ▼                      ▼
┌─────────────────┐   ┌──────────────────┐   ┌──────────────────────┐
│    PRODUCTS     │   │ INVENTORY_       │   │       ORDERS         │
├─────────────────┤   │ SNAPSHOT         │   ├──────────────────────┤
│ id (PK)         │   ├──────────────────┤   │ id (PK)              │
│ store_id (FK)   │   │ id (PK)          │   │ store_id (FK)        │
│ marketplace_id  │   │ store_id (FK)    │   │ marketplace_id       │
│ sku (UQ)        │   │ marketplace_id   │   │ amazon_order_id (UQ) │
│ asin            │   │ sku (UQ)         │   │ purchase_date        │
│ fnsku           │   │ fnsku            │   │ order_status (IDX)   │
│ upc             │   │ asin             │   │ fulfillment_channel  │
│ unit_cost       │   │ total_units      │   │ order_total_amount   │
│ title           │   │ inbound_units    │   │ sales_channel        │
│ brand           │   │ sellable_units   │   │ is_business_order    │
│ is_active       │   │ unsellable_units │   │ is_prime             │
│ raw_data        │   │ reserved_units   │   │ raw_data (JSONB)     │
│ created_at      │   │ days_of_supply   │   │ created_at           │
│ updated_at      │   │ forecast_depletion│  └──────────┬───────────┘
└─────────────────┘   │ snapshot_date    │             │ 1
                       │ raw_data (JSONB) │             │
                       │ created_at       │             ▼
                       └──────────────────┘   ┌──────────────────────┐
                                               │    ORDER_ITEMS       │
┌──────────────────┐                           ├──────────────────────┤
│ INBOUND_          │                           │ id (PK)              │
│ SHIPMENTS         │                           │ order_id (FK)        │
├──────────────────┤                           │ store_id           │
│ id (PK)           │                           │ amazon_order_id      │
│ store_id (FK)     │                           │ asin                 │
│ marketplace_id    │                           │ seller_sku (IDX)     │
│ shipment_id (UQ)  │                           │ title                │
│ shipment_status   │                           │ quantity_ordered     │
│ destination_fc    │                           │ item_price_amount    │
│ total_shipped     │                           │ shipping_price       │
│ total_received    │                           │ raw_data (JSONB)     │
│ discrepancy_units │                           │ created_at           │
│ raw_data (JSONB)  │                           └──────────────────────┘
└────────┬─────────┘
         │ 1
         │
         ▼
┌──────────────────────┐
│ SHIPMENT_            │
│ DISCREPANCIES        │  ← HIGHEST PRIORITY TABLE
├──────────────────────┤
│ id (PK)              │
│ store_id (FK)        │
│ shipment_id          │
│ sku                  │
│ quantity_shipped     │
│ quantity_received    │
│ quantity_damaged     │
│ quantity_lost        │
│ discrepancy_type     │
│ estimated_loss       │
│ claim_filed (IDX)    │
│ claim_id             │
│ claim_status         │
│ claim_filed_date     │
│ evidence_ready       │
│ evidence_path        │
│ raw_data (JSONB)     │
│ created_at           │
└──────────────────────┘

┌──────────────────┐    ┌──────────────────────┐    ┌──────────────────────┐
│ ADS_             │    │ PROFIT_DAILY_         │    │ CUSTOMER_            │
│ PERFORMANCE      │    │ SNAPSHOT              │    │ FEEDBACK             │
├──────────────────┤    ├──────────────────────┤    ├──────────────────────┤
│ id (PK)          │    │ id (PK)               │    │ id (PK)              │
│ store_id (FK)    │    │ store_id (FK)          │    │ store_id (FK)        │
│ campaign_id (IDX)│    │ sku                    │    │ asin                 │
│ ad_group_id      │    │ date                   │    │ source               │
│ sku              │    │ units_sold             │    │ feedback_id (UQ)     │
│ date             │    │ revenue                │    │ feedback_type        │
│ impressions      │    │ product_cost           │    │ star_rating          │
│ clicks           │    │ fba_fees               │    │ title                │
│ spend            │    │ referral_fees          │    │ body                 │
│ sales            │    │ advertising_spend      │    │ sentiment (IDX)      │
│ orders           │    │ returns_cost           │    │ is_verified          │
│ acos (generated) │    │ storage_fees           │    │ feedback_date (IDX)  │
│ ctr (generated)  │    │ inbound_shipping_cost  │    │ raw_data (JSONB)     │
│ raw_data (JSONB) │    │ net_profit (generated) │    │ created_at           │
│ created_at       │    │ margin_pct (generated) │    └──────────┬───────────┘
└──────────────────┘    │ tacos (generated)      │               │ 1
                        │ raw_data (JSONB)       │               │
                        │ created_at             │               ▼
                        └────────────────────────┘   ┌──────────────────────┐
                                                     │  FEEDBACK_TOPICS     │
┌──────────────────┐                                 ├──────────────────────┤
│ RECOMMENDATION_  │                                 │ id (PK)              │
│ LOG              │                                 │ feedback_id (FK)     │
├──────────────────┤                                 │ topic                │
│ id (PK)          │                                 │ sub_topic            │
│ store_id (FK)    │                                 │ sentiment            │
│ recommendation_id│                                 │ confidence_score     │
│ recommendation_  │                                 │ keywords (TEXT[])    │
│ type             │                                 │ created_at           │
│ recommendation_  │                                 └──────────────────────┘
│ text             │
│ confidence_score │
│ risk_level       │
│ human_action(IDX)│
│ before_state     │
│ after_state      │
│ raw_data (JSONB) │
│ created_at       │
└──────────────────┘

┌──────────────────────┐       ┌──────────────────────┐
│    AUDIT_LOG         │       │    FEEDBACK_LOOP      │
├──────────────────────┤       ├──────────────────────┤
│ id (PK)              │       │ id (PK)               │
│ store_id (FK)        │       │ recommendation_id(FK) │
│ action               │       │ store_id (FK)         │
│ actioned_by          │       │ outcome               │
│ resource_type        │       │ impact_metric         │
│ resource_id          │       │ impact_value          │
│ before_state (JSONB) │       │ threshold_adjusted    │
│ after_state (JSONB)  │       │ previous_threshold    │
│ status               │       │ new_threshold         │
│ error_message        │       │ created_at            │
│ created_at (IDX)     │       └──────────────────────┘
└──────────────────────┘
```

## Index Strategy

| Table | Index | Type | Purpose |
|---|---|---|---|
| `inventory_snapshot` | `(store_id, snapshot_date)` | B-tree | Daily inventory queries |
| `inventory_snapshot` | `(sku)` | B-tree | SKU lookups |
| `inventory_snapshot` | `(days_of_supply)` | B-tree | Low/overstock filtering |
| `orders` | `(store_id, purchase_date)` | B-tree | Date range queries |
| `orders` | `(order_status)` | B-tree | Status filtering |
| `order_items` | `(seller_sku)` | B-tree | SKU aggregation |
| `order_items` | `(asin)` | B-tree | ASIN lookups |
| `shipment_discrepancies` | `(claim_filed)` | Partial B-tree | Unfiled claims |
| `shipment_discrepancies` | `(shipment_id)` | B-tree | Shipment lookups |
| `ads_performance` | `(acos)` | Partial B-tree | WHERE acos > 0.25 |
| `ads_performance` | `(store_id, date)` | B-tree | Date range queries |
| `profit_daily_snapshot` | `(store_id, date)` | B-tree | Daily profit queries |
| `profit_daily_snapshot` | `(margin_pct)` | B-tree | Margin filtering |
| `profit_daily_snapshot` | `(sku, date)` | B-tree | SKU trends |
| `customer_feedback` | `(asin)` | B-tree | ASIN analysis |
| `customer_feedback` | `(sentiment)` | B-tree | Sentiment filtering |
| `customer_feedback` | `(feedback_date)` | B-tree | Time-based queries |
| `recommendation_log` | `(human_action)` | B-tree | Pending approvals |
| `recommendation_log` | `(store_id)` | B-tree | Store isolation |
| `audit_log` | `(created_at)` | B-tree | Audit trail queries |
| `expenses` | `(store_id, expense_date)` | B-tree | Cash flow queries |
| `expenses` | `(expense_type)` | B-tree | Category filtering |
| `expenses` | `(payment_status)` | B-tree | Unpaid/payment tracking |

## Generated Columns

| Table | Column | Formula | Purpose |
|---|---|---|---|
| `profit_daily_snapshot` | `net_profit` | `revenue - product_cost - fba_fees - ...` | True profit |
| `profit_daily_snapshot` | `margin_pct` | `net_profit / revenue` | Profitability |
| `profit_daily_snapshot` | `tacos` | `advertising_spend / revenue` | Ad efficiency |
| `ads_performance` | `acos` | `spend / sales` | Campaign health |
| `ads_performance` | `ctr` | `clicks / impressions` | Engagement |
| `ads_performance` | `cvr` | `orders / clicks` | Conversion |

```

## New Table: Expenses (Manual Entry)

```
┌──────────────────────┐
│      EXPENSES         │
├──────────────────────┤
│ id (PK)               │
│ store_id (FK)         │
│ marketplace_id        │
│ expense_date          │
│ expense_type          │  ← cogs, logistics, salary, software...
│ category              │
│ description           │
│ vendor                │
│ invoice_number        │
│ amount                │
│ tax_amount            │
│ payment_status        │  ← unpaid / paid / partial
│ payment_date          │
│ is_recurring          │
│ attachment_path       │  ← invoice file path
│ created_by            │
│ raw_data (JSONB)      │
│ created_at            │
└──────────────────────┘

## Views

| View | Purpose | Source Tables |
|---|---|---|
| `v_profit_summary` | Daily profit by store | `profit_daily_snapshot` |
| `v_pending_claims` | Unfiled claims sorted by loss | `shipment_discrepancies` |
| `v_inventory_risk` | Low/overstock SKUs | `inventory_snapshot` |
