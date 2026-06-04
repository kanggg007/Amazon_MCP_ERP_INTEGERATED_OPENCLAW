# Amazon Operations Intelligence Platform — Architecture

## 1. System Overview

The Amazon Operations Intelligence Platform is an enterprise-grade multi-tenant system for managing Amazon FBA, Walmart, and TikTok Shop operations across multiple stores. It provides inventory intelligence, shipment discrepancy detection, profit analytics, Voice of Customer analysis, ads optimization, and human-in-the-loop approval workflows.

### 1.1 Design Principles

- **Store Isolation:** Every record includes `store_id`. No cross-store data access.
- **Human-in-the-Loop:** No high-risk action executes without human approval.
- **Token Centralization:** Only the auth layer manages tokens. The rest of the system calls `get_access_token(store_id)`.
- **Audit Everything:** Every action is logged with timestamp, user, store, before/after state.
- **Least Privilege:** RBAC ensures users only access their authorized stores.
- **Fail Closed:** Authentication or permission failures deny access by default.

### 1.2 Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        EXTERNAL INTEGRATIONS                         │
├─────────────┬──────────────┬──────────────┬────────────────────────┤
│  Amazon     │   Walmart    │   TikTok     │  OpenClaw / Feishu     │
│  SP-API     │  Marketplace │   Shop API   │  Command Center        │
└──────┬──────┴──────┬───────┴──────┬───────┴───────────┬────────────┘
       │             │              │                   │
       ▼             ▼              ▼                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       GATEWAY / API LAYER                            │
