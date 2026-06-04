# Folder Structure

```
amazon-ops-intel/
│
├── README.md
├── .env                        # Environment variables (never committed)
├── .env.example                # Template for environment variables
├── requirements.txt            # Python dependencies
├── docker-compose.yml          # PostgreSQL + Redis + API
├── Makefile                    # Common commands
│
├── docs/
│   ├── architecture.md         # System architecture document
│   ├── folder_structure.md     # This file
│   ├── database_erd.md         # Database ERD document
│   ├── api_contracts.md        # API contract document
│   └── implementation_plan.md  # Phased implementation plan
│
├── auth/                       # Authentication & Authorization Layer
│   ├── __init__.py             # Public API: get_access_token(), get_rbac()
│   ├── token_manager.py        # LWA token refresh, caching, retry
│   ├── aws_signer.py           # AWS Signature V4 signing
│   ├── store_registry.py       # Store configuration & marketplaces
│   └── rbac.py                 # Role-based access control
│
├── clients/                    # External API Clients
│   ├── __init__.py
│   ├── rate_limiter.py         # Token-bucket rate limiter
│   ├── sp_api_client.py        # Amazon SP-API client
│   ├── walmart_api_client.py   # Walmart Marketplace client
│   └── tiktok_api_client.py    # TikTok Shop client
│
├── config/                     # Configuration
│   ├── __init__.py
│   ├── settings.py             # Pydantic settings (all from env)
│   └── secrets.py              # Secrets loader (env / AWS Secrets Manager)
│
├── db/                         # Database Layer
│   ├── __init__.py
│   ├── schema.sql              # Full PostgreSQL DDL
│   ├── models.py               # SQLAlchemy ORM models
│   ├── connection.py           # Connection pool
│   └── migrations/             # Alembic migrations
│       ├── env.py
│       └── versions/
│
├── data/                       # Data Ingestion (from SP-API)
│   ├── __init__.py
│   ├── base_fetcher.py         # Retry, backoff, storage
│   ├── orders_fetcher.py       # Order ingestion
│   ├── inventory_fetcher.py    # Inventory ingestion
│   ├── shipments_fetcher.py    # FBA inbound shipment ingestion
│   └── data_lake_writer.py     # Raw JSON storage to local/S3
│
├── etl/                        # ETL Pipelines
│   ├── __init__.py             # Base deduplicator, fingerprint
│   ├── etl_orders.py           # Order normalization
│   ├── etl_inventory.py        # Inventory normalization
│   ├── etl_shipments.py        # Shipment normalization + discrepancy
│   ├── etl_ads.py              # Ad performance normalization
│   └── etl_feedback.py         # Customer feedback ingestion
│
├── engines/                    # Business Intelligence Engines
│   ├── __init__.py
│   ├── inventory_engine.py     # Days of cover, forecasting, alerts
│   ├── discrepancy_engine.py   # Missing/damaged detection, claims
│   ├── profit_engine.py        # True SKU profit calculation
│   ├── ads_engine.py           # TACoS/ACoS, keyword scoring
│   └── voc_engine.py           # Voice of Customer classification
│
├── api/                        # API Layer (FastAPI + OpenClaw)
│   ├── __init__.py
│   ├── app.py                  # FastAPI application, routes
│   ├── query_engine.py         # Natural language → structured query
│   ├── approval_workflow.py    # Level 1/2/3 approval system
│   ├── dashboard_api.py        # KPI aggregation
│   └── action_executor.py      # Execute approved actions
│
├── feedback/                   # Feedback Loop
│   ├── __init__.py
│   ├── recommendation_log.py   # Log all recommendations
│   ├── outcome_tracker.py      # Track accepted/rejected
│   └── threshold_adjuster.py   # Learn from outcomes
│
├── alerts/                     # Alert System
│   ├── __init__.py
│   ├── alert_engine.py          # Rule evaluation + dispatch
│   ├── alert_rules.py           # Configurable thresholds
│   └── alert_dispatcher.py     # Slack/email/webhook
│
├── scripts/                    # Utility scripts
│   ├── seed_stores.py          # Load store config from env
│   ├── run_etl.py              # Run ETL on schedule
│   ├── run_engines.py          # Run intelligence engines
│   └── init_db.py              # Initialize database schema
│
└── tests/                      # Test suite
    ├── conftest.py             # Fixtures, test database
    ├── test_auth.py
    ├── test_token_manager.py
    ├── test_rbac.py
    ├── test_etl.py
    ├── test_inventory_engine.py
    ├── test_discrepancy_engine.py
    ├── test_profit_engine.py
    ├── test_ads_engine.py
    ├── test_voc_engine.py
    ├── test_approval_workflow.py
    └── test_api.py
```

## Module Ownership

| Module | Owner | Description |
|---|---|---|
| `auth/` | Platform | Token management, IAM signing, RBAC |
| `clients/` | Platform | External API clients |
| `config/` | Platform | Configuration, secrets |
| `db/` | Platform | Database schema, models |
| `data/` | Data Engineering | SP-API data ingestion |
| `etl/` | Data Engineering | Data normalization pipelines |
| `engines/` | Data Science | Business intelligence logic |
| `api/` | Backend | API server, query engine |
| `feedback/` | Data Science | Learning system |
| `alerts/` | Platform | Notification system |
| `tests/` | QA | Test coverage |
