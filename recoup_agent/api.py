from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict

from . import db
from .pipeline import compute_findings

def verify_token(authorization: str = Header(...)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization.split("Bearer ")[1]
    if token != "mock-enterprise-token-123":
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"email": "admin@enterprise.com"}

app = FastAPI(title="Recoup API", description="API for the Recoup Revenue Recovery platform")

# Enable CORS for the local React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class StatusUpdate(BaseModel):
    status: str
    reason: str = ""

class UsagePayload(BaseModel):
    customer_id: str
    period: str
    units: int

class InvoicePayload(BaseModel):
    customer_id: str
    period: str
    base_charge: float
    overage_charge: float = 0
    discounts_applied: list = []

class ContractPayload(BaseModel):
    customer_id: str
    customer_name: str
    committed_minimum_monthly: float = 0
    included_units: int = 0
    overage_rate: float = 0
    annual_escalator_pct: float = 0
    escalator_effective_date: str = ""
    discounts: list = []
    clauses: dict = {}

@app.get("/api/findings/pending")
def get_pending_findings(user: dict = Depends(verify_token)) -> List[Dict]:
    """Returns all findings currently awaiting human review."""
    return db.get_pending_findings()

@app.get("/api/findings")
def get_all_findings(user: dict = Depends(verify_token)) -> List[Dict]:
    """Returns all findings across all statuses."""
    return db.get_all_findings()

@app.post("/api/reconcile")
def trigger_reconciliation(period: str = "2026-06", user: dict = Depends(verify_token)):
    """Triggers the reconciliation engine to compute findings and save to DB."""
    try:
        # In a full system, this would trigger the multi-agent ADK run.
        # For the fast API demo, we run the deterministic engine.
        findings = compute_findings(period)
        db.save_findings(findings)
        return {"status": "success", "findings_found": len(findings)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/findings/{finding_id}/approve")
def approve_finding(finding_id: str, user: dict = Depends(verify_token)):
    """Marks a finding as approved in the DB and audit log."""
    db.update_finding_status(finding_id, "approved", f"ui_approval_by_{user.get('email', 'unknown')}")
    return {"status": "approved", "finding_id": finding_id}

@app.post("/api/findings/{finding_id}/reject")
def reject_finding(finding_id: str, update: StatusUpdate, user: dict = Depends(verify_token)):
    """Marks a finding as rejected in the DB and audit log, with an optional reason."""
    db.update_finding_status(finding_id, "rejected", f"ui_rejection_by_{user.get('email', 'unknown')}_{update.reason}")
    return {"status": "rejected", "finding_id": finding_id}

@app.post("/api/ingest/usage")
def ingest_usage(payload: UsagePayload, user: dict = Depends(verify_token)):
    """Ingests usage metrics into the DB."""
    db.save_usage(payload.model_dump())
    return {"status": "success", "message": "Usage ingested successfully"}

@app.post("/api/ingest/invoice")
def ingest_invoice(payload: InvoicePayload, user: dict = Depends(verify_token)):
    """Ingests invoice records into the DB."""
    db.save_invoice(payload.model_dump())
    return {"status": "success", "message": "Invoice ingested successfully"}

@app.post("/api/ingest/contract")
def ingest_contract(payload: ContractPayload, user: dict = Depends(verify_token)):
    """Ingests contract records into the DB. 
    In a full implementation, this might accept a PDF and run Gemini extraction."""
    db.save_contract(payload.model_dump())
    return {"status": "success", "message": "Contract ingested successfully"}

# Run with: uvicorn recoup_agent.api:app --reload