├─────────────────────────────────────────────────────────────────────┤
│  API Gateway  │  Auth Middleware  │  Rate Limiter  │  Audit Logger   │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     APPLICATION SERVICES                              │
├────────────────────┬──────────────────────┬────────────────────────┤
│  Auth Service      │  Token Manager        │  RBAC Service          │
│  - LWA auth        │  - Token cache         │  - Role management     │
│  - IAM signing     │  - Auto-refresh        │  - Store assignment    │
│  - Store registry  │  - Failure alerts      │  - Permission checks   │
├────────────────────┼──────────────────────┼────────────────────────┤
│  Ingestion Service  │  ETL Service          │  Data Lake Writer      │
│  - Orders           │  - Normalization      │  - Raw JSON storage    │
│  - Inventory        │  - Deduplication      │  - S3/local fallback   │
│  - Shipments        │  - SKU mapping        │  - Partition by store  │
│  - Ads              │  - Validation         │                       │
├────────────────────┼──────────────────────┼────────────────────────┤
│  Intelligence Engines (Store-Isolated)                               │
│  - Inventory Engine  │  Discrepancy Engine  │  Profit Engine        │
│  - Ads Engine        │  VoC Engine          │                     │
├────────────────────┼──────────────────────┼────────────────────────┤
│  Approval Service   │  Recommendation Log  │  Feedback Loop         │
│  - Level 1/2/3      │  - All actions        │  - Outcome tracking   │
│  - Propose/Approve  │  - Before/after       │  - Threshold learning │
├────────────────────┼──────────────────────┼────────────────────────┤
│  Alert Service      │  Dashboard API       │  Query Engine          │
│  - Slack/email      │  - KPI aggregation   │  - NL→SQL mapping     │
│  - Rule evaluation  │  - Multi-store views  │  - Confidence scoring │
└────────────────────┴──────────────────────┴────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         DATA LAYER                                   │
├──────────────────────────────┬──────────────────────────────────────┤
│  PostgreSQL (Primary)        │  Redis (Cache)                       │
│  - stores                    │  - Token cache                       │
│  - inventory_snapshot        │  - Rate limit state                  │
│  - orders + order_items      │  - Session cache                     │
│  - inbound_shipments         │                                      │
│  - shipment_discrepancies    │                                      │
│  - ads_performance           │                                      │
│  - profit_daily_snapshot     │                                      │
│  - customer_feedback         │                                      │
│  - recommendation_log        │                                      │
│  - audit_log                 │                                      │
├──────────────────────────────┴──────────────────────────────────────┤
│  Data Lake (Local/S3)                                                │
│  - Raw SP-API JSON partitioned by store_id/source/date              │
└─────────────────────────────────────────────────────────────────────┘
```

## 1.3 Financial Reporting Architecture

The platform auto-generates monthly **P&L (Profit & Loss)** and **Cash Flow** statements per store.

### P&L Statement (Monthly)

| Line Item | Source |
|---|---|
| **Revenue** | `orders.order_total_amount` (aggregated monthly) |
| **COGS** | `products.unit_cost × order_items.quantity_ordered` |
| **FBA Fees** | SP-API / `profit_daily_snapshot.fba_fees` |
| **Referral Fees** | SP-API / `profit_daily_snapshot.referral_fees` |
| **Advertising** | `ads_performance.spend` (monthly aggregate) |
| **Shipping Cost** | `profit_daily_snapshot.shipping_cost` |
| **Inbound Shipping** | Upfront logistics costs (manual or via shipment data) |
| **Storage Fees** | SP-API monthly storage fee report |
| **Returns** | `order_items` with refund status |
| **Net Profit** | All of the above aggregated |

### Cash Flow Statement (Monthly)

| Line Item | Source | Timing |
|---|---|---|
| **Sales Receipts** | Orders shipped | T+7 to T+14 (Amazon settlement) |
| **COGS Payments** | Supplier invoices | On payment date |
| **Upfront Logistics** | Prepaid freight / FBA inbound ship cost | On payment date |
| **Ad Spend** | Advertising charges | Weekly/biweekly deduction |
| **FBA Fees** | Amazon settlement deduction | With each settlement |
| **Net Cash Flow** | Inflow - Outflow | Monthly |

### Key Difference: P&L vs Cash Flow

- **P&L** = accrual-based (revenue when earned, costs when incurred)
- **Cash Flow** = cash-based (revenue when received, costs when paid)

The system tracks both and reports the gap (working capital impact).

## 2. Service Boundaries

| Service | Responsibility | Depends On |
|---|---|---|
| **Auth Service** | Token refresh, IAM signing, store registry | Secrets Manager |
| **RBAC Service** | User management, role assignment, permission checks | Auth Service |
| **Ingestion Service** | Pull raw data from SP-API/Walmart/TikTok | Auth Service |
| **ETL Service** | Normalize, deduplicate, validate, persist | Data Lake, PostgreSQL |
| **Inventory Engine** | Days of cover, forecasting, reorder alerts | PostgreSQL |
| **Discrepancy Engine** | Missing/damaged detection, evidence packs | PostgreSQL |
| **Profit Engine** | True SKU profit calculation | PostgreSQL |
| **Ads Engine** | TACoS/ACoS analysis, keyword scoring | PostgreSQL |
| **VoC Engine** | Topic classification, sentiment, complaints | PostgreSQL |
| **Financial Engine** | Monthly P&L (cash/accrual), cash flow | Profit Engine, PO Manager, Expenses |
| **PO Manager** | Manufacturing PO upload, COGS tracking | PostgreSQL |
| **Expense Manager** | Manual invoice entry, recurring costs | PostgreSQL |
| **Approval Service** | Propose, classify risk, route, log | PostgreSQL |
| **Alert Service** | Rule evaluation, dispatch | PostgreSQL |
| **Dashboard API** | KPI aggregation, trend data | PostgreSQL |
| **Query Engine** | Natural language → structured query | PostgreSQL |

## 3. Security Architecture

### 3.1 Authentication Flow

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│ Any      │────▶│ Auth     │────▶│ Token    │────▶│ Cached   │
│ Module   │     │ __init__ │     │ Manager  │     │ Token    │
└──────────┘     └──────────┘     └──────────┘     └──────────┘
                      │                 │
                      │                 ▼
                      │           ┌──────────┐
                      │           │ LWA      │
                      │           │ HTTPS    │
                      │           │ Request  │
                      │           └──────────┘
                      │
                      ▼
               ┌──────────┐
               │ Secrets  │
               │ Loader   │
               │ (env/AWS)│
               └──────────┘
```

