import time
from datetime import datetime, timezone
from .cloud.storage import DocumentStore, get_storage_adapter
from .document_quality import (
    NEEDS_VERIFICATION, held_contracts, require_verified_contracts,
    require_verified_document,
)
from .recovery import assert_transition

_client = None
_platform_settings_cache: dict = {"at": 0.0, "value": None}
_tenant_touch_cache: dict[str, float] = {}


class FindingNotFound(Exception):
    pass


class RecoveryConflict(Exception):
    pass


class VerificationResolutionError(ValueError):
    pass


class IllegalTransition(Exception):
    def __init__(self, current: str, new: str):
        self.current, self.new = current, new
        super().__init__(f"Illegal transition: {current} -> {new}")

def get_client() -> DocumentStore:
    global _client
    if _client is None:
        _client = get_storage_adapter().client()
    return _client


def _account_root(db, account_id: str):
    return db.collection("accounts").document(account_id)


STALE_WITHDRAWAL_REASON = ("Superseded: re-evaluation with complete billing "
                           "and usage data no longer reproduces this discrepancy")


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
        existing_data = existing.to_dict() if existing.exists else {}
        status = (f.get("status") or "open") if not existing.exists \
            else existing_data.get("status", "open")

        created_at = (existing_data.get("created_at", now) if existing.exists
                      else f.get('created_at', now))
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
            "expected_value": f.get('expected_value'),
            "actual_value": f.get('actual_value'),
            "created_at": created_at
        }
        if not existing.exists:
            data["status"] = status
        elif (existing_data.get("status") == "rejected"
              and (existing_data.get("withdrawn_by") == "system"
                   or (existing_data.get("withdrawal_reason") == STALE_WITHDRAWAL_REASON
                       and not existing_data.get("rejected_by")))):
            # A finding the system withdrew as stale is re-detected: reopen it.
            # Human rejections (rejected_by / ui_rejection) are never reopened.
            data.update({"status": "open", "withdrawal_reason": None,
                         "withdrawn_by": None, "withdrawn_at": None,
                         "reopened_at": now})
            batch.set(_collection(db, account_id, "audit_log").document(), {
                "finding_id": f.get("finding_id"), "event": "assurance_reopened",
                "decision": "open", "ts": now})
        batch.set(doc_ref, data, merge=True)
    
    batch.commit()

def get_pending_findings(account_id: str) -> list[dict]:
    db = get_client()
    docs = get_storage_adapter().equal(_collection(db, account_id, "findings"), "status", "open")
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


def transition_finding_status(account_id: str, finding_id: str, new_status: str,
                              event_name: str, fields: dict | None = None):
    """Atomically assert and apply a finding lifecycle transition."""
    from .recovery import assert_transition
    db = get_client()
    doc_ref = _collection(db, account_id, "findings").document(finding_id)
    transaction = db.transaction()

    def _run(txn):
        snap = doc_ref.get(transaction=txn)
        if not snap.exists:
            raise FindingNotFound(finding_id)
        current = (snap.to_dict() or {}).get("status", "open")
        if new_status in {"approved", "invoiced", "recovered"}:
            account = _account_root(db, account_id).get(transaction=txn).to_dict() or {}
            require_verified_contracts(list(account.get("document_verification_holds", {}).values()),
                                       (snap.to_dict() or {}).get("customer_id"))
        try:
            assert_transition(current, new_status)
        except ValueError as exc:
            raise IllegalTransition(current, new_status) from exc
        now = datetime.now(timezone.utc).isoformat()
        update_fields = {"status": new_status}
        if new_status == "recovered":
            update_fields["recovered_at"] = now
        if fields:
            update_fields.update(fields)
        txn.update(doc_ref, update_fields)
        entry = {"finding_id": finding_id, "event": event_name,
                 "decision": new_status, "ts": now}
        if fields:
            entry["details"] = fields
        txn.set(_collection(db, account_id, "audit_log").document(), entry)
        return {"finding_id": finding_id, **(snap.to_dict() or {}), **update_fields}

    return get_storage_adapter().transact(transaction, _run)


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

