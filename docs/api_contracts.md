# API Contracts

## Base URL

```
Production:  https://api.ops.megapower.com/v1
Development: http://localhost:8000
```

## Authentication

All API requests require:
1. A valid **LWA access token** for SP-API calls (managed internally)
2. An **OpenClaw API key** (or Feishu user ID) for platform API calls

### Headers

```
Content-Type: application/json
Authorization: Bearer <openclaw_api_key>
X-User-Id: <user_id>
X-Store-Id: <store_id>
```

## Roles & Permissions

| Endpoint | Super Admin | Store Admin | Analyst | Auditor |
|---|---|---|---|---|
| `POST /query` | All stores | Assigned stores | Assigned stores | All stores (read) |
| `GET /dashboard/{store_id}` | All stores | Assigned stores | Assigned stores | All stores |
| `POST /action/propose` | All stores (write) | Assigned stores (write) | ❌ | ❌ |
| `POST /action/approve` | ✅ All | ❌ | ❌ | ❌ |
| `GET /actions/pending` | All stores | Assigned stores | ❌ | ❌ |
| `POST /users/create` | ✅ | ❌ | ❌ | ❌ |
| `GET /users` | ✅ (all) | ❌ | ❌ | ✅ (audit only) |
| `DELETE /users/{id}` | ✅ | ❌ | ❌ | ❌ |
| `GET /audit/logs` | ✅ | Assigned stores | ❌ | ✅ All |

---

## Endpoints

### 1. Query

```
POST /query
```

Submit a natural language query about store operations.

**Request:**
```json
{
    "user_id": "master",
    "store_id": "01",
    "query": "Show reimbursement opportunities above $500"
}
```

**Response:**
```json
{
    "status": "ok",
    "data": {
        "query": "reimbursement_opportunities",
        "store_id": "01",
        "count": 3,
        "total_estimated_loss": 1245.50,
        "discrepancies": [
            {
                "id": 1,
                "shipment_id": "FBA123ABC",
                "sku": "PROD-001",
                "type": "missing",
                "shipped": 500,
                "received": 480,
                "lost": 20,
                "estimated_loss": 599.80,
                "evidence_ready": false
            }
        ],
        "recommendation": "File FBA claims for 3 discrepancies totaling $1,245.50",
        "confidence": 0.85,
        "approval_required": true
    }
}
```

**Supported Query Intents:**

| Intent | Example | Returns |
|---|---|---|
| `inventory_risks` | "Show top inventory risks" | Low stock, overstock, out-of-stock alerts |
| `reimbursement` | "Show reimbursement opportunities" | Unfiled claim discrepancies |
| `declining_profit` | "Show products with declining profit" | SKUs with margins below threshold |
| `complaints` | "Show biggest customer complaints" | Top negative feedback topics |
| `ad_campaigns` | "What ad campaigns exceeded 25% TACoS?" | Campaigns with ACoS above threshold |
| `reorder` | "Show reorder recommendations for SKU PROD-001" | Reorder quantity, timing |

---

### 2. Dashboard

```
GET /dashboard/{store_id}
```

**Parameters:**
- `store_id` (path, required): Store identifier
- `user_id` (query, required): Requesting user

**Response:**
```json
{
    "store_id": "01",
    "requested_by": "master",
    "inventory": {
        "critical_alerts": 2,
        "warning_alerts": 5,
        "total": 7
    },
    "reimbursement": {
        "open": 3,
        "estimated_recovery": 1245.50
    },
    "ads": {
        "total_ad_spend": 3200.00,
        "total_revenue": 45000.00,
        "TACoS": 0.0711,
        "tacos_alert": false
    },
    "voc": {
        "top_complaints": [
            {"topic": "quality", "count": 12, "pct": 35.0},
            {"topic": "installation", "count": 8, "pct": 23.0},
            {"topic": "shipping_damage", "count": 5, "pct": 15.0}
        ]
    }
}
```

---

### 3. Propose Action

```
POST /action/propose
```

Propose an action. Routes to correct approval level based on risk.

