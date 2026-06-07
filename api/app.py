"""
FastAPI Application — Amazon Operations Intelligence Platform
==============================================================
OpenClaw command center and API layer.
Includes RBAC: Master Admin and Sub Admin permission control.
"""

import logging
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from api.query_engine import QueryEngine
from api.approval_workflow import ApprovalWorkflow
from engines.inventory_engine import InventoryEngine
from engines.discrepancy_engine import DiscrepancyEngine
from engines.ads_engine import AdsEngine
from engines.voc_engine import VoCEngine
from engines.return_refund_engine import ReturnRefundEngine
from engines.profit_leakage_engine import ProfitLeakageEngine
from engines.inventory_autopilot_engine import InventoryAutopilotEngine
from engines.cost_engine import CostEngine
from engines.ccis_engine import CCISEngine
from engines.reimbursement_engine import ReimbursementEngine
from api.direct_finances import router as direct_finances_router
from db.connection import get_session
# lazy import inside route handlers
from auth import get_store_registry
from auth.rbac import AdminRole, RBACManager, AdminUser

logger = logging.getLogger("amazon.api")

# Router
from api.direct_finances import router as direct_finances_router
app = FastAPI(
    title="Amazon Operations Intelligence Platform",
    version="1.0.0",
    description="Multi-store Amazon FBA management with RBAC.",
)
app.include_router(direct_finances_router)


# ─── Schemas ───

class QueryRequest(BaseModel):
    user_id: str = "master"
    store_id: str
    query: str

class QueryResponse(BaseModel):
    status: str = "ok"
    data: Dict

class ActionRequest(BaseModel):
    user_id: str = "master"
    store_id: str
    action_type: str
    message: str
    confidence: float = 0.75
    metadata: Optional[Dict] = None

class ActionResponse(BaseModel):
    status: str
    data: Dict

class ApproveRequest(BaseModel):
    store_id: str
    action_id: str
    approved: bool
    notes: Optional[str] = None
    actioned_by: str = "master"

class CreateUserRequest(BaseModel):
    user_id: str
    username: str
    assigned_stores: List[str] = Field(default_factory=list)
    created_by: str = "master"


# ─── Dependencies ───

def get_db():
    db = get_session()
    try:
        yield db
    finally:
        db.close()


def require_store_access(user_id: str, store_id: str, action: str = "read"):
    """Validate user has access to the given store."""
    registry = get_store_registry()
    if not registry.is_valid_store(store_id):
        raise HTTPException(
            status_code=404,
            detail=f"Store '{store_id}' not found. Available: {list(registry.active_stores.keys())}",
        )
    # Simple RBAC: Master has full access
    rbac = RBACManager()
    if action == "read":
        if not rbac.check_read_access(user_id, store_id):
            raise HTTPException(status_code=403, detail=f"User '{user_id}' cannot read store '{store_id}'")
    elif action == "write":
        if not rbac.check_write_access(user_id, store_id):
            raise HTTPException(status_code=403, detail=f"User '{user_id}' cannot write to store '{store_id}'")


# ─── Routes ───