def _contract_write_payload(payload: dict) -> dict:
    # Re-uploading a contract must not inherit a prior version's human
    # confirmation; only an explicit confirmed flag survives the write.
    if "confirmed" in payload:
        return payload
    return {**payload, "confirmed": False, "confirmed_by": None,
            "confirmed_at": None,
            "term_conflicts": payload.get("term_conflicts", []),
            "unresolved_terms": payload.get("unresolved_terms", []),
            "term_resolutions": payload.get("term_resolutions")}


def save_contract(account_id: str, payload: dict):
    db = get_client()
    _collection(db, account_id, "contracts").document(payload["customer_id"]).set(
        _contract_write_payload(payload), merge=True)
    for source in payload.get("documents") or [payload]:
        verification = source.get("structural_verification") or {}
        if source.get("document_id") and verification.get("state") == "Verified":
            _collection(db, account_id, "documents").document(source["document_id"]).set({
                "document_id": source["document_id"],
                "file_name": source.get("file_name"),
                "customer_id": payload["customer_id"],
                "customer_name": payload.get("customer_name"),
                "state": "Verified", "structural_verification": verification,
            }, merge=True)

def get_all_usage(account_id: str) -> list[dict]:
    db = get_client()
    return [doc.to_dict() for doc in _collection(db, account_id, "usage").stream()]

def get_all_invoices(account_id: str) -> list[dict]:
    db = get_client()
    return [doc.to_dict() for doc in _collection(db, account_id, "invoices").stream()]

def get_all_contracts(account_id: str) -> list[dict]:
    db = get_client()
    return [doc.to_dict() for doc in _collection(db, account_id, "contracts").stream()]


def hold_document(account_id: str, document: dict) -> dict:
    store = get_client()
    document_id = document["document_id"]
    contracts = get_all_contracts(account_id)
    related = [c for c in contracts if c.get("document_id") == document_id
               or any(d.get("document_id") == document_id for d in c.get("documents", []))]
    scope = "customer" if related else "account"
    affected = related or [c for c in contracts if c.get("verification_scope") != "account"]
    placeholder = {
        "customer_id": "verification_" + document_id,
        "customer_name": document["file_name"],
        "file_name": document["file_name"], "verification_scope": scope,
        "verification_state": NEEDS_VERIFICATION,
        "verification_document_ids": [document_id],
        "verification_customer_ids": [c["customer_id"] for c in affected],
        "structural_verification": document,
        "confirmed": False,
    }
    ids = {c["customer_id"] for c in affected}
    now = datetime.now(timezone.utc).isoformat()
    record = {**document, "state": NEEDS_VERIFICATION, "scope": scope,
              "customer_ids": sorted(ids), "created_at": now}
    doc_ref = _collection(store, account_id, "documents").document(document_id)
    batch = store.batch()
    batch.set(doc_ref, record, merge=True)
    batch.set(_collection(store, account_id, "contracts").document(placeholder["customer_id"]),
              placeholder)
    batch.set(_collection(store, account_id, "audit_log").document(), {
        "event": "document_structure_held", "document_id": document_id,
        "state": NEEDS_VERIFICATION, "customer_ids": sorted(ids), "ts": now})
    batch.set(_account_root(store, account_id), {
        "document_verification_holds": {
            document_id: {
                "verification_state": NEEDS_VERIFICATION, "verification_scope": scope,
                "verification_customer_ids": sorted(ids),
            },
        },
    }, merge=True)
    pending = 4
    for contract in affected:
        cid = contract["customer_id"]
        held_ids = sorted(set(contract.get("verification_document_ids", [])) | {document_id})
        batch.set(_collection(store, account_id, "contracts").document(cid), {
            "verification_state": NEEDS_VERIFICATION,
            "verification_document_ids": held_ids,
            "structural_verification": document,
            "confirmed": False, "confirmed_by": None, "confirmed_at": None,
        }, merge=True)
        pending += 1
        if pending >= 400:
            batch.commit()
            batch, pending = store.batch(), 0
    findings = get_all_findings(account_id)
    for finding in findings:
        if scope == "account" or finding.get("customer_id") in ids:
            batch.set(_collection(store, account_id, "findings").document(finding["finding_id"]), {
                "verification_state": NEEDS_VERIFICATION,
                "structural_verification": document,
                "verification_document_ids": sorted(
                    set(finding.get("verification_document_ids", [])) | {document_id}),
            }, merge=True)
            pending += 1
            if pending >= 400:
                batch.commit()
                batch, pending = store.batch(), 0
    if pending:
        batch.commit()
    return record


