from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import logging
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, File, HTTPException, Header, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from pypdf import PdfReader

from . import db
from .billing.connector_keys import (
    delete_connector_key,
    get_connector_status,
    resolve_connector_key,
    store_connector_key,
)
from .billing import recoup_billing
from .billing.stripe_oauth import (
    build_oauth_install_url,
    build_oauth_state,
    exchange_authorization_code,
    oauth_redirect_uri,
    oauth_web_base_url,
    parse_oauth_state,
)
from .ingest_bulk import ingest_files
from .ingestion_doc import ContractEntitlements, extract_entitlements
from .normalizer import normalize_contract_entitlements
from .pipeline import _load_book, compute_findings_and_review, run_book
from .recovery import assert_transition
from .renewals import build_renewal_calendar
from .report import build_report, render_html, render_pdf
from .security import assert_key_separation
from .success_fee import compute_metrics
from .trueup import build_trueup, render_trueup_pdf

logger = logging.getLogger(__name__)
from .templates import TEMPLATES

_firebase_lock = threading.Lock()
_firebase_ready = False

SAMPLE_HEADER = "X-Recoup-Sample"


def _sample_mode_enabled() -> bool:
    return os.getenv("RECOUP_SAMPLE_MODE", "").lower() in {"1", "true", "yes", "on"}


def _sample_identity(source: str) -> dict:
    return {"uid": "sample", "email": "sample@recoup.local", "account_id": None,
            "sample_source": source}


def _is_header_sample(user: dict) -> bool:
    return user.get("sample_source") == "header"


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


def verify_token(authorization: str | None = Header(default=None),
                 x_recoup_sample: str | None = Header(default=None)):
    if _sample_mode_enabled():
        return _sample_identity("env")
    if (x_recoup_sample or "").lower() in {"1", "true", "yes", "on"}:
        return _sample_identity("header")
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
assert_key_separation()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_RECOVERY_PATHS = ("/invoiced", "/recovered", "/disputed", "/written-off")


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request, exc: RequestValidationError):
    if request.url.path.startswith("/api/findings/") and request.url.path.endswith(_RECOVERY_PATHS):
        return JSONResponse(
            status_code=422,
            content={"detail": "Recovery evidence is required.", "errors": exc.errors()},
        )
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


class InvoiceEvidence(BaseModel):
    invoice_ref: str
    invoice_amount: float
    invoice_date: str | None = None
    invoice_url: str | None = None
    note: str = ""


class PaymentEvidence(BaseModel):
    paid_amount: float
    paid_date: str | None = None
    payment_ref: str | None = None
    note: str = ""


class DisputeNote(BaseModel):
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


