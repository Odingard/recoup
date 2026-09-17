import copy
import io
from dataclasses import replace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from google.cloud import documentai
from reportlab.pdfgen import canvas

from recoup_agent import api, assurance, command_center, db, ingestion_doc, pipeline
from recoup_agent.cloud.documents import LayoutToken, Page, Point, TextBlock, TextSpan
from recoup_agent.cloud.google_documents import _document_ai_pages
from recoup_agent.cloud.local_documents import LocalDocumentAdapter, pdf_text_pages, rectangle
from recoup_agent.cloud.visual_structure import inspect_raster
from recoup_agent.document_quality import (
    LowConfidenceGateException,
    NEEDS_VERIFICATION,
    StructuralIssue,
    inspect_structure,
    require_sound_structure,
)
from recoup_agent.normalizer import normalize_contract_entitlements
from recoup_agent.billing import realized_value as rv
from recoup_agent.cloud.local_documents import native_pages_complete
from recoup_agent.extraction.extractor import DocumentProfile, ExtractionResult


def renewal_pdf(masked=False, strike_height=2.2):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(612, 792))
    pdf.setFont("Helvetica", 18)
    pdf.drawString(50, 740, "Service agreement for Redwood Company")
    pdf.drawString(50, 700, "Renewal requires sixty days notice.")
    pdf.drawString(50, 660, "Customer commits to minimum seats.")
    if masked:
        pdf.setFillColorRGB(1, 1, 1)
        pdf.rect(48, 706, 480, strike_height, fill=1, stroke=0)
    pdf.save()
    return buffer.getvalue()


def critical_page():
    text = "Renewal requires sixty days notice."
    polygon = rectangle(0.1, 0.2, 0.7, 0.03, 1, 1)
    tokens = []
    offset = 0
    for i, word in enumerate(text.split()):
        tokens.append(LayoutToken(word, rectangle(0.1 + i*0.13, 0.2, 0.12, 0.03, 1, 1),
                                  (TextSpan(offset, offset+len(word)),)))
        offset += len(word)+1
    block = TextBlock(text, 0.995, polygon, (TextSpan(0, len(text)),), tuple(tokens))
    return Page(1, text, (block,), 0.995, text, True)


def test_clean_and_masked_pdf_with_unchanged_high_confidence(tmp_path):
    outputs = []
    for masked in (False, True):
        path = tmp_path / f"renewal-{masked}.pdf"
        path.write_bytes(renewal_pdf(masked))
        pages = [replace(p, ocr_confidence=0.995) for p in pdf_text_pages(str(path))]
        assert pages[0].ocr_confidence > 0.99
        assert not inspect_structure(pages)
        outputs.append(inspect_raster(str(path), pages))
    assert outputs[0] == []
    assert {issue.code for issue in outputs[1]} == {"horizontal_text_break"}


def test_clean_geometry_does_not_depend_on_character_confidence():
    page = critical_page()
    require_sound_structure([page])
    require_sound_structure([replace(page, ocr_confidence=0.1,
                                    blocks=(replace(page.blocks[0], confidence=0.1),))])


@pytest.mark.parametrize("polygon", [
    (), (Point(0, 0), Point(0.1, 0.1)),
    (Point(0, 0), Point(1, 1), Point(0, 1), Point(1, 0)),
    (Point(float("nan"), 0), Point(1, 0), Point(1, 1)),
    rectangle(-0.1, 0.2, 0.5, 0.2, 1, 1),
])
def test_irregular_polygons_fail_closed(polygon):
    page = critical_page()
    page = replace(page, blocks=(replace(page.blocks[0], polygon=polygon),))
    with pytest.raises(LowConfidenceGateException):
        require_sound_structure([page])


@pytest.mark.parametrize("change,code", [
    ({"spans": (TextSpan(-1, 5),)}, "token_anchor_drift"),
    ({"spans": (TextSpan(1, 8),)}, "token_anchor_drift"),
    ({"polygon": rectangle(0.01, 0.2, 0.1, 0.03, 1, 1)}, "token_boundary_drift"),
])
def test_token_drift(change, code):
    page = critical_page()
    block = page.blocks[0]
    token = replace(block.tokens[0], **change)
    page = replace(page, blocks=(replace(block, tokens=(token, *block.tokens[1:])),))
    assert code in {issue.code for issue in inspect_structure([page])}


