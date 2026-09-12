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
            "type": f.get('type'),
            "title": f.get('title'),
            "detail": f.get('detail'),
            "monthly_recoverable": f.get('monthly_recoverable'),
            "clause_ref": f.get('clause_ref'),
            "math": f.get('math'),
            "clause_text": f.get('clause_text'),
            "assumption": f.get('assumption'),
            "right_id": f.get('right_id'),
            "expected_state_id": f.get('expected_state_id'),
            "discrepancy_id": f.get('discrepancy_id'),
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

def get_finding(account_id: str, finding_id: str) -> dict | None:
    db = get_client()
    doc = _collection(db, account_id, "findings").document(finding_id).get()
    if not doc.exists:
        return None
    return {"finding_id": doc.id, **doc.to_dict()}


def update_finding_status(account_id: str, finding_id: str, status: str, event_name: str,
                          fields: dict | None = None):
    db = get_client()
    now = datetime.now(timezone.utc).isoformat()

    # Update finding
    doc_ref = _collection(db, account_id, "findings").document(finding_id)
    update_fields = {"status": status}
    if status == "recovered":
        update_fields["recovered_at"] = now
    if fields:
        update_fields.update(fields)
    doc_ref.update(update_fields)

    # Insert audit log
    entry = {
        "finding_id": finding_id,
        "event": event_name,
        "decision": status,
        "ts": now,
    }
    if fields:
        entry["details"] = fields
    _collection(db, account_id, "audit_log").document().set(entry)

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


def get_account_billing(account_id: str) -> dict | None:
    db = get_client()
    doc = _account_root(db, account_id).get()
    if not doc.exists:
        return None
    return (doc.to_dict() or {}).get("billing")


def set_account_billing(account_id: str, billing: dict) -> None:
    db = get_client()
    _account_root(db, account_id).set({"billing": billing}, merge=True)


def update_finding_fields(account_id: str, finding_id: str, fields: dict, event_name: str):
    """Merge fields into a finding without a status change; audit-logged."""
    db = get_client()
    _collection(db, account_id, "findings").document(finding_id).update(fields)
    entry = {
        "finding_id": finding_id,
        "event": event_name,
        "ts": datetime.now(timezone.utc).isoformat(),
        "details": fields,
    }
    _collection(db, account_id, "audit_log").document().set(entry)


def delete_account_data(account_id: str) -> dict[str, int]:
    """Delete every subcollection document under the account root, then the
    root document itself. Returns {collection_name: deleted_count}."""
    db = get_client()
    root = _account_root(db, account_id)
    counts: dict[str, int] = {}
    for coll in root.collections():
        deleted = 0
        batch = db.batch()
        pending = 0
        for doc in coll.stream():
            batch.delete(doc.reference)
            pending += 1
            deleted += 1
            if pending >= 400:
                batch.commit()
                batch = db.batch()
                pending = 0
        if pending:
            batch.commit()
        counts[coll.id] = deleted
    root.delete()
    return counts


# --- RIGHTS DISCOVERY (candidate rights, compiled rights, observations) ---

def save_candidate_rights(account_id: str, candidates: list[dict]):
    db = get_client()
    batch = db.batch()
    for c in candidates:
        d = c.to_dict() if hasattr(c, "to_dict") else dict(c)
        doc = _collection(db, account_id, "candidate_rights").document(
            d["candidate_id"])
        batch.set(doc, d, merge=True)
    batch.commit()


def get_candidate_rights(account_id: str, customer_id: str | None = None) -> list[dict]:
    db = get_client()
    docs = [doc.to_dict() for doc in
            _collection(db, account_id, "candidate_rights").stream()]
    if customer_id is not None:
        docs = [d for d in docs
                if d.get("customer_id") == customer_id
                or (d.get("metadata") or {}).get("customer_id") == customer_id]
    return docs


def save_compiled_right(account_id: str, compiled: dict):
    db = get_client()
    d = compiled.to_dict() if hasattr(compiled, "to_dict") else dict(compiled)
    _collection(db, account_id, "compiled_rights").document(
        d["spec"]["right_id"]).set(d, merge=True)


def get_compiled_rights(account_id: str, customer_id: str | None = None) -> list[dict]:
    db = get_client()
    docs = [doc.to_dict() for doc in
            _collection(db, account_id, "compiled_rights").stream()]
    if customer_id is not None:
        docs = [d for d in docs if d.get("customer_id") == customer_id]
    return docs


def save_observation(account_id: str, obs: dict):
    db = get_client()
    _collection(db, account_id, "observations").document(
        obs["observation_id"]).set(obs, merge=True)


def get_observations(account_id: str, customer_id: str | None = None,
                     period: str | None = None) -> list[dict]:
    db = get_client()
    docs = [doc.to_dict() for doc in
            _collection(db, account_id, "observations").stream()]
    if customer_id is not None:
        docs = [d for d in docs if d.get("customer_id") == customer_id]
    if period is not None:
        docs = [d for d in docs if str(d.get("period", "")).startswith(period)]
    return docs


# --- RECOVERY EVENTS (realized recovered value) ---

def save_recovery_event(account_id: str, event: dict) -> bool:
    """Create-only: returns True when the event doc was written, False when an
    event with the same recovery_event_id already exists (duplicate)."""
    db = get_client()
    ref = _collection(db, account_id, "recovery_events").document(
        event["recovery_event_id"])
    if ref.get().exists:
        return False
    ref.set(event)
    return True


def update_recovery_event_fields(account_id: str, event_id: str, fields: dict,
                                 event_name: str):
    """Merge fields into a recovery event; audit-logged like findings."""
    db = get_client()
    _collection(db, account_id, "recovery_events").document(event_id).update(fields)
    _collection(db, account_id, "audit_log").document().set({
        "event": event_name,
        "recovery_event_id": event_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "details": fields,
    })


def get_recovery_events(account_id: str, finding_id: str | None = None) -> list[dict]:
    db = get_client()
    docs = [doc.to_dict() for doc in
            _collection(db, account_id, "recovery_events").stream()]
    if finding_id is not None:
        docs = [d for d in docs if d.get("finding_id") == finding_id]
    return docs