def verified_replacements(account_id: str) -> list[dict]:
    return [s.to_dict() for s in _collection(get_client(), account_id, "documents").stream()
            if (s.to_dict() or {}).get("state") == "Verified"]


def resolve_document_hold(account_id: str, document_id: str,
                          replacement_id: str, actor: str) -> dict:
    store = get_client()
    root = _account_root(store, account_id)
    document_ref = root.collection("documents").document(document_id)
    replacement_ref = root.collection("documents").document(replacement_id)
    original = document_ref.get().to_dict() or {}

    def validate(txn):
        account = root.get(transaction=txn).to_dict() or {}
        document = document_ref.get(transaction=txn).to_dict() or {}
        replacement = replacement_ref.get(transaction=txn).to_dict() or {}
        holds = account.get("document_verification_holds", {})
        if (document_id not in holds or document.get("state") != NEEDS_VERIFICATION
                or document.get("created_at") != original.get("created_at")):
            raise VerificationResolutionError("This document has no current matching hold.")
        if (replacement_id == document_id or replacement.get("state") != "Verified"
                or (replacement.get("structural_verification") or {}).get("state") != "Verified"
                or not replacement.get("customer_id")):
            raise VerificationResolutionError("Choose a successfully verified replacement upload.")
        if (document.get("scope") == "customer"
                and replacement["customer_id"] not in document.get("customer_ids", [])):
            raise VerificationResolutionError("The replacement belongs to a different customer.")
        contract = root.collection("contracts").document(
            replacement["customer_id"]).get(transaction=txn).to_dict() or {}
        source_ids = {s.get("document_id") for s in contract.get("documents", [])}
        source_ids.add(contract.get("document_id"))
        if replacement_id not in source_ids:
            raise VerificationResolutionError("The replacement is no longer in the current agreement.")
        return {key: value for key, value in holds.items() if key != document_id}

    get_storage_adapter().transact(store.transaction(), validate)
    references = [
        snapshot.reference
        for name in ("contracts", "findings")
        for snapshot in root.collection(name).stream()
        if not (snapshot.to_dict() or {}).get("verification_scope")
    ]
    for start in range(0, len(references), 350):
        chunk = references[start:start + 350]

        def project(txn):
            remaining = validate(txn)
            snapshots = [ref.get(transaction=txn) for ref in chunk]
            for snap in snapshots:
                data = snap.to_dict() or {}
                active = {key: hold for key, hold in remaining.items()
                          if held_contracts([hold], data.get("customer_id"))}
                if document_id not in data.get("verification_document_ids", []):
                    continue
                txn.update(snap.reference, {
                    "verification_document_ids": sorted(active),
                    "verification_state": NEEDS_VERIFICATION if active else "Verified",
                    "structural_verification": {
                        "state": NEEDS_VERIFICATION if active else "Verified",
                        "replacement_document_id": replacement_id,
                    },
                })

        get_storage_adapter().transact(store.transaction(), project)

    def finish(txn):
        remaining = validate(txn)
        resolution = {
            "state": "Resolved", "replacement_document_id": replacement_id,
            "resolved_by": actor, "resolved_at": datetime.now(timezone.utc).isoformat(),
        }
        txn.update(root, {"document_verification_holds": remaining})
        txn.update(document_ref, resolution)
        txn.delete(root.collection("contracts").document("verification_" + document_id))
        txn.set(root.collection("audit_log").document(), {
            "event": "document_structure_resolved", "document_id": document_id, **resolution,
        })
        return {"document_id": document_id, **resolution}

    return get_storage_adapter().transact(store.transaction(), finish)