- **Only entry point:** `get_access_token(store_id)`
- **Secret storage:** Environment variables (dev) or AWS Secrets Manager (prod)
- **Never hardcoded:** No credentials in source code

### 3.2 Authorization Flow (RBAC)

```
User Request
    │
    ▼
┌─────────────────────┐
│ Auth Middleware      │
│ - Extract user_id    │
│ - Extract store_id   │
│ - Extract action     │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ RBAC.check_access() │
├─────────────────────┤
│ MASTER: all stores  │
│ STORE: assigned     │
│ ANALYST: read-only  │
│ AUDITOR: audit logs │
└─────────┬───────────┘
          │
     ┌────┴────┐
     │         │
     ▼         ▼
   Allow     Deny 403
```

### 3.3 Approval Flow

```
┌──────────┐    Level 1    ┌──────────┐
│ Propose   │─────────────▶│ Auto     │
│ Action    │              │ Execute  │
└─────┬────┘              └──────────┘
      │ Level 2/3
      ▼
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ Pending  │───▶│ Master   │───▶│ Approved │───▶│ Execute  │
│ Queue    │    │ Review   │    │ /Rejected│    │ Action   │
└──────────┘    └──────────┘    └──────────┘    └──────────┘
                                                   │
                                                   ▼
                                            ┌──────────┐
                                            │ Feedback │
                                            │ Loop     │
                                            └──────────┘
```

| Level | Type | Auto | Examples |
|---|---|---|---|
| 1 | Auto | ✅ | Reports, analytics, alerts |
| 2 | Approval Required | ❌ | Ad bid changes, FBA claims, reorder |
| 3 | Never Automatic | ❌ | Pricing changes, listing removal, account settings |

## 4. Token Management Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                     TOKEN MANAGER                                │
├──────────────────────────────────────────────────────────────────┤
│  Public API:                                                     │
│    get_access_token(store_id) → str                              │
│                                                                  │
│  Private:                                                        │
│    _refresh_token(store_id) → str                                │
│    _cache: Dict[store_id, TokenEntry]                            │
│    _locks: Dict[store_id, asyncio.Lock]                          │
├──────────────────────────────────────────────────────────────────┤
│  Per-store token isolation                                       │
│  Automatic refresh 60s before expiry                             │
│  Exponential backoff (3 retries)                                 │
│  Alert on failure                                                │
└──────────────────────────────────────────────────────────────────┘
```

**Key properties:**
- One `TokenEntry` per store with `access_token`, `expires_at`, `refreshed_at`
- Lock per store prevents concurrent refreshes for the same store
- 60-second buffer before expiry to prevent edge cases
- All tokens isolated — Store A's refresh never affects Store B

## 5. Multi-Tenant RBAC

| Role | Read Stores | Write Stores | Manage Users | Approve Actions | Audit |
|---|---|---|---|---|---|
| **Super Admin** | All | All (with approval) | ✅ | ✅ | ✅ |
| **Store Admin** | Assigned only | Assigned only (with approval) | ❌ | ❌ | ✅ |
| **Analyst** | Assigned only | ❌ | ❌ | ❌ | ❌ |
| **Auditor** | All (read-only) | ❌ | ❌ | ❌ | ✅ (full access) |

## 6. Component Interactions

```
OpenClaw/Feishu
    │
    │ POST /query "Show reimbursement opportunities"
    ▼
Query Engine
    │
    ├── RBAC.check_read_access(user, store)
    ├── DiscrepancyEngine.scan_shipments(store_id)
    ├── Returns discrepancies + recommendation
    │
    │ POST /action/propose {type: "file_claim", target: "shipment_123"}
    ▼
ApprovalWorkflow.propose("file_claim", ...)
    │
    ├── Classify risk: "medium" → Level 2
    ├── Log to recommendation_log
    ├── Return "pending_approval"
    │
    │ POST /action/approve {action_id: "...", approved: true}
    ▼
ApprovalWorkflow.approve(...)
    │
    ├── DiscrepancyEngine.record_claim_filed(...)
    ├── Log to feedback_loop
    └── AlertService.send("Claim filed: shipment_123")
```