def test_subword_token_density_gate():
    page = critical_page()
    block = page.blocks[0]
    tokens = tuple(LayoutToken(char, rectangle(0.1 + i*0.019, 0.2, 0.016, 0.03, 1, 1),
                               (TextSpan(i, i+1),))
                   for i, char in enumerate(page.text) if not char.isspace())
    page = replace(page, blocks=(replace(block, tokens=tokens),))
    assert "subword_fragmentation" in {issue.code for issue in inspect_structure([page])}


def test_microscopic_blocks_and_tokens_fail_closed():
    page = critical_page()
    shards = tuple(TextBlock(
        "-", 0.995, rectangle(0.1 + i*0.05, 0.232, 0.004, 0.003, 1, 1),
        tokens=(LayoutToken("-"),)) for i in range(6))
    fragmented = replace(page, blocks=(*page.blocks, *shards))
    assert "block_fragmentation" in {i.code for i in inspect_structure([fragmented])}
    block = page.blocks[0]
    tokens = tuple(replace(token, polygon=rectangle(
        token.polygon[0].x, 0.2, 0.05, 0.0008, 1, 1)) for token in block.tokens)
    microscopic = replace(page, blocks=(replace(block, tokens=tokens),))
    assert "microscopic_tokens" in {i.code for i in inspect_structure([microscopic])}


def test_missing_layout_fails_closed():
    with pytest.raises(LowConfidenceGateException):
        require_sound_structure([Page(1, "Renewal requires sixty days notice.")])


def test_document_ai_protobuf_preserves_normalized_geometry_and_anchors():
    page = critical_page()
    block = page.blocks[0]

    def layout(text_spans, polygon):
        return {"text_anchor": {"text_segments": [
            {"start_index": s.start, "end_index": s.end} for s in text_spans]},
            "bounding_poly": {"normalized_vertices": [{"x": p.x, "y": p.y} for p in polygon]},
            "confidence": 0.995}

    document = documentai.Document(
        text=page.text, pages=[{
            "layout": layout(block.spans, block.polygon),
            "blocks": [{"layout": layout(block.spans, block.polygon)}],
            "tokens": [{"layout": layout(t.spans, t.polygon)} for t in block.tokens],
        }])
    converted = _document_ai_pages(document)
    assert converted[0].ocr_confidence > 0.99
    assert len(converted[0].blocks[0].tokens) == 5
    require_sound_structure(converted)


class MemorySnapshot:
    def __init__(self, reference):
        self.reference = reference
        self.id = reference.path[-1]
        self.exists = reference.path in reference.store.data
        self.value = copy.deepcopy(reference.store.data.get(reference.path))

    def to_dict(self):
        return copy.deepcopy(self.value)


class MemoryDocument:
    def __init__(self, store, path):
        self.store, self.path = store, path

    def collection(self, name):
        return MemoryCollection(self.store, (*self.path, name))

    def get(self, transaction=None):
        return MemorySnapshot(self)

    def set(self, data, merge=False):
        def merged(old, new):
            result = copy.deepcopy(old)
            for key, value in new.items():
                result[key] = (merged(result[key], value)
                               if isinstance(result.get(key), dict) and isinstance(value, dict)
                               else copy.deepcopy(value))
            return result
        self.store.data[self.path] = (
            merged(self.store.data.get(self.path, {}), data) if merge else copy.deepcopy(data))

    def update(self, data):
        self.store.data[self.path].update(copy.deepcopy(data))

    def delete(self):
        self.store.data.pop(self.path, None)


class MemoryCollection:
    def __init__(self, store, path):
        self.store, self.path = store, path

    def document(self, document_id=None):
        self.store.sequence += 1
        return MemoryDocument(self.store, (*self.path, document_id or str(self.store.sequence)))

    def stream(self):
        return [MemoryDocument(self.store, path).get() for path in list(self.store.data)
                if path[:-1] == self.path]


