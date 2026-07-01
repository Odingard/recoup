from __future__ import annotations

from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from recoup_agent import api
from recoup_agent.ingestion_doc import ContractEntitlements, Entitlement
from recoup_agent.reconciliation import reconcile


@pytest.fixture(autouse=True)
def sample_mode(monkeypatch):
    monkeypatch.setenv("RECOUP_SAMPLE_MODE", "1")


def _client():
    return TestClient(api.app)


def _blank_pdf_bytes() -> bytes:
    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(buffer)
    return buffer.getvalue()


def test_document_upload_returns_preview_and_does_not_persist(monkeypatch):
    saved = {}

    def fake_extract_entitlements(file_path):
        return ContractEntitlements(
            customer_name="Acme Corp",
            entitlements=[
                Entitlement(
                    term_type="committed_minimum",
                    value=50000,
                    effective_date=None,
                    confidence_score=0.96,
                    provenance="Clause 2.1",
                )
            ],
        )

    monkeypatch.setattr(api, "extract_entitlements", fake_extract_entitlements)
    monkeypatch.setattr(api.db, "save_contract", lambda account_id, payload: saved.setdefault("called", True))

    res = _client().post(
        "/api/ingest/contract/document",
        files={"file": ("contract.txt", b"contract body", "text/plain")},
    )

    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "success"
    assert payload["saved"] is False
    assert payload["contract"]["term_meta"]["committed_minimum_monthly"]["confidence"] == 0.96
    assert saved == {}


@pytest.mark.parametrize(
    "filename,content,message_fragment",
    [
        ("contract.exe", b"binary", "Unsupported file type"),
        ("empty.pdf", b"", "file is empty"),
    ],
)
def test_document_upload_flags_unsupported_and_empty(monkeypatch, filename, content, message_fragment):
    monkeypatch.setattr(api, "extract_entitlements", lambda *_: pytest.fail("extract_entitlements should not run"))
    res = _client().post(
        "/api/ingest/contract/document",
        files={"file": (filename, content, "application/octet-stream")},
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "needs_review"
    assert message_fragment.lower() in payload["message"].lower()


def test_document_upload_flags_corrupt_and_scanned_pdf(monkeypatch):
    monkeypatch.setattr(api, "extract_entitlements", lambda *_: pytest.fail("extract_entitlements should not run"))

    corrupt = _client().post(
        "/api/ingest/contract/document",
        files={"file": ("broken.pdf", b"not-a-pdf", "application/pdf")},
    )
    assert corrupt.status_code == 200
    corrupt_payload = corrupt.json()
    assert corrupt_payload["status"] == "needs_review"
    assert "corrupt" in corrupt_payload["message"].lower() or "unreadable" in corrupt_payload["message"].lower()

    scanned = _client().post(
        "/api/ingest/contract/document",
        files={"file": ("scanned.pdf", _blank_pdf_bytes(), "application/pdf")},
    )
    assert scanned.status_code == 200
    scanned_payload = scanned.json()
    assert scanned_payload["status"] == "needs_review"
    assert "scanned/image pdf" in scanned_payload["message"].lower()


def test_structured_ingest_missing_fields_returns_field_flags():
    res = _client().post("/api/ingest/contract", json={"customer_id": "acme"})
    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "needs_review"
    assert any(field["field"] == "customer_name" for field in payload["fields"])


def test_sample_mode_reconcile_still_works():
    res = _client().post("/api/reconcile", params={"period": "2026-06"})
    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "success"
    assert payload["findings_found"] == 4


def test_rule4_none_guard_produces_needs_review():
    contract = {
        "customer_id": "acme",
        "customer_name": "Acme Corp",
        "committed_minimum_monthly": None,
        "included_units": 10000,
        "overage_rate": 3.5,
        "annual_escalator_pct": 0.04,
        "escalator_effective_date": "2026-05-01",
        "discounts": [],
    }
    usage = {"customer_id": "acme", "period": "2026-06", "units": 10000}
    invoice = {"customer_id": "acme", "period": "2026-06", "base_charge": None, "overage_charge": 0, "discounts_applied": []}
    needs_review: list[dict] = []

    findings = reconcile(contract, usage, invoice, "2026-06", needs_review=needs_review)

    assert findings == []
    assert any(item["term"] == "annual_escalator_pct" for item in needs_review)