def confirm_contract(account_id: str, customer_id: str, actor: str | None) -> dict | None:
    db = get_client()
    contract_ref = _collection(db, account_id, "contracts").document(customer_id)
    snapshot = contract_ref.get()
    if not snapshot.exists:
        return None
    now = datetime.now(timezone.utc).isoformat()
    fields = {
        "confirmed": True,
        "confirmed_by": actor or "unknown",
        "confirmed_at": now,
    }
    contract_ref.update(fields)
    _collection(db, account_id, "audit_log").document().set({
        "event": "contract_terms_confirmed",
        "customer_id": customer_id,
        "actor": actor or "unknown",
        "ts": now,
    })
    return {"customer_id": customer_id, **(snapshot.to_dict() or {}), **fields}


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

def save_recovery_event(account_id: str, event: dict, *,
                        expected_finding: dict | None = None,
                        finding_fields: dict | None = None,
                        event_name: str = "recovery_realized") -> bool:
    """Commit the event and case projection under the same verification guard."""
    store = get_client()
    root = _account_root(store, account_id)
    ref = root.collection("recovery_events").document(
        event["recovery_event_id"])
    finding_ref = root.collection("findings").document(event["finding_id"])

    def record(txn):
        if ref.get(transaction=txn).exists:
            return False
        snap = finding_ref.get(transaction=txn)
        if not snap.exists:
            raise FindingNotFound(event["finding_id"])
        finding = {"finding_id": snap.id, **(snap.to_dict() or {})}
        account = root.get(transaction=txn).to_dict() or {}
        contract = root.collection("contracts").document(
            finding["customer_id"]).get(transaction=txn).to_dict() or {}
        require_verified_contracts(
            [*account.get("document_verification_holds", {}).values(), finding, contract],
            finding["customer_id"])
        require_verified_document(finding.get("structural_verification"))
        if expected_finding is not None and finding != expected_finding:
            raise RecoveryConflict("The finding changed; refresh before recording recovery.")
        current = finding.get("status", "open")
        if current not in {"approved", "invoiced", "disputed", "recovered"}:
            raise IllegalTransition(current, "recovered")
        if finding_fields and finding_fields.get("status", current) != current:
            try:
                assert_transition(current, finding_fields["status"])
            except ValueError as exc:
                raise IllegalTransition(current, finding_fields["status"]) from exc
        txn.set(ref, event)
        if finding_fields:
            txn.update(finding_ref, finding_fields)
        txn.set(root.collection("audit_log").document(), {
            "event": event_name, "finding_id": event["finding_id"],
            "recovery_event_id": event["recovery_event_id"],
            "details": finding_fields or {}, "ts": datetime.now(timezone.utc).isoformat(),
        })
        return True

    return get_storage_adapter().transact(store.transaction(), record)


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


def get_audit_log(account_id: str, finding_id: str | None = None) -> list[dict]:
    db = get_client()
    docs = [doc.to_dict() for doc in
            _collection(db, account_id, "audit_log").stream()]
    if finding_id is not None:
        docs = [d for d in docs if d.get("finding_id") == finding_id]
    docs.sort(key=lambda e: e.get("ts") or "")
    return docs


def get_recovery_events(account_id: str, finding_id: str | None = None) -> list[dict]:
    db = get_client()
    docs = [doc.to_dict() for doc in
            _collection(db, account_id, "recovery_events").stream()]
    if finding_id is not None:
        docs = [d for d in docs if d.get("finding_id") == finding_id]
    return docs


# --- CONTINUOUS ASSURANCE (change events + per-account status) ---

def save_assurance_event(account_id: str, event: dict):
    """Upsert by event_id — identical replays converge to one doc."""
    db = get_client()
    _collection(db, account_id, "assurance_events").document(
        event["event_id"]).set(event, merge=True)


def assurance_event_exists(account_id: str, event_id: str) -> bool:
    db = get_client()
    return _collection(db, account_id, "assurance_events").document(event_id).get().exists


def get_assurance_events(account_id: str, limit: int = 50) -> list[dict]:
    db = get_client()
    docs = [doc.to_dict() for doc in
            _collection(db, account_id, "assurance_events").stream()]
    docs.sort(key=lambda e: e.get("received_at") or "", reverse=True)
    return docs[:limit]


