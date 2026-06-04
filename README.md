# Amazon Operations Intelligence Platform

Enterprise-grade multi-store Amazon operations intelligence system.

## Purpose

This is NOT a bot. This is an **operations intelligence platform** that supports:

- Multi-store Amazon FBA management
- Walmart marketplace management
- TikTok Shop management
- Voice of Customer intelligence
- Inventory intelligence
- Shipment discrepancy detection
- Ads optimization
- Human approval workflow
- OpenClaw integration
- PostgreSQL data warehouse

## Architecture Principle

```
Data → Rules → AI → Human Approval → Execution → Feedback
```

**Never bypass this flow.**

## System Modules

```
amazon-ops-intel/
├── auth/              # Layer 1 — Centralized token management
├── clients/           # Layer 2 — SP-API, Walmart, TikTok clients
├── db/                # Layer 3 — PostgreSQL schema + ORM
├── etl/               # Layer 4 — Data normalization
├── engines/           # Layers 5-9 — Business logic
│   ├── inventory_engine.py    # Days of cover, forecasting, reorder
│   ├── discrepancy_engine.py  # Missing/damaged units, claims
│   ├── profit_engine.py       # True SKU-level profit
│   ├── ads_engine.py          # TACoS, ACoS, keyword scoring
│   └── voc_engine.py          # Voice of Customer classification
├── api/               # Layer 10 — FastAPI + OpenClaw interface
├── feedback/          # Layer 11 — Learning system
├── alerts/            # Layer 12 — Slack/email dispatch
├── config/            # Infrastructure
└── tests/             # Quality
```

## Approval Levels

| Level | Type | Examples |
|---|---|---|
| **1 — Auto** | No approval needed | Reports, analytics, alerts |
| **2 — Required** | Human must approve | Ad changes, claims, reorder |
| **3 — Never** | Never automatic | Pricing, listing removal, account |

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up database
createdb amazon_ops
psql -d amazon_ops -f db/schema.sql

# 3. Configure credentials
cp .env.example .env
# Edit .env with your store credentials

# 4. Run the API
uvicorn api.app:app --reload

# 5. Query via OpenClaw
curl -X POST "http://localhost:8000/query" \
  -H "Content-Type: application/json" \
  -d '{"store_id":"store_a","query":"Show top inventory risks"}'
```

## Multi-Store Isolation

Every record includes `store_id` and `marketplace_id`.
No cross-store data mixing. Every store operates independently.

## Security

- No credentials hardcoded
- Secrets via environment variables or AWS Secrets Manager
- Centralized token management — only `get_access_token(store_id)` exposed
- Every action logged with timestamp, user, store_id, before/after, status
