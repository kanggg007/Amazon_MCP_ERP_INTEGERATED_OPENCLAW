# Implementation Plan

## Phase Strategy

```
Phase 1 — Foundation (Week 1)
    ↓
Phase 2 — Data Layer (Week 2)
    ↓
Phase 3 — Intelligence Layer (Week 3)
    ↓
Phase 4 — OpenClaw Integration (Week 4)
    ↓
Phase 5 — Production Hardening (Week 5)
```

---

## Phase 1: Foundation (Week 1)

### Goal
Establish the core infrastructure: authentication, RBAC, database, and project skeleton.

### Tasks

| # | Task | Deliverable | Effort | Dependencies |
|---|---|---|---|---|
| 1.1 | Set up project structure | Folder structure, package init files | 0.5d | — |
| 1.2 | Configure PostgreSQL | Connection pool, migrations setup | 0.5d | 1.1 |
| 1.3 | Implement `schema.sql` | All 10+ tables, indexes, views, triggers | 1d | 1.2 |
| 1.4 | Generate SQLAlchemy models | `models.py` matching `schema.sql` | 0.5d | 1.3 |
| 1.5 | Set up Secrets Manager | `secrets.py`, `.env.example`, `settings.py` | 0.5d | 1.1 |
| 1.6 | Implement Token Manager | `token_manager.py` — refresh, cache, retry | 1d | 1.5 |
| 1.7 | Implement AWS Signer | `aws_signer.py` — Signature V4 | 0.5d | 1.5 |
| 1.8 | Implement Store Registry | `store_registry.py` — multi-marketplace | 0.5d | 1.5 |
| 1.9 | Implement RBAC | `rbac.py` — Super Admin, Store Admin, Analyst, Auditor | 1d | 1.8 |
| 1.10 | Wire up auth `__init__.py` | Public API: `get_access_token()`, `check_store_access()` | 0.5d | 1.6-1.9 |
| 1.11 | Write tests for auth | `test_auth.py`, `test_token_manager.py`, `test_rbac.py` | 1d | 1.10 |

**Phase 1 Gate:** Auth layer passes all tests. Token refresh works end-to-end. RBAC enforces store isolation.

---

## Phase 2: Data Layer (Week 2)

### Goal
Ingest, normalize, and store data from Amazon SP-API (and later Walmart/TikTok).

### Tasks

| # | Task | Deliverable | Effort | Dependencies |
|---|---|---|---|---|
| 2.1 | Implement SP-API client | `sp_api_client.py` with rate limiter | 1d | 1.10 |
| 2.2 | Implement Walmart client | `walmart_api_client.py` | 0.5d | 1.10 |
| 2.3 | Implement TikTok client | `tiktok_api_client.py` | 0.5d | 1.10 |
| 2.4 | Build Orders Fetcher | `orders_fetcher.py` — pagination, date ranges | 0.5d | 2.1 |
| 2.5 | Build Inventory Fetcher | `inventory_fetcher.py` — summaries, health | 0.5d | 2.1 |
| 2.6 | Build Shipments Fetcher | `shipments_fetcher.py` — all statuses | 0.5d | 2.1 |
| 2.7 | Implement Data Lake Writer | `data_lake_writer.py` — local/S3, partitioning | 0.5d | 1.1 |
| 2.8 | Build ETL — Orders | `etl_orders.py` — normalize, deduplicate, SKU map | 1d | 1.3, 2.4 |
| 2.9 | Build ETL — Inventory | `etl_inventory.py` — daily snapshots | 0.5d | 1.3, 2.5 |
| 2.10 | Build ETL — Shipments | `etl_shipments.py` — normalize, find discrepancies | 1d | 1.3, 2.6 |
| 2.11 | Build ETL — Ads | `etl_ads.py` — campaign performance | 0.5d | 1.3 |
| 2.12 | Build ETL — Feedback | `etl_feedback.py` — customer comments | 0.5d | 1.3 |
| 2.13 | Write ingestion pipeline | `run_etl.py` — orchestrate all fetchers | 1d | 2.8-2.12 |
| 2.14 | Tests | `test_etl.py` — dedup, normalization | 1d | 2.13 |

**Phase 2 Gate:** Data flows from SP-API to PostgreSQL. ETL pipeline runs on schedule. Raw JSON archived in data lake. Discrepancies detected on ingest.

---

## Phase 3: Intelligence Layer (Week 3)

### Goal
Build the business engines: inventory intelligence, discrepancy detection, profit calculation, ads optimization, voice of customer.

### Tasks

| # | Task | Deliverable | Effort | Dependencies |
|---|---|---|---|---|
| 3.1 | Inventory Engine | `inventory_engine.py` — days of cover, forecasting, low/overstock alerts | 1d | 2.13 |
| 3.2 | Discrepancy Engine | `discrepancy_engine.py` — scan, evidence packs, claim prep | 1d | 2.13 |
| 3.3 | Profit Engine | `profit_engine.py` — SKU-level true profit, all costs included | 1d | 2.13 |
| 3.4 | Ads Engine | `ads_engine.py` — TACoS/ACoS analysis, keyword scoring, recommendations | 1d | 2.13 |
| 3.5 | VoC Engine | `voc_engine.py` — topic classification, complaints, listing suggestions | 1d | 2.13 |
| 3.6 | Engine scheduler script | `run_engines.py` — daily run, store-isolated | 0.5d | 3.1-3.5 |
| 3.7 | Tests | `test_inventory_engine.py`, `test_discrepancy_engine.py`, `test_profit_engine.py`, `test_ads_engine.py`, `test_voc_engine.py` | 2d | 3.1-3.5 |