class MemoryBatch:
    def __init__(self):
        self.writes = []

    def set(self, ref, data, merge=False):
        self.writes.append(lambda: ref.set(data, merge))

    def update(self, ref, data):
        self.writes.append(lambda: ref.update(data))

    def delete(self, ref):
        self.writes.append(ref.delete)

    def commit(self):
        for write in self.writes:
            write()


class MemoryStore:
    def __init__(self):
        self.data = {}
        self.sequence = 0

    def collection(self, name):
        return MemoryCollection(self, (name,))

    def batch(self):
        return MemoryBatch()

    def transaction(self):
        return self.batch()


class MemoryAdapter:
    def transact(self, transaction, operation):
        result = operation(transaction)
        transaction.commit()
        return result

    def equal(self, collection, field, value):
        return [s for s in collection.stream() if s.to_dict().get(field) == value]


@pytest.fixture
def isolated_store(monkeypatch):
    store = MemoryStore()
    monkeypatch.setattr(db, "get_client", lambda: store)
    monkeypatch.setattr(db, "get_storage_adapter", MemoryAdapter)
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    monkeypatch.delenv("RECOUP_BILLING_SOURCE", raising=False)
    return store


def seed(account="tenant-a", filename="agreement.pdf"):
    db.save_contract(account, {
        "customer_id": "redwood", "customer_name": "Redwood", "file_name": filename,
        "committed_minimum_monthly": 1000, "confirmed": True})
    db.save_findings(account, [{"finding_id": "case-1", "customer_id": "redwood",
                               "status": "approved", "monthly_recoverable": 100}])


def test_masked_pdf_holds_workspace_and_cases_without_reconciliation(isolated_store, monkeypatch):
    seed()
    seed("tenant-b")
    extraction = Mock(side_effect=AssertionError("Model extraction must not run."))
    reconcile = Mock(side_effect=AssertionError("Reconciliation must not run."))
    monkeypatch.setattr("recoup_agent.extraction.extractor.extract_pages", extraction)
    monkeypatch.setattr(pipeline, "reconcile", reconcile)
    response = api._ingest_contract_bytes("tenant-a", "agreement.pdf", renewal_pdf(True))
    assert response["state"] == NEEDS_VERIFICATION
    assert {i["code"] for i in response["needs_review"][0]["issues"]} == {"horizontal_text_break"}
    assert assurance.account_status("tenant-a")["state"] == NEEDS_VERIFICATION
    assert db.get_finding("tenant-a", "case-1")["verification_state"] == NEEDS_VERIFICATION
    assert db.get_finding("tenant-b", "case-1").get("verification_state") is None
    assert db.get_all_contracts("tenant-b")[0]["confirmed"]
    with pytest.raises(LowConfidenceGateException):
        db.transition_finding_status("tenant-a", "case-1", "invoiced", "test")
    with pytest.raises(LowConfidenceGateException):
        api.create_recovery_action(
            "case-1", api.RecoveryActionCreate(action_type="credit_request"),
            user={"account_id": "tenant-a"})
    held = db.get_finding("tenant-a", "case-1")
    assert not command_center.is_verified(held)
    assert command_center.next_step(held, []) == "Inspect the document structure before recovery"
    with pytest.raises(LowConfidenceGateException):
        pipeline.compute_findings_and_review("2026-06", account_id="tenant-a")
    with pytest.raises(LowConfidenceGateException):
        api.confirm_contract("redwood", user={"account_id": "tenant-a"})
    event = assurance.make_event("tenant-a", "new_invoice", "redwood",
                                "2026-06", "test", {})
    assert assurance.evaluate_event("tenant-a", event)["status"] == NEEDS_VERIFICATION
    extraction.assert_not_called()
    reconcile.assert_not_called()


def test_normalizer_rejects_structurally_held_extraction():
    contract = ingestion_doc.ContractEntitlements(
        customer_name="Redwood", entitlements=[],
        structural_verification={"state": NEEDS_VERIFICATION, "issues": []})
    with pytest.raises(LowConfidenceGateException):
        normalize_contract_entitlements(contract)