**Request:**
```json
{
    "user_id": "store_admin_1",
    "store_id": "01",
    "action_type": "file_claim",
    "message": "File FBA claim for shipment FBA123ABC — 20 missing units, est. loss $599.80",
    "confidence": 0.85,
    "metadata": {
        "discrepancy_id": 1,
        "shipment_id": "FBA123ABC",
        "estimated_loss": 599.80
    }
}
```

**Response (Level 2 — Pending Approval):**
```json
{
    "status": "pending_approval",
    "data": {
        "action_id": "a1b2c3d4e5f6",
        "status": "pending_approval",
        "risk_level": "medium",
        "approval_level": "required",
        "message": "File FBA claim for shipment FBA123ABC — 20 missing units, est. loss $599.80",
        "confidence": 0.85
    }
}
```

**Response (Level 1 — Auto):**
```json
{
    "status": "auto_executed",
    "data": {
        "action_id": "f6e5d4c3b2a1",
        "status": "auto_executed",
        "risk_level": "low",
        "approval_level": "auto",
        "message": "Inventory report generated",
        "confidence": 0.95
    }
}
```

**Response (Level 3 — Rejected):**
```json
{
    "status": "rejected_never_auto",
    "data": {
        "action_id": "x1y2z3",
        "status": "rejected_never_auto",
        "risk_level": "high",
        "approval_level": "never",
        "message": "Action 'change_price' can never be automatic.",
        "confidence": 0.95
    }
}
```

---

### 4. Approve Action

```
POST /action/approve
```

**Request:**
```json
{
    "store_id": "01",
    "action_id": "a1b2c3d4e5f6",
    "approved": true,
    "notes": "Loss confirmed. Proceed with claim.",
    "actioned_by": "master"
}
```

**Response:**
```json
{
    "status": "approved",
    "data": {
        "action_id": "a1b2c3d4e5f6",
        "status": "approved",
        "actioned_by": "master",
        "notes": "Loss confirmed. Proceed with claim."
    }
}
```

---

### 5. List Pending Actions

```
GET /actions/pending?store_id=01&user_id=master
```

**Response:**
```json
{
    "status": "ok",
    "actions": [
        {
            "action_id": "a1b2c3d4e5f6",
            "type": "file_claim",
            "message": "File FBA claim for shipment FBA123ABC",
            "confidence": 0.85,
            "risk_level": "medium",
            "created_at": "2026-06-04T10:30:00Z"
        }
    ],
    "user_id": "master"
}
```

---

### 6. User Management

**Create Sub-Admin:**
```
POST /users/create
```
```json
{
    "user_id": "store_admin_1",
    "username": "Alice Operations",
    "assigned_stores": ["01", "02"],
    "created_by": "master"
}
```
```json
{
    "status": "ok",
    "user": {
        "user_id": "store_admin_1",
        "username": "Alice Operations",
        "role": "sub_admin",
        "assigned_stores": ["01", "02"],
        "is_active": true
    }
}
```

**List Users (Master/Auditor only):**
```
GET /users?user_id=master
```

**Deactivate User (Master only):**
```
DELETE /users/{user_id}?actioned_by=master
```

---

### 7. Health Check

```
GET /health
```

**Response:**
```json
{
    "status": "ok",
    "version": "1.0.0",
    "active_stores": ["01", "02", "03"],
    "uptime_seconds": 3600
}
```

---

## Error Codes

| Code | HTTP Status | Description |
|---|---|---|
| `STORE_NOT_FOUND` | 404 | Store not configured or inactive |
| `ACCESS_DENIED` | 403 | User lacks permission for this store/action |
| `ACTION_NOT_FOUND` | 404 | Action ID not found for approval |
| `ALREADY_ACTIONED` | 409 | Action already approved/rejected |
| `INVALID_QUERY` | 400 | Natural language query not understood |
| `RATE_LIMITED` | 429 | Too many requests |
| `INTERNAL_ERROR` | 500 | Unexpected system error |

## Rate Limits

| Endpoint Group | Limit | Window |
|---|---|---|
| Query Engine | 60 requests | 1 minute |
| Action Propose | 30 requests | 1 minute |
| Action Approve | 10 requests | 1 minute |
| Dashboard | 120 requests | 1 minute |
| SP-API calls | 10 requests | 1 second (per store) |
