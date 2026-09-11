from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from recoup_agent import api
from recoup_agent.trueup import build_trueup, render_trueup_pdf


@pytest.fixture(autouse=True)
def sample_mode(monkeypatch):
    monkeypatch.setenv("RECOUP_SAMPLE_MODE", "1")


def _finding(**over):
    base = {
        "customer_id": "acme",
        "customer_name": "Acme Corp",
        "type": "unenforced_minimum",
        "period": "2026-06",
        "monthly_recoverable": 1000.0,
        "status": "approved",
        "clause_text": "Minimum monthly fee of $5,000",
        "math": "$5,000/mo − billed $4,000/mo = $1,000/mo",
        "term": "committed_minimum_monthly",
        "corrective_invoice": {"ref": "INV-9"},
    }
    base.update(over)
    return base


def test_build_trueup_selects_collectible_statuses():
    findings = [
        _finding(period="2026-06"),
        _finding(period="2026-07", status="invoiced"),
        _finding(period="2026-08", status="disputed"),
        _finding(period="2026-09", status="open"),
        _finding(period="2026-10", status="recovered"),
        _finding(period="2026-11", status="rejected"),
        _finding(customer_id="other", customer_name="Other Co", status="approved"),
    ]
    pack = build_trueup("acme", findings, [])
    assert pack is not None
    assert pack["customer_name"] == "Acme Corp"
    assert pack["total"] == 3000.0
    assert pack["periods"] == ["2026-06", "2026-07", "2026-08"]
    assert all(r["status"] in {"approved", "invoiced", "disputed"} for r in pack["rows"])
    assert pack["rows"][0]["invoice_ref"] == "INV-9"


def test_build_trueup_include_open():
    findings = [_finding(status="open")]
    assert build_trueup("acme", findings, [], include_open=False) is None
    pack = build_trueup("acme", findings, [], include_open=True)
    assert pack["total"] == 1000.0


def test_build_trueup_none_when_nothing_qualifies():
    assert build_trueup("acme", [], []) is None
    assert build_trueup("acme", [_finding(status="recovered")], []) is None


def test_letter_contents():
    pack = build_trueup("acme", [_finding(period="2026-06"), _finding(period="2026-07")], [],
                        sender="Odingard Inc")
    letter = pack["letter"]
    assert "accounts payable team at Acme Corp" in letter
    assert "2026-06 and 2026-07" in letter
    assert "$2,000.00" in letter
    assert "15 days" in letter
    assert "Odingard Inc" in letter
    assert "Recoup" not in letter
    pack2 = build_trueup("acme", [_finding()], [])
    assert "[Your company]" in pack2["letter"]


def test_render_trueup_pdf():
    pack = build_trueup("acme", [_finding()], [], sender="Odingard Inc")
    pdf = render_trueup_pdf(pack)
    assert pdf.startswith(b"%PDF")
    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) >= 2
    text = "".join(p.extract_text() or "" for p in reader.pages)
    assert "Schedule 1 of" in text
    assert "accounts payable team at Acme Corp" in text
    assert "Schedule of amounts due" in text
    assert "Recoup" not in text


def test_trueup_endpoints_sample_mode():
    client = TestClient(api.app)
    # pick a customer that has findings in the sample book
    findings = api._offline_findings()
    cid = findings[0]["customer_id"]
    res = client.get(f"/api/trueup/{cid}")
    assert res.status_code == 200
    pack = res.json()
    assert pack["customer_id"] == cid
    assert pack["total"] > 0
    res_pdf = client.get(f"/api/trueup/{cid}.pdf")
    assert res_pdf.status_code == 200
    assert res_pdf.content.startswith(b"%PDF")


def test_trueup_unknown_customer_404():
    res = TestClient(api.app).get("/api/trueup/nonexistent-customer")
    assert res.status_code == 404
