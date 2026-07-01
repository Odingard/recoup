import os
from datetime import datetime, timezone
from google.cloud import firestore

_client = None

def get_client():
    global _client
    if _client is None:
        project = os.getenv("GOOGLE_CLOUD_PROJECT")
        _client = firestore.Client(project=project) if project else firestore.Client()
    return _client


def _account_root(db, account_id: str):
    return db.collection("accounts").document(account_id)


def _collection(db, account_id: str, name: str):
    return _account_root(db, account_id).collection(name)

def init_db():
    """Firestore doesn't require schema initialization."""
    pass

def save_findings(account_id: str, findings: list[dict]):
    db = get_client()
    batch = db.batch()
    now = datetime.now(timezone.utc).isoformat()
    
    for f in findings:
        doc_ref = _collection(db, account_id, "findings").document(f.get('finding_id'))
        existing = doc_ref.get()
        status = f.get("status") if f.get("status") is not None else (existing.to_dict().get("status") if existing.exists else "open")
        
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
            "status": status,
            "created_at": f.get('created_at', now)
        }
        batch.set(doc_ref, data, merge=True)
    
    batch.commit()

def get_pending_findings(account_id: str) -> list[dict]:
    db = get_client()
    docs = _collection(db, account_id, "findings").where(filter=firestore.FieldFilter("status", "==", "open")).stream()
    findings = [{"finding_id": doc.id, **doc.to_dict()} for doc in docs]
    
    # Sort descending by recoverable amount
    findings.sort(key=lambda x: x.get("monthly_recoverable", 0), reverse=True)
    return findings

def get_all_findings(account_id: str) -> list[dict]:
    db = get_client()
    docs = _collection(db, account_id, "findings").stream()
    findings = [{"finding_id": doc.id, **doc.to_dict()} for doc in docs]
    findings.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return findings

def update_finding_status(account_id: str, finding_id: str, status: str, event_name: str):
    db = get_client()
    now = datetime.now(timezone.utc).isoformat()
    
    # Update finding
    doc_ref = _collection(db, account_id, "findings").document(finding_id)
    update_fields = {"status": status}
    if status == "recovered":
        update_fields["recovered_at"] = now
    doc_ref.update(update_fields)
    
    # Insert audit log
    audit_ref = _collection(db, account_id, "audit_log").document()
    audit_ref.set({
        "finding_id": finding_id,
        "event": event_name,
        "decision": status,
        "ts": now
    })

# --- INGESTION APIs ---

def save_usage(account_id: str, payload: dict):
    db = get_client()
    doc_id = f"{payload['customer_id']}_{payload['period']}"
    _collection(db, account_id, "usage").document(doc_id).set(payload, merge=True)

def save_invoice(account_id: str, payload: dict):
    db = get_client()
    doc_id = f"{payload['customer_id']}_{payload['period']}"
    _collection(db, account_id, "invoices").document(doc_id).set(payload, merge=True)

def save_contract(account_id: str, payload: dict):
    db = get_client()
    _collection(db, account_id, "contracts").document(payload["customer_id"]).set(payload, merge=True)

def get_all_usage(account_id: str) -> list[dict]:
    db = get_client()
    return [doc.to_dict() for doc in _collection(db, account_id, "usage").stream()]

def get_all_invoices(account_id: str) -> list[dict]:
    db = get_client()
    return [doc.to_dict() for doc in _collection(db, account_id, "invoices").stream()]

def get_all_contracts(account_id: str) -> list[dict]:
    db = get_client()
    return [doc.to_dict() for doc in _collection(db, account_id, "contracts").stream()]