def test_account_hold_blocks_transitions_after_projection_write_failure(
        isolated_store, monkeypatch):
    seed()
    db.save_findings("tenant-a", [
        {"finding_id": f"case-{i}", "customer_id": "acme", "status": "open"}
        for i in range(2, 403)])
    batches = 0

    class FailingBatch(MemoryBatch):
        def commit(self):
            raise RuntimeError("Storage unavailable")

    def batch():
        nonlocal batches
        batches += 1
        return MemoryBatch() if batches == 1 else FailingBatch()

    monkeypatch.setattr(isolated_store, "batch", batch)
    hold = LowConfidenceGateException([
        StructuralIssue(1, "anchor_drift", "Critical text anchors disagree.")
    ]).payload()
    with pytest.raises(RuntimeError, match="Storage unavailable"):
        db.hold_document("tenant-a", {
            **hold, "document_id": "held-document", "file_name": "agreement.pdf"})
    unprojected = db.get_finding("tenant-a", "case-402")
    assert unprojected.get("verification_state") is None
    with pytest.raises(LowConfidenceGateException):
        db.transition_finding_status("tenant-a", "case-402", "approved", "test")


def test_masked_pdf_with_high_native_ocr_confidence_holds_workspace(
        isolated_store, monkeypatch, tmp_path):
    seed()
    extraction = Mock(side_effect=AssertionError("Model extraction must not run."))
    monkeypatch.setattr("recoup_agent.extraction.extractor.extract_pages", extraction)
    for masked in (False, True):
        content = renewal_pdf(masked, strike_height=0.5)
        ocr_pages = LocalDocumentAdapter().page_texts(content, "application/pdf")
        assert ocr_pages[0].ocr_confidence > 0.9
        path = tmp_path / f"native-ocr-{masked}.pdf"
        path.write_bytes(content)
        geometry = [replace(page, ocr_confidence=ocr_pages[0].ocr_confidence)
                    for page in pdf_text_pages(str(path))]
        issues = inspect_raster(str(path), geometry)
        if masked:
            assert issues[0].code == "horizontal_text_break"
            assert issues[0].observed > issues[0].limit
            result = api._ingest_contract_bytes("tenant-a", "agreement.pdf", content)
            assert result["state"] == NEEDS_VERIFICATION
            assert assurance.account_status("tenant-a")["state"] == NEEDS_VERIFICATION
        else:
            assert not issues
    extraction.assert_not_called()


def test_bulk_masks_hold_other_files_before_reconciliation(isolated_store, monkeypatch):
    seed()
    monkeypatch.setattr("recoup_agent.extraction.extractor.extract_pages",
                        Mock(side_effect=AssertionError("No model calls expected.")))
    api.app.dependency_overrides[api.verify_token] = lambda: {"account_id": "tenant-a"}
    try:
        response = TestClient(api.app).post("/api/ingest/bulk", files=[
            ("files", ("agreement.pdf", renewal_pdf(True), "application/pdf")),
            ("files", ("usage.csv", b"customer,period,units\nRedwood,2026-06,240\n", "text/csv")),
        ])
        assert response.status_code == 200
        assert response.json()["state"] == NEEDS_VERIFICATION
        assert response.json()["usage"] == 0
        assert assurance.account_status("tenant-a")["state"] == NEEDS_VERIFICATION
        assert db.get_all_usage("tenant-a") == []
    finally:
        api.app.dependency_overrides.clear()


def test_unassigned_document_holds_entire_tenant(isolated_store):
    seed()
    hold = {
        **LowConfidenceGateException([StructuralIssue(1, "anchor_drift", "Drift")]).payload(),
        "document_id": "new-document", "file_name": "unknown-customer.pdf",
    }
    db.hold_document("tenant-a", hold)
    with pytest.raises(LowConfidenceGateException):
        pipeline.compute_findings_and_review("2026-06", account_id="tenant-a",
                                             customer_ids={"another-customer"})