VALID_UPLOAD_SUFFIXES = {".pdf", ".docx", ".txt", ".md", ".png", ".jpg", ".jpeg"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
MAX_SCANNED_PDF_PAGES = 25
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


def _contract_preview(normalized: dict, *, saved: bool, needs_review: list[dict] | None = None,
                      message: str = "Contract extracted successfully", ocr: bool = False) -> dict:
    payload = {
        "status": "success",
        "message": message,
        "saved": saved,
        "ocr": ocr,
        "contract": normalized,
    }
    if needs_review:
        payload["needs_review"] = needs_review
        payload["needs_review_count"] = len(needs_review)
    return payload


def _looks_like_restricted_key(key: str) -> bool:
    return key.startswith("rk_")


def _looks_like_write_key(key: str) -> bool:
    return key.startswith("sk_live_") or key.startswith("sk_test_")


def _stripe_oauth_success_url(payload: dict[str, Any], store_result: dict[str, Any]) -> str:
    params = {
        "stripe_connect": "success",
        "account_id": payload.get("account_id", ""),
        "stripe_account_id": store_result.get("stripe_account_id", "") or "",
    }
    return f"{oauth_web_base_url().rstrip('/')}/app/?{urlencode(params)}"


def _stripe_oauth_error_url(message: str, *, account_id: str | None = None) -> str:
    params = {
        "stripe_connect": "error",
        "message": message,
    }
    if account_id:
        params["account_id"] = account_id
    return f"{oauth_web_base_url().rstrip('/')}/app/?{urlencode(params)}"


def _pdf_has_text_layer(file_path: str) -> tuple[bool, str | None, int]:
    """-> (has_text, error, page_count). error is set only for corrupt/unreadable
    files; a scanned PDF (no text layer) returns (False, None, pages) since
    Gemini reads the raw bytes natively."""
    try:
        reader = PdfReader(file_path)
    except Exception:
        return False, "Could not read uploaded PDF; the file may be corrupt or unreadable.", 0

    if not getattr(reader, "pages", None):
        return False, "Could not read uploaded PDF; the file may be corrupt or unreadable.", 0

    pages = len(reader.pages)
    text = []
    try:
        for page in reader.pages:
            try:
                text.append(page.extract_text() or "")
            except Exception:
                continue
    except Exception:
        return False, "Could not read uploaded PDF; the file may be corrupt or unreadable.", pages

    if not "".join(text).strip():
        return False, None, pages  # scanned/image PDF -> handled via OCR path
    return True, None, pages


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


_LOCKED_MESSAGE = "Add a payment method to unlock the contract clause and calculation."
_LOCK_DETAIL = ("Add a payment method to unlock reports and true-up packs. "
                "You are only charged 20% of dollars actually recovered.")


def _proof_unlocked(user: dict) -> bool:
    """Clause text, math, and reports are gated behind a card on file."""
    account_id = _account_id(user)
    if account_id is None:
        return True  # sample/offline mode keeps full proof
    if not recoup_billing.is_configured():
        return True  # dev/test envs without a billing key stay unlocked
    billing = db.get_account_billing(account_id) or {}
    return bool(billing.get("payment_method_id"))


def _redact_if_locked(user: dict, findings: list[dict]) -> list[dict]:
    if not findings or _proof_unlocked(user):
        return findings
    redacted = []
    for f in findings:
        f = dict(f)
        for key in ("provenance", "clause_text", "math", "detail"):
            f[key] = _LOCKED_MESSAGE
        f["locked"] = True
        redacted.append(f)
    return redacted


def _require_unlocked(user: dict) -> None:
    if not _proof_unlocked(user):
        raise HTTPException(status_code=402, detail=_LOCK_DETAIL)


@app.get("/api/findings/pending")
def get_pending_findings(user: dict = Depends(verify_token)) -> List[Dict]:
    account_id = _account_id(user)
    if account_id is None:
        return _offline_findings()
    return _redact_if_locked(user, db.get_pending_findings(account_id))


@app.get("/api/findings")
def get_all_findings(user: dict = Depends(verify_token)) -> List[Dict]:
    account_id = _account_id(user)
    if account_id is None:
        return _offline_findings()
    return _redact_if_locked(user, db.get_all_findings(account_id))


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


def _transition_fields(account_id: str | None, finding_id: str, new_status: str) -> None:
    """Enforce the finding lifecycle when a persistent account is attached."""
    finding = db.get_finding(account_id, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail=f"Finding {finding_id} not found.")
    try:
        assert_transition(finding.get("status", "open"), new_status)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/findings/{finding_id}/invoiced")
def record_finding_invoiced(finding_id: str, evidence: InvoiceEvidence, user: dict = Depends(verify_token)):
    """Record the corrective invoice sent to the customer for an approved finding."""
    if not evidence.invoice_ref.strip():
        raise HTTPException(status_code=422, detail="invoice_ref is required.")
    account_id = _account_id(user)
    fields = {
        "corrective_invoice": {
            "ref": evidence.invoice_ref,
            "amount": evidence.invoice_amount,
            "date": evidence.invoice_date,
            "url": evidence.invoice_url,
            "note": evidence.note,
            "recorded_by": user.get("email", "unknown"),
        }
    }
    if account_id is not None:
        _transition_fields(account_id, finding_id, "invoiced")
        db.update_finding_status(account_id, finding_id, "invoiced",
                                 f"ui_invoiced_by_{user.get('email', 'unknown')}", fields=fields)
    return {"status": "invoiced", "finding_id": finding_id, **fields}


@app.post("/api/findings/{finding_id}/recovered")
def mark_finding_recovered(finding_id: str, evidence: PaymentEvidence, user: dict = Depends(verify_token)):
    """Record payment against an approved/invoiced finding. Recoup's 20% fee
    applies only to dollars that reach this state."""
    if evidence.paid_amount <= 0:
        raise HTTPException(status_code=422, detail="paid_amount must be greater than zero.")
    account_id = _account_id(user)
    fields = {
        "recovered_amount": evidence.paid_amount,
        "payment": {
            "ref": evidence.payment_ref,
            "date": evidence.paid_date,
            "note": evidence.note,
            "recorded_by": user.get("email", "unknown"),
        },
    }
    fee_charge = None
    if account_id is not None:
        _transition_fields(account_id, finding_id, "recovered")
        db.update_finding_status(account_id, finding_id, "recovered",
                                 f"ui_recovered_by_{user.get('email', 'unknown')}", fields=fields)
        # Collect Recoup's 20% against the just-recorded paid amount; billing
        # problems never block the recovery transition.
        finding = db.get_finding(account_id, finding_id) or {"finding_id": finding_id}
        fee_charge = recoup_billing.charge_success_fee_for_finding(
            account_id, finding, evidence.paid_amount)
        if fee_charge:
            db.update_finding_fields(account_id, finding_id,
                                     {"fee_charge": fee_charge}, "success_fee_charge")
    return {"status": "recovered", "finding_id": finding_id, "fee_charge": fee_charge, **fields}


@app.post("/api/findings/{finding_id}/disputed")
def mark_finding_disputed(finding_id: str, note: DisputeNote, user: dict = Depends(verify_token)):
    """Flag an invoiced finding as disputed by the customer."""
    account_id = _account_id(user)
    fields = {"dispute": {"reason": note.reason, "recorded_by": user.get("email", "unknown")}}
    if account_id is not None:
        _transition_fields(account_id, finding_id, "disputed")
        db.update_finding_status(account_id, finding_id, "disputed",
                                 f"ui_disputed_by_{user.get('email', 'unknown')}", fields=fields)
    return {"status": "disputed", "finding_id": finding_id, **fields}


@app.post("/api/findings/{finding_id}/written-off")
def mark_finding_written_off(finding_id: str, note: DisputeNote, user: dict = Depends(verify_token)):
    """Write off an approved/invoiced/disputed finding as uncollectible."""
    account_id = _account_id(user)
    fields = {"write_off": {"reason": note.reason, "recorded_by": user.get("email", "unknown")}}
    if account_id is not None:
        _transition_fields(account_id, finding_id, "written_off")
        db.update_finding_status(account_id, finding_id, "written_off",
                                 f"ui_written_off_by_{user.get('email', 'unknown')}", fields=fields)
    return {"status": "written_off", "finding_id": finding_id, **fields}


def _findings_for(account_id: str | None) -> list[dict]:
    if account_id is None:
        return _offline_findings()
    return db.get_all_findings(account_id)


@app.get("/api/metrics")
def get_metrics(user: dict = Depends(verify_token)):
    """Recovered-to-date and Recoup's success fee this month."""
    account_id = _account_id(user)
    return compute_metrics(_findings_for(account_id))


@app.get("/api/billing/status")
def get_billing_status(user: dict = Depends(verify_token)):
    account_id = _account_id(user)
    if account_id is None:
        return {"configured": recoup_billing.is_configured(),
                "card_on_file": True, "sample": True, "success_fee_pct": 0.20}
    return recoup_billing.billing_status(account_id)


@app.post("/api/billing/setup-session")
def start_billing_setup(request: Request, user: dict = Depends(verify_token)):
    """Hosted Stripe Checkout (setup mode) to place a card on file."""
    account_id = _account_id(user)
    if account_id is None:
        return _needs_review_payload("Sample mode has no billing account.")
    base = os.getenv("RECOUP_WEB_BASE_URL") or str(request.base_url).rstrip("/")
    return recoup_billing.create_setup_checkout_url(account_id, user.get("email"), base)


class SetupComplete(BaseModel):
    session_id: str = ""


@app.post("/api/billing/setup-complete")
def finish_billing_setup(payload: SetupComplete, user: dict = Depends(verify_token)):
    account_id = _account_id(user)
    if account_id is None:
        return _needs_review_payload("Sample mode has no billing account.")
    return recoup_billing.complete_setup_session(account_id, payload.session_id)


@app.post("/api/billing/sync-recoveries")
def sync_recoveries(user: dict = Depends(verify_token)):
    """Verify paid corrective invoices against the customer's Stripe via the
    read-only connector, transition them to recovered, and collect the fee."""
    account_id = _account_id(user)
    if account_id is None:
        return _needs_review_payload("Sample mode has no Stripe connector.")
    tenant_key = resolve_connector_key(account_id)
    if not tenant_key:
        return {"status": "needs_connector", "checked": 0}

    import stripe
    checked = 0
    recovered: list[str] = []
    fee_charges: list[dict] = []
    errors: list[dict] = []
    for f in db.get_all_findings(account_id):
        ref = (f.get("corrective_invoice") or {}).get("ref") or ""
        if f.get("status") != "invoiced" or not ref.startswith("in_"):
            continue
        checked += 1
        try:
            inv = stripe.Invoice.retrieve(ref, api_key=tenant_key)
            if inv.get("status") != "paid":
                continue
            amount_paid = inv.get("amount_paid") or 0
            paid_at = (inv.get("status_transitions") or {}).get("paid_at")
            payment_ref = inv.get("payment_intent") or inv.get("charge")
            fields = {
                "recovered_amount": amount_paid / 100.0,
                "payment": {
                    "ref": payment_ref,
                    "date": (datetime.fromtimestamp(paid_at, tz=timezone.utc).isoformat()
                             if paid_at else None),
                    "verified_via": "stripe_connect",
                    "recorded_by": "stripe_connect",
                },
            }
            try:
                _transition_fields(account_id, f["finding_id"], "recovered")
            except HTTPException:
                continue
            db.update_finding_status(account_id, f["finding_id"], "recovered",
                                     "stripe_connect_sync", fields=fields)
            fee = recoup_billing.charge_success_fee_for_finding(
                account_id, f, fields["recovered_amount"])
            if fee:
                db.update_finding_fields(account_id, f["finding_id"],
                                         {"fee_charge": fee}, "success_fee_charge")
                fee_charges.append(fee)
            recovered.append(f["finding_id"])
        except Exception as exc:
            errors.append({"finding_id": f.get("finding_id"), "error": str(exc)})
    return {"status": "success", "checked": checked, "recovered": recovered,
            "fee_charges": fee_charges, "errors": errors}


@app.post("/api/billing/charge-success-fee")
def charge_success_fee(user: dict = Depends(verify_token)):
    """Collect Recoup's 20% success fee on recovered dollars. Charges the card
    on file per finding; falls back to a mailed invoice when no card exists."""
    account_id = _account_id(user)
    if _is_header_sample(user):
        return _needs_review_payload("Sample mode does not bill a success fee.")
    metrics = compute_metrics(_findings_for(account_id))
    findings = _findings_for(account_id)
    unbilled = [f for f in findings
                if f.get("status") == "recovered"
                and (f.get("fee_charge") or {}).get("status") not in {"paid", "pending"}]

    billing = (db.get_account_billing(account_id) or {}) if account_id else {}
    if billing.get("payment_method_id"):
        charged = []
        for f in unbilled:
            amount = f.get("recovered_amount") or f.get("monthly_recoverable") or 0
            result = recoup_billing.charge_success_fee_for_finding(account_id, f, amount)
            if result:
                db.update_finding_fields(account_id, f["finding_id"],
                                         {"fee_charge": result}, "success_fee_charge")
            charged.append({"finding_id": f.get("finding_id"), **result})
        return {"metrics": metrics,
                "billing": {"status": "success" if charged else "skipped",
                            "charged": charged}}

    result = recoup_billing.create_success_fee_invoice(
        customer_email=user.get("email"),
        amount_dollars=metrics["success_fee_this_month"],
        current_month=metrics["current_month"],
    )
    if result.get("status") == "success" and account_id is not None:
        for f in unbilled:
            db.update_finding_fields(
                account_id, f["finding_id"],
                {"fee_charge": {"status": "invoiced", "invoice_id": result["invoice_id"],
                                "hosted_invoice_url": result.get("hosted_invoice_url"),
                                "amount": result.get("amount")}},
                "success_fee_charge")
    return {"metrics": metrics, "billing": {**result, "fallback": True}}


@app.get("/api/findings/export")
def export_findings(user: dict = Depends(verify_token)):
    """Export findings as CSV for the operator's records."""
    _require_unlocked(user)
    account_id = _account_id(user)
    findings = _findings_for(account_id)
    columns = [
        "finding_id", "customer_id", "customer_name", "period", "title",
        "monthly_recoverable", "status", "recovered_at", "recovered_amount",
        "corrective_invoice_ref", "payment_ref", "confidence_score", "clause_ref",
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for finding in findings:
        row = {col: finding.get(col, "") for col in columns}
        row["corrective_invoice_ref"] = (finding.get("corrective_invoice") or {}).get("ref", "")
        row["payment_ref"] = (finding.get("payment") or {}).get("ref", "")
        writer.writerow(row)
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=recoup_findings.csv"},
    )


def _report_for_account(account_id: str | None) -> dict:
    """Audit report over stored findings (account) or the local book (sample mode)."""
    if account_id is None:
        contracts, usage, invoices = _load_book(None)
        findings_by_period, needs_review = run_book(contracts, usage, invoices)
        return build_report(findings_by_period, needs_review, contracts)
    findings = db.get_all_findings(account_id)
    by_period: dict[str, list[dict]] = {}
    for f in findings:
        f.setdefault("math", f.get("detail", ""))
        f.setdefault("clause_text", f.get("detail", ""))
        by_period.setdefault(f.get("period", ""), []).append(f)
    return build_report(by_period, [], db.get_all_contracts(account_id))


def _share_token(account_id: str) -> str:
    secret = os.getenv("RECOUP_REPORT_SHARE_SECRET")
    if not secret:
        raise HTTPException(status_code=503,
                            detail="RECOUP_REPORT_SHARE_SECRET is not configured; report sharing is disabled.")
    return hmac.new(secret.encode(), account_id.encode(), hashlib.sha256).hexdigest()[:32]


@app.get("/api/report")
def get_report(user: dict = Depends(verify_token)):
    _require_unlocked(user)
    return _report_for_account(_account_id(user))


@app.get("/api/report.pdf")
def get_report_pdf(user: dict = Depends(verify_token)):
    _require_unlocked(user)
    pdf = render_pdf(_report_for_account(_account_id(user)))
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": "attachment; filename=recoup_report.pdf"})


@app.post("/api/report/share")
def share_report(request: Request, user: dict = Depends(verify_token)):
    _require_unlocked(user)
    account_id = _account_id(user)
    base = os.getenv("RECOUP_WEB_BASE_URL") or str(request.base_url).rstrip("/")
    if account_id is None:
        return {"url": f"{base.rstrip('/')}/report/sample"}
    token = _share_token(account_id)
    return {"url": f"{base.rstrip('/')}/report/{account_id}/{token}"}


@app.get("/report/sample")
def sample_report_html():
    return HTMLResponse(render_html(_report_for_account(None)))


@app.get("/report/sample.pdf")
def sample_report_pdf():
    return Response(content=render_pdf(_report_for_account(None)), media_type="application/pdf")


@app.get("/report/{account_id}/{token}")
def shared_report(account_id: str, token: str):
    wants_pdf = token.endswith(".pdf")
    raw_token = token[:-4] if wants_pdf else token
    secret = os.getenv("RECOUP_REPORT_SHARE_SECRET")
    if not secret:
        raise HTTPException(status_code=503, detail="Report sharing is not configured.")
    expected = hmac.new(secret.encode(), account_id.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expected, raw_token):
        raise HTTPException(status_code=403, detail="Invalid share token.")
    report = _report_for_account(account_id)
    if wants_pdf:
        return Response(content=render_pdf(report), media_type="application/pdf")
    return HTMLResponse(render_html(report))


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


@app.get("/api/connector/stripe/status")
def connector_status(user: dict = Depends(verify_token)):
    account_id = _account_id(user)
    return {"status": "success", **get_connector_status(account_id)}


@app.post("/api/connector/stripe/oauth/start")
def start_stripe_oauth(user: dict = Depends(verify_token)):
    account_id = _account_id(user)
    if account_id is None:
        return _needs_review_payload("Sample mode does not connect to Stripe.")

    state = build_oauth_state(account_id, user.get("uid") or account_id, email=user.get("email"))
    try:
        install_url = build_oauth_install_url(state=state)
    except Exception as exc:
        return _needs_review_payload(f"Could not build Stripe install link. Details: {exc}")
    return {"status": "success", "install_url": install_url, "redirect_uri": oauth_redirect_uri()}


@app.get("/api/connector/stripe/oauth/callback")
def stripe_oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        return RedirectResponse(url=_stripe_oauth_error_url(error), status_code=302)
    if not code or not state:
        return RedirectResponse(url=_stripe_oauth_error_url("Missing OAuth code or state."), status_code=302)

    try:
        state_payload = parse_oauth_state(state)
    except Exception as exc:
        return RedirectResponse(url=_stripe_oauth_error_url(f"Invalid OAuth state. Details: {exc}"), status_code=302)

    account_id = state_payload.get("account_id")
    if not account_id:
        return RedirectResponse(url=_stripe_oauth_error_url("OAuth state did not include an account id."), status_code=302)

    try:
        credential = exchange_authorization_code(code, account_id=account_id)
    except Exception as exc:
        return RedirectResponse(url=_stripe_oauth_error_url(f"Stripe OAuth exchange failed. Details: {exc}", account_id=account_id), status_code=302)

    result = store_connector_key(account_id, credential)
    if result.get("status") != "success":
        return RedirectResponse(
            url=_stripe_oauth_error_url(result.get("message", "Could not store Stripe OAuth credential."), account_id=account_id),
            status_code=302,
        )
    return RedirectResponse(
        url=_stripe_oauth_success_url(state_payload, result),
        status_code=302,
    )


def _ingest_contract_bytes(account_id: str | None, filename: str, content: bytes) -> dict:
    """Extract + normalize one uploaded contract document; returns a preview or
    needs_review payload. Scanned PDFs and images go through Gemini OCR."""
    suffix = Path(filename).suffix.lower()
    if suffix not in VALID_UPLOAD_SUFFIXES:
        return _needs_review_payload(
            "Unsupported file type; upload a PDF, DOCX, TXT, MD, or image.")
    if not content:
        return _needs_review_payload("The uploaded file is empty; please upload a valid document.")

    ocr = suffix in IMAGE_SUFFIXES
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            temp_path = tmp.name

        if suffix == ".pdf":
            has_text, error_message, pages = _pdf_has_text_layer(temp_path)
            if error_message:
                return _needs_review_payload(error_message)
            if not has_text:
                if pages > MAX_SCANNED_PDF_PAGES:
                    return _needs_review_payload(
                        f"Scanned PDF exceeds {MAX_SCANNED_PDF_PAGES} pages ({pages}); "
                        "split it into smaller documents or enter terms manually.")
                ocr = True

        try:
            normalized, needs_review, error_message = _extract_and_normalize_contract(temp_path)
        except Exception:
            return _needs_review_payload("Could not extract terms; please confirm manually.")

        if error_message or normalized is None:
            return _needs_review_payload(error_message or "Could not extract terms; please confirm manually.")

        saved = account_id is not None
        if saved:
            _save_contract_if_needed(account_id, normalized)

        message = ("Contract extracted from scanned PDF (OCR); verify amounts against the original"
                   if ocr else "Contract extracted successfully")
        return _contract_preview(normalized, saved=saved, needs_review=needs_review,
                                 message=message, ocr=ocr)
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass


def _trueup_pack(user: dict, customer_id: str, sender: str | None, include_open: bool):
    _require_unlocked(user)
    account_id = _account_id(user)
    if account_id is None:
        contracts, _usage, _invoices = _load_book(None)
        findings = _offline_findings()
        include_open = True  # sample findings are all 'open'
    else:
        contracts = db.get_all_contracts(account_id)
        findings = db.get_all_findings(account_id)
    pack = build_trueup(customer_id, findings, contracts,
                        sender=sender, include_open=include_open)
    if pack is None:
        raise HTTPException(status_code=404,
                            detail=f"No collectible findings for customer '{customer_id}'.")
    return pack


@app.get("/api/trueup/{customer_id}.pdf")
def get_trueup_pdf(customer_id: str, sender: str | None = None, include_open: bool = False,
                   user: dict = Depends(verify_token)):
    pack = _trueup_pack(user, customer_id, sender, include_open)
    return Response(
        content=render_trueup_pdf(pack), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="trueup_{customer_id}.pdf"'})


@app.get("/api/trueup/{customer_id}")
def get_trueup(customer_id: str, sender: str | None = None, include_open: bool = False,
               user: dict = Depends(verify_token)):
    """JSON true-up pack for one customer (letter + schedule rows)."""
    return _trueup_pack(user, customer_id, sender, include_open)


class DeleteConfirm(BaseModel):
    confirm: str = ""


@app.delete("/api/account/data")
def delete_account_data(payload: DeleteConfirm, user: dict = Depends(verify_token)):
    """Self-serve deletion of everything stored for this account."""
    account_id = _account_id(user)
    if account_id is None:
        return _needs_review_payload("Sample mode has no account data to delete.")
    if payload.confirm != "DELETE":
        raise HTTPException(status_code=400, detail="Type DELETE to confirm")
    deleted = db.delete_account_data(account_id)
    connector_deleted = delete_connector_key(account_id)
    logger.info("Deleted all data for account %s", account_id)
    return {"status": "deleted", "account_id": account_id, "deleted": deleted,
            "connector_key_deleted": connector_deleted}


@app.post("/api/ingest/contract/document")
async def ingest_contract_document(file: UploadFile = File(...), user: dict = Depends(verify_token)):
    account_id = _account_id(user)
    if _is_header_sample(user):
        return _needs_review_payload(
            "Sample mode does not extract uploaded contracts; sign in to use real data.")
    try:
        content = await file.read()
    except Exception:
        return _needs_review_payload("Could not read uploaded file; please upload a valid document.")
    return _ingest_contract_bytes(account_id, file.filename or "", content)


@app.post("/api/ingest/bulk")
async def ingest_bulk(files: list[UploadFile] = File(...), user: dict = Depends(verify_token)):
    """Ingest many files at once: contracts (incl. scans/images), billing/usage
    CSVs, or ZIP archives containing them."""
    account_id = _account_id(user)
    if account_id is None:
        return _needs_review_payload("Sample mode does not ingest uploads; sign in to use real data.")

    items: list[tuple[str, bytes]] = []
    for f in files:
        try:
            items.append((f.filename or "upload", await f.read()))
        except Exception:
            return _needs_review_payload("Could not read an uploaded file.")

    result = ingest_files(items, db.get_all_contracts(account_id), extract_entitlements)
    for contract in result.contracts:
        db.save_contract(account_id, contract)
    for invoice in result.invoices:
        db.save_invoice(account_id, invoice)
    for usage_rec in result.usage:
        db.save_usage(account_id, usage_rec)

    periods = sorted({r.get("period") for r in result.invoices + result.usage if r.get("period")})
    return {
        "status": "success",
        "files": result.files,
        "contracts": len(result.contracts),
        "invoices": len(result.invoices),
        "usage": len(result.usage),
        "needs_review": result.needs_review,
        "periods": periods,
    }


@app.get("/api/renewals")
def get_renewals(user: dict = Depends(verify_token)):
    """Renewal calendar: term end + cancellation notice deadline per contract."""
    from datetime import date as _date
    account_id = _account_id(user)
    contracts = _load_book(None)[0] if account_id is None else db.get_all_contracts(account_id)
    return build_renewal_calendar(contracts, _date.today())


@app.get("/api/templates/{system}/{kind}.csv", include_in_schema=False)
def export_template(system: str, kind: str):
    """Downloadable CSV template in a billing system's native column layout."""
    spec = TEMPLATES.get(system.lower(), {}).get(kind.lower())
    if spec is None:
        raise HTTPException(status_code=404, detail="Unknown template; use quickbooks|xero|stripe + invoices|usage.")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(spec["header"])
    writer.writerows(spec["rows"])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="recoup_{system}_{kind}_template.csv"'},
    )


@app.get("/app", include_in_schema=False)
def app_without_trailing_slash() -> RedirectResponse:
    """Ensure /app serves the SPA even when the static mount doesn't redirect."""
    return RedirectResponse("/app/", status_code=307)


# Serve the built web app (if present) at /; mounted LAST so API routes win.
_web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
if _web_dist.is_dir():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=str(_web_dist), html=True), name="web")


# Run with: uvicorn recoup_agent.api:app --reload
