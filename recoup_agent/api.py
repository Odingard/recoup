from __future__ import annotations

import json
import os
import threading
from typing import List, Dict

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import db
from .pipeline import compute_findings

_firebase_lock = threading.Lock()
_firebase_ready = False


def _sample_mode_enabled() -> bool:
    return os.getenv("RECOUP_SAMPLE_MODE", "").lower() in {"1", "true", "yes", "on"}


def _sample_identity() -> dict:
    return {"uid": "sample", "email": "sample@recoup.local", "account_id": None}


def _firebase_credential():
    from firebase_admin import credentials

    raw = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    if not raw:
        return None
    if os.path.exists(raw):
        with open(raw, "r") as fh:
            raw = fh.read()
    data = json.loads(raw)
    return credentials.Certificate(data)


def _ensure_firebase_app():
    global _firebase_ready
    if _firebase_ready:
        return
    with _firebase_lock:
        if _firebase_ready:
            return
        import firebase_admin

        try:
            firebase_admin.get_app()
        except ValueError:
            options = {}
            project = os.getenv("GOOGLE_CLOUD_PROJECT")
            if project:
                options["projectId"] = project
            cred = _firebase_credential()
            if cred is not None:
                firebase_admin.initialize_app(cred, options or None)
            else:
                firebase_admin.initialize_app(options=options or None)
        _firebase_ready = True


def verify_token(authorization: str | None = Header(default=None)):
    if _sample_mode_enabled():
        return _sample_identity()
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    _ensure_firebase_app()
    from firebase_admin import auth as firebase_auth

    token = authorization.split("Bearer ", 1)[1].strip()
    try:
        decoded = firebase_auth.verify_id_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized")

    uid = decoded.get("uid")
    email = decoded.get("email")
    account_id = decoded.get("account_id") or uid
    return {"uid": uid, "email": email, "account_id": account_id}

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


def _account_id(user: dict) -> str | None:
    return user.get("account_id")


def _offline_findings():
    return compute_findings(account_id=None)

@app.get("/api/findings/pending")
def get_pending_findings(user: dict = Depends(verify_token)) -> List[Dict]:
    """Returns all findings currently awaiting human review."""
    account_id = _account_id(user)
    if account_id is None:
        return _offline_findings()
    return db.get_pending_findings(account_id)

@app.get("/api/findings")
def get_all_findings(user: dict = Depends(verify_token)) -> List[Dict]:
    """Returns all findings across all statuses."""
    account_id = _account_id(user)
    if account_id is None:
        return _offline_findings()
    return db.get_all_findings(account_id)

@app.post("/api/reconcile")
def trigger_reconciliation(period: str = "2026-06", user: dict = Depends(verify_token)):
    """Triggers the reconciliation engine to compute findings and save to DB."""
    try:
        # In a full system, this would trigger the multi-agent ADK run.
        # For the fast API demo, we run the deterministic engine.
        account_id = _account_id(user)
        findings = compute_findings(period, account_id=account_id)
        if account_id is not None:
            db.save_findings(account_id, findings)
        return {"status": "success", "findings_found": len(findings)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/findings/{finding_id}/approve")
def approve_finding(finding_id: str, user: dict = Depends(verify_token)):
    """Marks a finding as approved in the DB and audit log."""
    account_id = _account_id(user)
    if account_id is not None:
        db.update_finding_status(account_id, finding_id, "approved", f"ui_approval_by_{user.get('email', 'unknown')}")
    return {"status": "approved", "finding_id": finding_id}

@app.post("/api/findings/{finding_id}/reject")
def reject_finding(finding_id: str, update: StatusUpdate, user: dict = Depends(verify_token)):
    """Marks a finding as rejected in the DB and audit log, with an optional reason."""
    account_id = _account_id(user)
    if account_id is not None:
        db.update_finding_status(account_id, finding_id, "rejected", f"ui_rejection_by_{user.get('email', 'unknown')}_{update.reason}")
    return {"status": "rejected", "finding_id": finding_id}

@app.post("/api/ingest/usage")
def ingest_usage(payload: UsagePayload, user: dict = Depends(verify_token)):
    """Ingests usage metrics into the DB."""
    account_id = _account_id(user)
    if account_id is not None:
        db.save_usage(account_id, payload.model_dump())
    return {"status": "success", "message": "Usage ingested successfully"}

@app.post("/api/ingest/invoice")
def ingest_invoice(payload: InvoicePayload, user: dict = Depends(verify_token)):
    """Ingests invoice records into the DB."""
    account_id = _account_id(user)
    if account_id is not None:
        db.save_invoice(account_id, payload.model_dump())
    return {"status": "success", "message": "Invoice ingested successfully"}

@app.post("/api/ingest/contract")
def ingest_contract(payload: ContractPayload, user: dict = Depends(verify_token)):
    """Ingests contract records into the DB. 
    In a full implementation, this might accept a PDF and run Gemini extraction."""
    account_id = _account_id(user)
    if account_id is not None:
        db.save_contract(account_id, payload.model_dump())
    return {"status": "success", "message": "Contract ingested successfully"}

# Run with: uvicorn recoup_agent.api:app --reload