def add_hold(document_id="held-document"):
    return db.hold_document("tenant-a", {
        **LowConfidenceGateException([StructuralIssue(1, "anchor_drift", "Drift")]).payload(),
        "document_id": document_id, "file_name": "agreement.pdf",
    })


@pytest.mark.parametrize("kind", ["partial", "closing", "settlement", "reversal", "legacy"])
def test_held_realization_has_no_event_fee_or_case_side_effects(isolated_store, monkeypatch, kind):
    seed()
    finding = db.get_finding("tenant-a", "case-1")
    original = rv.new_realization("tenant-a", finding, recovery_basis="cash_payment",
                                  realized_value=10, external_reference="original")
    db.save_recovery_event("tenant-a", original.to_dict())
    add_hold()
    fee = Mock(side_effect=AssertionError("Held recovery cannot bill."))
    monkeypatch.setattr(api.recoup_billing, "charge_success_fee_for_event", fee)
    monkeypatch.setattr(api.recoup_billing, "adjust_success_fee_for_reversal", fee)
    before = copy.deepcopy(isolated_store.data)
    api.app.dependency_overrides[api.verify_token] = lambda: {"account_id": "tenant-a"}
    try:
        path = "/api/findings/case-1/recovery-events"
        payload = {"recovery_basis": "settlement" if kind == "settlement" else "cash_payment",
                   "realized_value": 5 if kind == "partial" else 100,
                   "external_reference": kind}
        if kind == "reversal":
            path += f"/{original.recovery_event_id}/reverse"
            payload = {"reversal_amount": 1, "reversal_reference": "reverse"}
        if kind == "legacy":
            path = "/api/findings/case-1/recovered"
            payload = {"paid_amount": 100, "payment_ref": "legacy"}
        response = TestClient(api.app).post(path, json=payload)
        assert response.status_code == 409
        assert isolated_store.data == before
        fee.assert_not_called()
    finally:
        api.app.dependency_overrides.clear()


def test_recovery_transaction_checks_root_hold_before_missing_projection(isolated_store):
    seed()
    finding = db.get_finding("tenant-a", "case-1")
    isolated_store.collection("accounts").document("tenant-a").set({
        "document_verification_holds": {"h": {
            "verification_state": NEEDS_VERIFICATION, "verification_scope": "account"}}})
    event = rv.new_realization("tenant-a", finding, recovery_basis="cash_payment",
                               realized_value=10, external_reference="late")
    before = copy.deepcopy(isolated_store.data)
    with pytest.raises(LowConfidenceGateException):
        db.save_recovery_event("tenant-a", event.to_dict(), expected_finding=finding,
                               finding_fields={"recovered_amount": 10})
    assert isolated_store.data == before


def test_recovery_commit_is_atomic_idempotent_and_rejects_stale_rollups(isolated_store):
    seed()
    finding = db.get_finding("tenant-a", "case-1")
    event = rv.new_realization("tenant-a", finding, recovery_basis="cash_payment",
                               realized_value=10, external_reference="first")
    assert db.save_recovery_event("tenant-a", event.to_dict(), expected_finding=finding,
                                 finding_fields={"recovered_amount": 10})
    assert not db.save_recovery_event("tenant-a", event.to_dict(), expected_finding=finding)
    second = rv.new_realization("tenant-a", finding, recovery_basis="cash_payment",
                                realized_value=20, external_reference="second")
    before = copy.deepcopy(isolated_store.data)
    with pytest.raises(db.RecoveryConflict):
        db.save_recovery_event("tenant-a", second.to_dict(), expected_finding=finding,
                               finding_fields={"recovered_amount": 20})
    assert isolated_store.data == before
    assert db.get_finding("tenant-a", "case-1")["recovered_amount"] == 10
    assert len(db.get_recovery_events("tenant-a")) == 1