def append_assurance_audit(account_id: str, entry: dict):
    db = get_client()
    _collection(db, account_id, "audit_log").document().set(
        {"ts": datetime.now(timezone.utc).isoformat(), **entry})


def get_assurance_status(account_id: str) -> dict | None:
    db = get_client()
    doc = _account_root(db, account_id).get()
    if not doc.exists:
        return None
    return (doc.to_dict() or {}).get("assurance")


def set_assurance_status(account_id: str, status: dict) -> None:
    db = get_client()
    _account_root(db, account_id).set({"assurance": status}, merge=True)

# --- RECOVERY ACTIONS ---

def save_recovery_action(account_id: str, action: dict):
    db = get_client()
    _collection(db, account_id, "recovery_actions").document(
        action["recovery_action_id"]).set(action, merge=True)


def get_recovery_action(account_id: str, action_id: str) -> dict | None:
    db = get_client()
    doc = _collection(db, account_id, "recovery_actions").document(action_id).get()
    if not doc.exists:
        return None
    return doc.to_dict()


def get_recovery_actions(account_id: str, finding_id: str | None = None) -> list[dict]:
    db = get_client()
    docs = [doc.to_dict() for doc in
            _collection(db, account_id, "recovery_actions").stream()]
    if finding_id is not None:
        docs = [d for d in docs if d.get("finding_id") == finding_id]
    docs.sort(key=lambda d: d.get("created_at") or "")
    return docs


def update_recovery_action(account_id: str, action: dict, event_name: str):
    """Write the full action doc plus an audit_log entry."""
    db = get_client()
    _collection(db, account_id, "recovery_actions").document(
        action["recovery_action_id"]).set(action, merge=True)
    _collection(db, account_id, "audit_log").document().set({
        "event": event_name,
        "recovery_action_id": action.get("recovery_action_id"),
        "finding_id": action.get("finding_id"),
        "ts": datetime.now(timezone.utc).isoformat(),
        "details": {"status": action.get("status"),
                    "channel": action.get("channel")},
    })


def save_outcome_record(account_id: str, record: dict):
    """Upsert the structured outcome record keyed by finding_id (tenant-
    scoped, write-only store)."""
    db = get_client()
    _collection(db, account_id, "outcome_records").document(
        record["finding_id"]).set(record)


def get_outcome_record(account_id: str, finding_id: str) -> dict | None:
    db = get_client()
    doc = _collection(db, account_id, "outcome_records").document(
        finding_id).get()
    return doc.to_dict() if doc.exists else None


# --- PLATFORM ADMIN (platform-level data, outside tenant accounts/) ---

def _platform_document(db, name: str):
    return db.collection("platform").document(name)


def _platform_tenants(db):
    return _platform_document(db, "tenants").collection("accounts")


def _platform_admin_audit(db):
    return _platform_document(db, "admin").collection("audit")


def get_platform_settings() -> dict:
    now = time.time()
    cached = _platform_settings_cache.get("value")
    if cached is not None and now - _platform_settings_cache["at"] < 30:
        return dict(cached)
    doc = _platform_document(get_client(), "settings").get()
    data = doc.to_dict() if doc.exists else {}
    value = {
        "signup_enabled": bool(data.get("signup_enabled", True)),
        "invited_emails": list(data.get("invited_emails") or []),
    }
    _platform_settings_cache.update({"at": now, "value": value})
    return dict(value)


def set_platform_settings(settings: dict) -> dict:
    value = {
        "signup_enabled": bool(settings.get("signup_enabled", True)),
        "invited_emails": list(settings.get("invited_emails") or []),
    }
    _platform_document(get_client(), "settings").set(value, merge=True)
    _platform_settings_cache.update({"at": time.time(), "value": value})
    return dict(value)


