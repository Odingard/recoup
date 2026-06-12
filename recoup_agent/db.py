import os
from datetime import datetime, timezone
from google.cloud import firestore

_client = None

def get_client():
    global _client
    if _client is None:
        _client = firestore.Client(project="gen-lang-client-0647036765")
    return _client

def init_db():
    """Firestore doesn't require schema initialization."""
    pass

def save_findings(findings: list[dict]):
    db = get_client()
    batch = db.batch()
    now = datetime.now(timezone.utc).isoformat()
    
    for f in findings:
        doc_ref = db.collection("findings").document(f.get('finding_id'))
        
        data = {
            "customer_id": f.get('customer_id'),
            "customer_name": f.get('customer_name'),
            "period": f.get('period', '2026-06'),
            "title": f.get('title'),
            "detail": f.get('detail'),
            "monthly_recoverable": f.get('monthly_recoverable'),
            "clause_ref": f.get('clause_ref'),
            "confidence_score": f.get('confidence_score', 1.0),
            "provenance": f.get('provenance', ''),
            "status": "open", # Always open when pushed to DB to make the demo repeatable
            "created_at": now
        }
        
        # Merge ensures we don't clobber any other fields, but we intentionally
        # overwrite status to "open" here so you can run the engine repeatedly.
        batch.set(doc_ref, data, merge=True)
    
    batch.commit()

def get_pending_findings() -> list[dict]:
    db = get_client()
    # We do the filtering here to avoid needing a composite index
    # which would otherwise be required for status == 'open' + order_by.
    docs = db.collection("findings").where(filter=firestore.FieldFilter("status", "==", "open")).stream()
    findings = [{"finding_id": doc.id, **doc.to_dict()} for doc in docs]
    
    # Sort descending by recoverable amount
    findings.sort(key=lambda x: x.get("monthly_recoverable", 0), reverse=True)
    return findings

def get_all_findings() -> list[dict]:
    db = get_client()
    docs = db.collection("findings").stream()
    findings = [{"finding_id": doc.id, **doc.to_dict()} for doc in docs]
    findings.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return findings

def update_finding_status(finding_id: str, status: str, event_name: str):
    db = get_client()
    now = datetime.now(timezone.utc).isoformat()
    
    # Update finding
    doc_ref = db.collection("findings").document(finding_id)
    doc_ref.update({"status": status})
    
    # Insert audit log
    audit_ref = db.collection("audit_log").document()
    audit_ref.set({
        "finding_id": finding_id,
        "event": event_name,
        "decision": status,
        "ts": now
    })

# --- INGESTION APIs ---

def save_usage(payload: dict):
    db = get_client()
    doc_id = f"{payload['customer_id']}_{payload['period']}"
    db.collection("usage").document(doc_id).set(payload, merge=True)

def save_invoice(payload: dict):
    db = get_client()
    doc_id = f"{payload['customer_id']}_{payload['period']}"
    db.collection("invoices").document(doc_id).set(payload, merge=True)

def save_contract(payload: dict):
    db = get_client()
    db.collection("contracts").document(payload["customer_id"]).set(payload, merge=True)

def get_all_usage() -> list[dict]:
    db = get_client()
    return [doc.to_dict() for doc in db.collection("usage").stream()]

def get_all_invoices() -> list[dict]:
    db = get_client()
    return [doc.to_dict() for doc in db.collection("invoices").stream()]

def get_all_contracts() -> list[dict]:
    db = get_client()
    return [doc.to_dict() for doc in db.collection("contracts").stream()]