@pytest.mark.parametrize("filename", ["agreement.pdf", "replacement-new-name.pdf"])
def test_verified_replacement_requires_explicit_resolution(isolated_store, filename):
    seed()
    add_hold()
    db.save_contract("tenant-a", {
        "customer_id": "redwood", "customer_name": "Redwood",
        "document_id": "replacement", "file_name": filename,
        "structural_verification": {"state": "Verified", "pages": 1},
    })
    with pytest.raises(LowConfidenceGateException):
        db.transition_finding_status("tenant-a", "case-1", "invoiced", "test")
    resolved = db.resolve_document_hold("tenant-a", "held-document", "replacement", "reviewer")
    assert resolved["resolved_by"] == "reviewer"
    assert assurance.account_status("tenant-a")["verification_queue"] == []
    assert db.get_finding("tenant-a", "case-1")["verification_state"] == "Verified"
    assert not db.get_all_contracts("tenant-a")[0]["confirmed"]
    assert db.transition_finding_status("tenant-a", "case-1", "invoiced", "test")["status"] == "invoiced"


def test_resolving_one_hold_preserves_other_holds_and_tenants(isolated_store):
    seed()
    seed("tenant-b")
    add_hold("first")
    add_hold("second")
    db.save_contract("tenant-a", {
        "customer_id": "redwood", "document_id": "replacement", "file_name": "new.pdf",
        "structural_verification": {"state": "Verified"},
    })
    db.resolve_document_hold("tenant-a", "first", "replacement", "reviewer")
    assert db.get_finding("tenant-a", "case-1")["verification_document_ids"] == ["second"]
    with pytest.raises(LowConfidenceGateException):
        db.transition_finding_status("tenant-a", "case-1", "invoiced", "test")
    assert len(assurance.account_status("tenant-a")["verification_queue"]) == 1
    assert db.get_finding("tenant-b", "case-1").get("verification_state") is None
    db.resolve_document_hold("tenant-a", "second", "replacement", "reviewer")
    assert assurance.account_status("tenant-a")["state"] == "Ready"


@pytest.mark.parametrize("replacement", ["missing", "foreign", "stale", "wrong-customer"])
def test_resolution_rejects_unverified_or_unassociated_replacement(isolated_store, replacement):
    seed()
    db.save_contract("tenant-a", {"customer_id": "redwood", "document_id": "held-document"})
    add_hold()
    account = "tenant-b" if replacement == "foreign" else "tenant-a"
    if replacement != "missing":
        db.save_contract(account, {
            "customer_id": "other" if replacement == "wrong-customer" else "redwood",
            "document_id": replacement, "file_name": "replacement.pdf",
            "structural_verification": {"state": "Verified"},
        })
    if replacement == "stale":
        db.save_contract("tenant-a", {"customer_id": "redwood", "document_id": "latest"})
    before = copy.deepcopy(isolated_store.data)
    with pytest.raises(db.VerificationResolutionError):
        db.resolve_document_hold("tenant-a", "held-document", replacement, "reviewer")
    assert isolated_store.data == before


def test_native_blank_page_passes_but_missing_text_on_painted_page_does_not(tmp_path, monkeypatch):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    for _ in range(25):
        pdf.drawString(50, 700, "Renewal requires sixty days notice.")
        pdf.showPage()
    pdf.showPage()
    pdf.save()
    path = tmp_path / "native-with-blank.pdf"
    path.write_bytes(buffer.getvalue())
    pages = pdf_text_pages(str(path))
    assert len(pages) == 26
    assert native_pages_complete(str(path), pages, 26)
    assert not native_pages_complete(str(path), pages[:-1], 26)
    failed = [replace(pages[0], text="", blocks=()), *pages[1:]]
    assert not native_pages_complete(str(path), failed, 26)
    assert not native_pages_complete(str(path), [replace(p, text="") for p in pages], 26)
    extract = Mock(return_value=ExtractionResult([], "Redwood", 26, "pdf_text",
                                                 "test", 1, False, DocumentProfile()))
    monkeypatch.setattr("recoup_agent.extraction.extractor.extract_pages", extract)
    monkeypatch.setattr("recoup_agent.extraction.verifier.verify", lambda *args, **kwargs: [])
    result = ingestion_doc.extract_entitlements(str(path))
    assert result.structural_verification["state"] == "Verified"
    assert extract.call_args.args[1] == "pdf_text"