def touch_tenant(account_id: str, email: str | None) -> None:
    now = time.time()
    last = _tenant_touch_cache.get(account_id)
    if last is not None and now - last < 600:
        return
    ref = _platform_tenants(get_client()).document(account_id)
    snap = ref.get()
    existing = snap.to_dict() if snap.exists else {}
    seen_at = datetime.now(timezone.utc).isoformat()
    ref.set({
        "account_id": account_id,
        "email": email,
        "first_seen": existing.get("first_seen") or seen_at,
        "last_seen": seen_at,
        "demo": bool(existing.get("demo", False)),
    }, merge=True)
    _tenant_touch_cache[account_id] = now


def list_tenants() -> list[dict]:
    docs = [doc.to_dict() for doc in _platform_tenants(get_client()).stream()]
    docs.sort(key=lambda d: (d.get("last_seen") or "", d.get("account_id") or ""),
              reverse=True)
    return docs


def get_tenant(account_id: str) -> dict | None:
    doc = _platform_tenants(get_client()).document(account_id).get()
    return doc.to_dict() if doc.exists else None


def set_tenant_demo(account_id: str, demo: bool, **fields) -> dict:
    data = {"account_id": account_id, "demo": bool(demo), **fields}
    ref = _platform_tenants(get_client()).document(account_id)
    ref.set(data, merge=True)
    _tenant_touch_cache.pop(account_id, None)
    doc = ref.get()
    return doc.to_dict() if doc.exists else data


def append_admin_audit(entry: dict) -> dict:
    data = {"at": datetime.now(timezone.utc).isoformat(), **entry}
    _platform_admin_audit(get_client()).document().set(data)
    return data


def list_admin_audit(limit: int = 100) -> list[dict]:
    docs = [doc.to_dict() for doc in _platform_admin_audit(get_client()).stream()]
    docs.sort(key=lambda e: e.get("at") or "", reverse=True)
    return docs[:limit]


# --- STRIPE WEBHOOK EVENTS (platform-level idempotency store) ---

def _webhook_events(db):
    return _platform_document(db, "webhooks").collection("stripe_events")


def save_stripe_webhook_event(event_id: str, summary: dict) -> bool:
    """Create-only idempotency record; returns False when already processed."""
    db = get_client()
    ref = _webhook_events(db).document(event_id)
    return get_storage_adapter().create_once(
        ref, {"stripe_event_id": event_id,
              "processed_at": datetime.now(timezone.utc).isoformat(), **summary})


def find_recovery_event_by_fee_invoice(invoice_id: str) -> tuple[str, dict] | None:
    """Locate the recovery event whose fee invoice is invoice_id. Checks the
    top-level fee_invoice_id, then fee_charge.invoice_id for older records."""
    db = get_client()
    for account_doc in db.collection("accounts").stream():
        account_id = account_doc.id
        events = account_doc.reference.collection("recovery_events").stream()
        for doc in events:
            event = doc.to_dict() or {}
            charge = event.get("fee_charge") or {}
            if (event.get("fee_invoice_id") == invoice_id
                    or charge.get("invoice_id") == invoice_id
                    or charge.get("charge_id") == invoice_id):
                return account_id, event
    return None


def find_recovery_event_by_credit_note(credit_note_id: str) -> tuple[str, dict] | None:
    db = get_client()
    for account_doc in db.collection("accounts").stream():
        for doc in account_doc.reference.collection("recovery_events").stream():
            event = doc.to_dict() or {}
            if (event.get("fee_charge") or {}).get("credit_note_id") == credit_note_id:
                return account_doc.id, event
    return None


def find_account_by_stripe_customer(customer_id: str) -> str | None:
    db = get_client()
    for account_doc in db.collection("accounts").stream():
        billing = (account_doc.to_dict() or {}).get("billing") or {}
        if billing.get("stripe_customer_id") == customer_id:
            return account_doc.id
    return None


# --- TERMS ACCEPTANCE ---

def record_terms_acceptance(account_id: str, record: dict) -> dict:
    data = {**record, "recorded_at": datetime.now(timezone.utc).isoformat()}
    _account_root(get_client(), account_id).set(
        {"terms_acceptance": data}, merge=True)
    return data


def get_terms_acceptance(account_id: str) -> dict | None:
    doc = _account_root(get_client(), account_id).get()
    if not doc.exists:
        return None
    return (doc.to_dict() or {}).get("terms_acceptance")