@app.get("/")
async def root():
    from auth import get_store_registry
    try:
        stores = list(get_store_registry().active_stores.keys())
    except:
        stores = []
    return {
        "service": "Amazon Operations Intelligence Platform",
        "version": "1.0.0",
        "stores": stores,
        "endpoints": [
            "POST /query — Natural language query",
            "POST /action/propose — Propose action",
            "POST /action/approve — Approve/reject action",
            "GET /actions/pending — List pending approvals",
            "POST /users/create — Create sub-admin (Master only)",
            "GET /users — List all users (Master only)",
            "DELETE /users/{user_id} — Deactivate user (Master only)",
            "GET /dashboard/{store_id} — Store dashboard",
            "POST /finances/refunds — Fetch refunds from Finances API",
            "POST /finances/reconcile — Reconcile loss events",
            "GET /finances/loss/{store_id} — Profit leakage analysis",
            "GET /finances/summary/{store_id} — Return/refund summary",
            "GET /pld/leakage/{store_id} — Profit leakage by SKU",
            "GET /pld/sku/{store_id}/{sku} — True profit for one SKU",
            "GET /pld/scan/{store_id} — Scan all SKUs (min_loss filter)",
            "GET /pld/patterns/{store_id}/{sku} — AI pattern detection",
            "POST /pld/refresh-fba-rate/{store_id} — Refresh FBA fee rate per SKU",
            "GET /pld/daily-profit/{store_id} — Daily estimated P&L with settlement correction",
            "POST /rds/scan — Scan for reimbursement opportunities",
            "GET /rds/queue/{store_id} — Recovery queue",
            "GET /rds/summary/{store_id} — Total recoverable value",
            "GET /rds/evidence/{store_id}/{opp_id} — Evidence pack",
            "POST /rds/file/{opp_id} — Mark opportunity as filed",
            "GET /voc/dashboard/{store_id} — VoC intelligence dashboard",
            "GET /voc/profit-impact/{store_id} — Complaints → profit correlation",
            "GET /voc/trend/{store_id} — Monthly complaint trends",
            "GET /voc/by-source/{store_id} — Feedback sources ranked by tier",
            "POST /ccis/process — Classify + draft + approve buyer message",
            "GET /ccis/dashboard/{store_id} — CCIS intelligence dashboard",
            "GET /ccis/classify — Quick message classification",
            "POST /ccis/playbook — Create support playbook template",        ],
    }


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest, db: DBSession = Depends(get_db)):
    """Natural language query. RBAC-checked per store."""
    require_store_access(req.user_id, req.store_id, "read")
    engine = QueryEngine(db, req.store_id)
    result = engine.query(req.query)
    return QueryResponse(data={**result, "requested_by": req.user_id})


@app.post("/action/propose", response_model=ActionResponse)
async def propose_action(req: ActionRequest, db: DBSession = Depends(get_db)):
    """Propose an action. Requires write access. All writes need Level 2 approval."""
    require_store_access(req.user_id, req.store_id, "write")
    workflow = ApprovalWorkflow(db, req.store_id, actioned_by=req.user_id)
    result = workflow.propose(
        action_type=req.action_type,
        message=req.message,
        confidence=req.confidence,
        metadata=req.metadata,
    )
    return ActionResponse(status=result["status"], data={**result, "requested_by": req.user_id})


@app.post("/action/approve", response_model=ActionResponse)
async def approve_action(req: ApproveRequest, db: DBSession = Depends(get_db)):
    """Approve or reject a pending action. Master Admin only."""
    rbac = RBACManager()
    if not rbac.check_manage_users(req.actioned_by):
        raise HTTPException(status_code=403, detail="Only Master Admin can approve actions.")
    require_store_access(req.actioned_by, req.store_id, "write")
    workflow = ApprovalWorkflow(db, req.store_id, actioned_by=req.actioned_by)
    result = workflow.approve(
        action_id=req.action_id,
        approved=req.approved,
        notes=req.notes,
    )
    return ActionResponse(status=result.get("status", "ok"), data=result)


@app.get("/actions/pending")
async def pending_actions(store_id: str, user_id: str = "master",
                          db: DBSession = Depends(get_db)):
    """List pending actions for a store the user can access."""
    require_store_access(user_id, store_id, "read")
    workflow = ApprovalWorkflow(db, store_id)
    actions = workflow.get_pending()
    return {"status": "ok", "actions": actions, "user_id": user_id}


# ─── User Management (Master Admin Only) ───

