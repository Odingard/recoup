from __future__ import annotations

import csv
import io
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List

from fastapi import Depends, FastAPI, File, HTTPException, Header, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from pypdf import PdfReader

from . import db
from .billing import recoup_billing
from .ingestion_doc import ContractEntitlements, extract_entitlements
from .normalizer import normalize_contract_entitlements
from .pipeline import compute_findings_and_review
from .success_fee import compute_metrics

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def request_validation_handler(_, exc: RequestValidationError):
    fields: list[dict[str, str]] = []
    for error in exc.errors():
        loc = [part for part in error.get("loc", []) if part not in {"body", "query", "path", "header"}]
        field = ".".join(str(part) for part in loc) if loc else "request"
        fields.append({"field": field, "message": error.get("msg", "Invalid input")})
    return JSONResponse(
        status_code=200,
        content={
            "status": "needs_review",
            "message": "Invalid input; please correct the highlighted field(s).",
            "error": "Validation failed",
            "fields": fields,
        },
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


VALID_UPLOAD_SUFFIXES = {".pdf", ".docx", ".txt"}
DEFAULT_PERIOD = "2026-06"


def _account_id(user: dict) -> str | None:
    return user.get("account_id")


def _is_valid_period(period: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}", period))


def _needs_review_payload(message: str, *, fields: list[dict[str, str]] | None = None, extra: dict[str, Any] | None = None) -> dict:
    payload = {
        "status": "needs_review",
        "message": message,
    }
    if fields is not None:
        payload["fields"] = fields
    if extra:
        payload.update(extra)
    return payload


def _offline_findings():
    return compute_findings_and_review(account_id=None)[0]


def _save_contract_if_needed(account_id: str | None, normalized: dict) -> None:
    if account_id is not None:
        db.save_contract(account_id, normalized)


def _contract_preview(normalized: dict, *, saved: bool, needs_review: list[dict] | None = None, message: str = "Contract extracted successfully") -> dict:
    payload = {
        "status": "success",
        "message": message,
        "saved": saved,
        "contract": normalized,
    }
    if needs_review:
        payload["needs_review"] = needs_review
        payload["needs_review_count"] = len(needs_review)
    return payload


def _pdf_has_text_layer(file_path: str) -> tuple[bool, str | None]:
    try:
        reader = PdfReader(file_path)
    except Exception:
        return False, "Could not read uploaded PDF; the file may be corrupt or unreadable."

    if not getattr(reader, "pages", None):
        return False, "Could not read uploaded PDF; the file may be corrupt or unreadable."

    text = []
    try:
        for page in reader.pages:
            try:
                text.append(page.extract_text() or "")
            except Exception:
                continue
    except Exception:
        return False, "Could not read uploaded PDF; the file may be corrupt or unreadable."

    if not "".join(text).strip():
        return False, "This looks like a scanned/image PDF. A text-based PDF is required (OCR is on the roadmap)."
    return True, None


def _extract_and_normalize_contract(file_path: str) -> tuple[dict | None, list[dict], str | None]:
    extracted = extract_entitlements(file_path)
    if not isinstance(extracted, ContractEntitlements):
        return None, [], "Could not extract terms; please confirm manually."
    if not extracted.entitlements:
        return None, [], "Could not extract terms; please confirm manually."
    normalized = normalize_contract_entitlements(extracted)
    if not normalized.get("customer_name") or normalized.get("customer_name") == "Unknown":
        return None, [], "Could not extract terms; please confirm manually."
    return normalized, [], None


@app.get("/api/findings/pending")
def get_pending_findings(user: dict = Depends(verify_token)) -> List[Dict]:
    account_id = _account_id(user)
    if account_id is None:
        return _offline_findings()
    return db.get_pending_findings(account_id)


@app.get("/api/findings")
def get_all_findings(user: dict = Depends(verify_token)) -> List[Dict]:
    account_id = _account_id(user)
    if account_id is None:
        return _offline_findings()
    return db.get_all_findings(account_id)


@app.post("/api/reconcile")
def trigger_reconciliation(period: str = DEFAULT_PERIOD, user: dict = Depends(verify_token)):
    account_id = _account_id(user)
    if not _is_valid_period(period):
        return _needs_review_payload(
            "Invalid billing period; use YYYY-MM.",
            extra={
                "findings_found": 0,
                "needs_review_count": 1,
                "needs_review": [
                    {
                        "customer_id": None,
                        "customer_name": None,
                        "term": "period",
                        "reason": "Period must use YYYY-MM.",
                    }
                ],
            },
        )

    try:
        findings, needs_review = compute_findings_and_review(period, account_id=account_id)
        if account_id is not None:
            db.save_findings(account_id, findings)
        response = {
            "status": "success",
            "findings_found": len(findings),
            "needs_review_count": len(needs_review),
        }
        if needs_review:
            response["needs_review"] = needs_review
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/findings/{finding_id}/approve")
def approve_finding(finding_id: str, user: dict = Depends(verify_token)):
    account_id = _account_id(user)
    if account_id is not None:
        db.update_finding_status(account_id, finding_id, "approved", f"ui_approval_by_{user.get('email', 'unknown')}")
    return {"status": "approved", "finding_id": finding_id}


@app.post("/api/findings/{finding_id}/reject")
def reject_finding(finding_id: str, update: StatusUpdate, user: dict = Depends(verify_token)):
    account_id = _account_id(user)
    if account_id is not None:
        db.update_finding_status(account_id, finding_id, "rejected", f"ui_rejection_by_{user.get('email', 'unknown')}_{update.reason}")
    return {"status": "rejected", "finding_id": finding_id}


@app.post("/api/findings/{finding_id}/recovered")
def mark_finding_recovered(finding_id: str, user: dict = Depends(verify_token)):
    """Mark an approved finding as RECOVERED. Recoup's 20% fee applies only to
    dollars that reach this state."""
    account_id = _account_id(user)
    if account_id is not None:
        db.update_finding_status(account_id, finding_id, "recovered", f"ui_recovered_by_{user.get('email', 'unknown')}")
    return {"status": "recovered", "finding_id": finding_id}


def _findings_for(account_id: str | None) -> list[dict]:
    if account_id is None:
        return _offline_findings()
    return db.get_all_findings(account_id)


@app.get("/api/metrics")
def get_metrics(user: dict = Depends(verify_token)):
    """Recovered-to-date and Recoup's success fee this month."""
    account_id = _account_id(user)
    return compute_metrics(_findings_for(account_id))


@app.post("/api/billing/charge-success-fee")
def charge_success_fee(user: dict = Depends(verify_token)):
    """Bill Recoup's 20% success fee on THIS MONTH's recovered dollars through
    Recoup's own (separate) Stripe account."""
    account_id = _account_id(user)
    metrics = compute_metrics(_findings_for(account_id))
    result = recoup_billing.create_success_fee_invoice(
        customer_email=user.get("email"),
        amount_dollars=metrics["success_fee_this_month"],
        current_month=metrics["current_month"],
    )
    return {"metrics": metrics, "billing": result}


@app.get("/api/findings/export")
def export_findings(user: dict = Depends(verify_token)):
    """Export findings as CSV for the operator's records."""
    account_id = _account_id(user)
    findings = _findings_for(account_id)
    columns = [
        "finding_id", "customer_id", "customer_name", "period", "title",
        "monthly_recoverable", "status", "recovered_at", "confidence_score", "clause_ref",
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for finding in findings:
        writer.writerow({col: finding.get(col, "") for col in columns})
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=recoup_findings.csv"},
    )


@app.post("/api/ingest/usage")
def ingest_usage(payload: UsagePayload, user: dict = Depends(verify_token)):
    account_id = _account_id(user)
    if account_id is not None:
        db.save_usage(account_id, payload.model_dump())
    return {"status": "success", "message": "Usage ingested successfully"}


@app.post("/api/ingest/invoice")
def ingest_invoice(payload: InvoicePayload, user: dict = Depends(verify_token)):
    account_id = _account_id(user)
    if account_id is not None:
        db.save_invoice(account_id, payload.model_dump())
    return {"status": "success", "message": "Invoice ingested successfully"}


@app.post("/api/ingest/contract")
def ingest_contract(payload: ContractPayload, user: dict = Depends(verify_token)):
    account_id = _account_id(user)
    if account_id is not None:
        db.save_contract(account_id, payload.model_dump())
    return {"status": "success", "message": "Contract ingested successfully"}


@app.post("/api/ingest/contract/document")
async def ingest_contract_document(file: UploadFile = File(...), user: dict = Depends(verify_token)):
    account_id = _account_id(user)
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in VALID_UPLOAD_SUFFIXES:
        return _needs_review_payload("Unsupported file type; upload a PDF, DOCX, or TXT.")

    try:
        content = await file.read()
    except Exception:
        return _needs_review_payload("Could not read uploaded file; please upload a valid PDF, DOCX, or TXT.")

    if not content:
        return _needs_review_payload("The uploaded file is empty; please upload a valid document.")

    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            temp_path = tmp.name

        if suffix == ".pdf":
            has_text, error_message = _pdf_has_text_layer(temp_path)
            if not has_text:
                return _needs_review_payload(error_message or "Could not extract text from uploaded PDF.")

        try:
            normalized, needs_review, error_message = _extract_and_normalize_contract(temp_path)
        except Exception:
            return _needs_review_payload("Could not extract terms; please confirm manually.")

        if error_message or normalized is None:
            return _needs_review_payload(error_message or "Could not extract terms; please confirm manually.")

        saved = account_id is not None
        if saved:
            _save_contract_if_needed(account_id, normalized)

        return _contract_preview(normalized, saved=saved, needs_review=needs_review)
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass


# Run with: uvicorn recoup_agent.api:app --reload