**Phase 3 Gate:** All engines produce correct results. Inventory alerts fire. Discrepancies flagged with evidence packs. Profit calculated correctly. VoC classifies feedback.

---

## Phase 4: OpenClaw Integration (Week 4)

### Goal
Build the API layer, query engine, approval workflow, and OpenClaw command center.

### Tasks

| # | Task | Deliverable | Effort | Dependencies |
|---|---|---|---|---|
| 4.1 | Query Engine | `query_engine.py` — NL intent mapping, store-isolated | 1.5d | 3.1-3.5 |
| 4.2 | Approval Workflow | `approval_workflow.py` — Level 1/2/3, propose/approve/reject | 1d | 1.9 |
| 4.3 | FastAPI Application | `app.py` — all routes, middleware, error handling | 1d | 4.1, 4.2 |
| 4.4 | Recommendation Log | `recommendation_log.py` — audit trail | 0.5d | 4.2 |
| 4.5 | Action Executor | `action_executor.py` — execute approved actions | 0.5d | 4.2 |
| 4.6 | Dashboard API | `dashboard_api.py` — KPI aggregation | 0.5d | 3.1-3.5 |
| 4.7 | Alert Engine | `alert_engine.py` — rules, Slack/email dispatch | 1d | 3.1-3.5 |
| 4.8 | Alert Rules | `alert_rules.py` — configurable thresholds | 0.5d | 4.7 |
| 4.9 | Feedback Loop | Outcome tracking, `threshold_adjuster.py` | 1d | 4.2 |
| 4.10 | Integration tests | `test_api.py`, `test_approval_workflow.py` | 1d | 4.3 |
| 4.11 | OpenClaw connect | Configure Feishu/WeChat channel | 0.5d | 4.3 |
| 4.12 | User documentation | README, API docs | 0.5d | 4.11 |

**Phase 4 Gate:** OpenClaw answers queries from Feishu. Approval workflow enforces levels. Alerts fire via Slack. Dashboard shows accurate KPIs.

---

## Phase 5: Production Hardening (Week 5)

### Goal
Security audit, performance optimization, monitoring, and disaster recovery.

### Tasks

| # | Task | Deliverable | Effort | Dependencies |
|---|---|---|---|---|
| 5.1 | Security audit | Review all secrets handling, token management, RBAC enforcement | 1d | 4.12 |
| 5.2 | Penetration testing | Auth bypass attempts, store isolation bypass, SQL injection | 1d | 5.1 |
| 5.3 | Rate limiting review | SP-API rate limits, API throttling | 0.5d | 5.1 |
| 5.4 | Performance optimization | Query profiling, index tuning, connection pooling | 1d | 4.12 |
| 5.5 | Caching layer | Redis integration for token cache, hot data | 0.5d | 5.4 |
| 5.6 | Monitoring & alerting | Health checks, error rates, latency tracking | 1d | 5.4 |
| 5.7 | Backup & recovery | Database backups, data lake replication | 0.5d | 5.4 |
| 5.8 | Runbooks | Incident response, deployment, rollback | 1d | 5.7 |
| 5.9 | Load testing | Multi-store concurrent access, ETL performance | 1d | 5.4 |
| 5.10 | Production deployment | Docker compose, CI/CD pipeline | 1d | 5.8 |

**Phase 5 Gate:** Security audit passes. Performance meets SLAs. Monitoring covers all services. Backup strategy in place.

---

## Effort Summary

| Phase | Calendar Days | Engineering Days |
|---|---|---|
| 1 — Foundation | 5 | 7.5 |
| 2 — Data Layer | 5 | 8.5 |
| 3 — Intelligence Layer | 5 | 7.5 |
| 4 — OpenClaw Integration | 5 | 9.0 |
| 5 — Production Hardening | 5 | 8.5 |
| **Total** | **25** | **41.0** |

## Critical Path

```
1.6 (Token Manager) → 1.10 (Auth API) → 2.1 (SP-API Client) → 2.13 (ETL Pipeline)
                                                                     ↓
3.1 (Inventory Engine) ──────────────────────────────────────────────┘
3.2 (Discrepancy Engine) ────────────────────────────────────────────┘
3.3 (Profit Engine) ─────────────────────────────────────────────────┘
                                                                     ↓
4.1 (Query Engine) → 4.3 (FastAPI) → 4.11 (OpenClaw) → DONE
```

## Risk Register

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| SP-API token failure (LWA expiry) | Medium | High | Auto-refresh 60s before expiry, alert on failure |
| SP-API rate limiting | Medium | Medium | Configurable rate limiter, exponential backoff |
| Database connection pool exhaustion | Low | High | Pool sizing, connection monitoring |
| RBAC misconfiguration allows cross-store access | Low | Critical | Mandatory store_id on every query, automated tests |
| Data lake outgrows local disk | Medium | Low | S3 integration, data retention policy |
| Approval bypass | Low | Critical | Audit log on every action, Level 3 enforced in code |
