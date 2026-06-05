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
from db.connection import get_session
# lazy import inside route handlers
from auth.rbac import AdminRole

logger = logging.getLogger("amazon.api")

app = FastAPI(
    title="Amazon Operations Intelligence Platform",
    version="1.0.0",
    description="Multi-store Amazon FBA management with RBAC.",
)


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
    try:
        check_store_access(user_id, store_id, action)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


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
        ],
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
    rbac = get_rbac()
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
    rbac = get_rbac()
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
    rbac = get_rbac()
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
    rbac = get_rbac()
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


@app.on_event("startup")
async def startup():
    try:
        registry = get_store_registry()
        rbac = get_rbac()
        logger.info("API started. Stores: %s | Users: %s",
                     list(registry.active_stores.keys()) if registry.active_stores else "none",
                     [u.user_id for u in rbac.list_users()])
    except Exception as e:
        logger.warning("Startup with limited config: %s", e)


@app.on_event("shutdown")
async def shutdown():
    from auth import close as close_auth
    await close_auth()