@app.post("/users/create")
async def create_user(req: CreateUserRequest):
    """Create a sub-admin user. Master Admin only."""
    rbac = RBACManager()
    if not rbac.check_manage_users(req.created_by):
        raise HTTPException(status_code=403, detail="Only Master Admin can create users.")

    # Validate stores exist
    registry = get_store_registry()
    for s in req.assigned_stores:
        if not registry.is_valid_store(s):
            raise HTTPException(status_code=400, detail=f"Store '{s}' does not exist.")

    try:
        user = rbac.create_sub_admin(
            user_id=req.user_id,
            username=req.username,
            assigned_stores=req.assigned_stores,
            created_by=req.created_by,
        )
        return {"status": "ok", "user": user.model_dump()}
    except (ValueError, PermissionError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/users")
async def list_users(user_id: str = "master"):
    """List all users. Master Admin only."""
    rbac = RBACManager()
    if not rbac.check_manage_users(user_id):
        raise HTTPException(status_code=403, detail="Only Master Admin can list users.")
    users = rbac.list_users()
    return {
        "status": "ok",
        "users": [u.model_dump() for u in users],
        "requested_by": user_id,
    }


@app.delete("/users/{target_user_id}")
async def deactivate_user(target_user_id: str, actioned_by: str = "master"):
    """Deactivate a user. Master Admin only."""
    rbac = RBACManager()
    if not rbac.check_manage_users(actioned_by):
        raise HTTPException(status_code=403, detail="Only Master Admin can deactivate users.")
    try:
        result = rbac.deactivate_user(target_user_id, actioned_by)
        return {"status": "ok", "deactivated": target_user_id, "actioned_by": actioned_by}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


# ─── Dashboard ───

@app.get("/dashboard/{store_id}")
async def dashboard(store_id: str, user_id: str = "master",
                    db: DBSession = Depends(get_db)):
    """Store dashboard KPIs. RBAC-checked."""
    require_store_access(user_id, store_id, "read")

    inv_engine = InventoryEngine(db, store_id)
    alerts = inv_engine.analyze()
    critical = len([a for a in alerts if a["severity"] == "critical"])
    warnings = len([a for a in alerts if a["severity"] == "warning"])

    disc_engine = DiscrepancyEngine(db, store_id)
    discrepancies = disc_engine.scan_shipments()
    total_recovery = sum(d["estimated_loss"] for d in discrepancies)

    ads_engine = AdsEngine(db, store_id)
    try:
        tacos = ads_engine.calculate_tacos()
    except Exception:
        tacos = {"error": "No ad data"}

    voc = VoCEngine(db, store_id)
    try:
        complaints = voc.top_complaints(days=30, limit=3)
    except Exception:
        complaints = []

    return {
        "store_id": store_id,
        "requested_by": user_id,
        "inventory": {"critical_alerts": critical, "warning_alerts": warnings, "total": len(alerts)},
        "reimbursement": {"open": len(discrepancies), "estimated_recovery": round(total_recovery, 2)},
        "ads": tacos,
        "voc": {"top_complaints": complaints},
    }


# ─── Return & Refund Engine Routes ───


class FetchRefundsRequest(BaseModel):
    user_id: str = "master"
    store_id: str
    posted_after: Optional[str] = None
    posted_before: Optional[str] = None
    max_results: int = 100


@app.post("/finances/refunds")
async def fetch_refunds(req: FetchRefundsRequest):
    """Pull refund events from SP-API Finances API."""
    require_store_access(req.user_id, req.store_id, "read")
    engine = ReturnRefundEngine(req.store_id)
    events = engine.fetch_refunds_from_finances(
        posted_after=req.posted_after,
        posted_before=req.posted_before,
        max_results=req.max_results,
    )
    stored = engine.store_refund_events(events)
    return {
        "status": "ok",
        "fetched": len(events),
        "stored": stored,
        "store_id": req.store_id,
        "requested_by": req.user_id,
    }


@app.post("/finances/reconcile")
async def reconcile_losses(store_id: str, user_id: str = "master"):
    """Reconcile refunds → returns → loss events for AI analysis."""
    require_store_access(user_id, store_id, "write")
    engine = ReturnRefundEngine(store_id)
    reconciled = engine.reconcile_loss_events()
    return {
        "status": "ok",
        "reconciled": reconciled,
        "store_id": store_id,
        "requested_by": user_id,
    }


@app.get("/finances/loss/{store_id}")
async def get_profit_leakage(store_id: str, sku: Optional[str] = None, user_id: str = "master"):
    """Get profit leakage analysis by SKU."""
    require_store_access(user_id, store_id, "read")
    engine = ReturnRefundEngine(store_id)
    leakage = engine.get_profit_leakage(sku=sku)
    return {
        "status": "ok",
        "leakage": leakage,
        "store_id": store_id,
        "requested_by": user_id,
    }


@app.get("/finances/summary/{store_id}")
async def get_finance_summary(store_id: str, user_id: str = "master"):
    """Get high-level return/refund summary for dashboard."""
    require_store_access(user_id, store_id, "read")
    engine = ReturnRefundEngine(store_id)
    summary = engine.get_summary()
    return {
        "status": "ok",
        "summary": summary,
        "store_id": store_id,
        "requested_by": user_id,
    }


# ─── Profit Leakage Detection Routes ───


@app.get("/pld/leakage/{store_id}")
async def get_profit_leakage_sku(store_id: str, days: int = 30, user_id: str = "master"):
    """Get profit leakage breakdown by SKU for a store."""
    require_store_access(user_id, store_id, "read")
    engine = ProfitLeakageEngine(store_id)
    results = engine.scan_all_skus(days=days, min_loss=100)
    return {
        "status": "ok",
        "leakage": results,
        "total_leakage": sum(s.get("leakage", 0) for s in results),
        "store_id": store_id,
        "requested_by": user_id,
    }


@app.get("/pld/sku/{store_id}/{sku}")
async def get_sku_profit(store_id: str, sku: str, days: int = 30, user_id: str = "master"):
    """Full true profit + leakage breakdown for one SKU."""
    require_store_access(user_id, store_id, "read")
    engine = ProfitLeakageEngine(store_id)
    result = engine.calculate_sku_profit(sku, days=days)
    return {
        "status": "ok",
        "sku_analysis": result,
        "store_id": store_id,
        "requested_by": user_id,
    }


@app.get("/pld/scan/{store_id}")
async def scan_sku_leakage(store_id: str, days: int = 30, min_loss: float = 100.0, user_id: str = "master"):
    """Scan all SKUs and rank by profit leakage."""
    require_store_access(user_id, store_id, "read")
    engine = ProfitLeakageEngine(store_id)
    results = engine.scan_all_skus(days=days, min_loss=min_loss)
    return {
        "status": "ok",
        "leakage_ranked": results,
        "total_skus": len(results),
        "total_leakage": round(sum(s.get("leakage", 0) for s in results), 2),
        "store_id": store_id,
        "requested_by": user_id,
    }


@app.get("/pld/patterns/{store_id}/{sku}")
async def get_profit_patterns(store_id: str, sku: str, days: int = 90, user_id: str = "master"):
    """AI pattern detection for profit leakage.
    Returns: return reason clusters, wasted ad keywords, unclaimed FBA reimbursements.
    """
    require_store_access(user_id, store_id, "read")
    engine = ProfitLeakageEngine(store_id)
    patterns = engine.detect_patterns(sku, days=days)
    return {
        "status": "ok",
        "patterns": patterns,
        "sku": sku,
        "store_id": store_id,
        "requested_by": user_id,
    }


# ─── FBA Reimbursement Detection Routes ───


@app.get("/rds/scan/{store_id}")
async def scan_reimbursements(store_id: str, days_back: int = 90, user_id: str = "master"):
    """Scan for FBA reimbursement opportunities."""
    require_store_access(user_id, store_id, "read")
    engine = ReimbursementEngine(store_id)
    opportunities = engine.scan_all(days_back=days_back)
    return {
        "status": "ok",
        "opportunities": opportunities,
        "total": len(opportunities),
        "total_value": round(sum(o.get("estimated_value", 0) for o in opportunities), 2),
        "store_id": store_id,
        "requested_by": user_id,
    }


@app.get("/rds/queue/{store_id}")
async def get_recovery_queue(store_id: str, min_value: float = 100.0, user_id: str = "master"):
    """Get pending recovery opportunities sorted by value."""
    require_store_access(user_id, store_id, "read")
    engine = ReimbursementEngine(store_id)
    queue = engine.get_recovery_queue(min_value=min_value)
    return {
        "status": "ok",
        "queue": queue,
        "store_id": store_id,
        "requested_by": user_id,
    }


@app.get("/rds/summary/{store_id}")
async def get_recovery_summary(store_id: str, user_id: str = "master"):
    """Total recoverable value by category."""
    require_store_access(user_id, store_id, "read")
    engine = ReimbursementEngine(store_id)
    summary = engine.get_recovery_summary()
    return {
        "status": "ok",
        "summary": summary,
        "store_id": store_id,
        "requested_by": user_id,
    }


class EvidenceRequest(BaseModel):
    user_id: str = "master"
    store_id: str
    opportunity: dict  # previously scanned opportunity


@app.post("/rds/evidence")
async def generate_evidence(req: EvidenceRequest):
    """Generate an evidence pack for a specific opportunity."""
    require_store_access(req.user_id, req.store_id, "read")
    engine = ReimbursementEngine(req.store_id)
    pack = engine.generate_evidence_pack(req.opportunity)
    return {
        "status": "ok",
        "evidence_pack": pack,
        "store_id": req.store_id,
        "requested_by": req.user_id,
    }


@app.post("/rds/file/{opp_id}")
async def file_opportunity(opp_id: int, store_id: str, user_id: str = "master"):
    """Mark an opportunity as filed (after human approval)."""
    require_store_access(user_id, store_id, "write")
    engine = ReimbursementEngine(store_id)
    success = engine.file_opportunity(opp_id, filed_by=user_id)
    return {
        "status": "ok" if success else "error",
        "opportunity_id": opp_id,
        "store_id": store_id,
        "requested_by": user_id,
    }


# ─── Enhanced VoC Routes ───


@app.get("/voc/dashboard/{store_id}")
async def voc_intelligence(store_id: str, days: int = 90, user_id: str = "master",
                           db: DBSession = Depends(get_db)):
    """Complete VoC intelligence dashboard."""
    require_store_access(user_id, store_id, "read")
    engine = VoCEngine(db, store_id)
    return {
        "status": "ok",
        **engine.intelligence_dashboard(days=days),
        "store_id": store_id,
        "requested_by": user_id,
    }


@app.get("/voc/profit-impact/{store_id}")
async def voc_profit_impact(store_id: str, days: int = 90, user_id: str = "master",
                            db: DBSession = Depends(get_db)):
    """Correlate complaints with estimated profit loss."""
    require_store_access(user_id, store_id, "read")
    engine = VoCEngine(db, store_id)
    impact = engine.complaint_profit_impact(days=days)
    return {
        "status": "ok",
        "profit_impact": impact,
        "store_id": store_id,
        "requested_by": user_id,
    }


@app.get("/voc/trend/{store_id}")
async def voc_trend(store_id: str, topic: Optional[str] = None, weeks: int = 8,
                    user_id: str = "master", db: DBSession = Depends(get_db)):
    """Week-over-week complaint trends."""
    require_store_access(user_id, store_id, "read")
    engine = VoCEngine(db, store_id)
    trend = engine.complaint_trend(topic=topic, weeks=weeks)
    return {
        "status": "ok",
        "trend": trend,
        "store_id": store_id,
        "requested_by": user_id,
    }


@app.get("/voc/by-source/{store_id}")
async def voc_by_source(store_id: str, days: int = 30, user_id: str = "master",
                        db: DBSession = Depends(get_db)):
    """Feedback volume by source tier."""
    require_store_access(user_id, store_id, "read")
    engine = VoCEngine(db, store_id)
    by_source = engine.feedback_by_source(days=days)
    return {
        "status": "ok",
        "by_source": by_source,
        "store_id": store_id,
        "requested_by": user_id,
    }


# ─── Customer Communication Intelligence Routes ───


@app.post("/ccis/process")
async def ccis_process_message(store_id: str, body: str, subject: str = "",
                                amazon_order_id: str = "", sku: str = "",
                                user_id: str = "master"):
    """Full pipeline: classify → risk → draft → approval."""
    require_store_access(user_id, store_id, "write")
    engine = CCISEngine(store_id)
    result = engine.process_message(body, subject, amazon_order_id, sku)
    return {"status": "ok", **result, "store_id": store_id, "requested_by": user_id}


@app.get("/ccis/dashboard/{store_id}")
async def ccis_dashboard(store_id: str, days: int = 30, user_id: str = "master"):
    """CCIS dashboard: top complaints, risk, pending approvals."""
    require_store_access(user_id, store_id, "read")
    engine = CCISEngine(store_id)
    dashboard = engine.get_dashboard(days=days)
    return {"status": "ok", "dashboard": dashboard, "store_id": store_id, "requested_by": user_id}


@app.get("/ccis/classify")
async def ccis_classify(store_id: str, body: str, subject: str = "",
                        user_id: str = "master"):
    """Just classify a message without storing it."""
    require_store_access(user_id, store_id, "read")
    engine = CCISEngine(store_id)
    classification = engine.classify_message(body, subject)
    return {"status": "ok", "classification": classification, "store_id": store_id}


@app.post("/ccis/playbook")
async def ccis_create_playbook(store_id: str, issue_type: str, risk_level: str,
                                approved_resolution: str, template_body: str,
                                template_subject: str = "",
                                requires_approval: bool = True,
                                user_id: str = "master"):
    """Create a support playbook template."""
    require_store_access(user_id, store_id, "write")
    from db.connection import get_session
    from db.models import SupportPlaybook
    session = get_session()
    try:
        pb = SupportPlaybook(
            store_id=store_id, issue_type=issue_type, risk_level=risk_level,
            approved_resolution=approved_resolution, template_body=template_body,
            template_subject=template_subject, requires_approval=requires_approval,
            created_by=user_id)
        session.add(pb)
        session.commit()
        return {"status": "ok", "playbook_id": pb.id, "store_id": store_id}
    finally:
        session.close()


# ─── Two-Tier P&L: Daily Estimate + Settlement Correction ───


@app.get("/pld/fba-rate/{store_id}")
async def get_fba_rate(store_id: str, user_id: str = "master"):
    """Get historical FBA fee rate per SKU for daily profit estimation."""
    require_store_access(user_id, store_id, "read")
    from db.connection import get_engine
    from sqlalchemy import text
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT sku, COUNT(*) as tx_count, 
                   ABS(SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END)) as fees,
                   SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) as revenue
            FROM financial_transactions 
            WHERE store_id = :sid AND transaction_type = 'refund'
            GROUP BY sku
        """), {"sid": store_id}).fetchall()
    rates = {}
    for r in rows:
        if r.revenue and r.revenue > 0:
            rates[r.sku] = {
                "fba_fee_rate": round(r.fees / r.revenue, 4),
                "transactions": r.tx_count,
            }
    return {"status": "ok", "sku_fba_rates": rates, "store_id": store_id, "requested_by": user_id}


@app.post("/pld/estimate-daily/{store_id}")
async def estimate_daily_profit(store_id: str, revenue: float, sku: str = "",
                                 cogs_rate: float = 0.5, ads_rate: float = 0.03,
                                 user_id: str = "master"):
    """Estimate daily profit using historical FBA fee rates.
    Formula: Est. Profit = Revenue * (1 - FBA_rate - COGS_rate - ads_rate)
    """
    require_store_access(user_id, store_id, "read")
    from db.connection import get_engine
    from sqlalchemy import text
    engine = get_engine()
    
    # Get historical FBA rate for this SKU
    fba_rate = 0.50  # default if no data
    if sku:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT ABS(SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END)) / 
                       GREATEST(SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END), 1) as rate
                FROM financial_transactions 
                WHERE store_id = :sid AND sku = :sku AND transaction_type = 'refund'
            """), {"sid": store_id, "sku": sku}).fetchone()
            if row and row.rate:
                fba_rate = float(row.rate)
    
    estimated_fba = revenue * fba_rate
    estimated_cogs = revenue * cogs_rate
    estimated_ads = revenue * ads_rate
    estimated_profit = revenue - estimated_fba - estimated_cogs - estimated_ads
    margin_pct = estimated_profit / revenue if revenue > 0 else 0
    
    return {
        "status": "ok",
        "revenue": round(revenue, 2),
        "fba_rate": round(fba_rate, 4),
        "estimated_fba": round(estimated_fba, 2),
        "estimated_cogs": round(estimated_cogs, 2),
        "estimated_ads": round(estimated_ads, 2),
        "estimated_profit": round(estimated_profit, 2),
        "estimated_margin": round(margin_pct, 4),
        "note": "Weekly settlement will correct these estimates with actual fees",
        "store_id": store_id,
    }
async def startup():
    try:
        from auth import get_store_registry
        registry = get_store_registry()
        rbac = RBACManager()
        logger.info("API started. Stores: %s | Users: %s",
                     list(registry.active_stores.keys()) if registry.active_stores else "none",
                     [u.user_id for u in rbac.list_users()])
    except Exception as e:
        logger.warning("Startup with limited config: %s", e)


@app.on_event("shutdown")
async def shutdown():
    from auth import close as close_auth
    await close_auth()
