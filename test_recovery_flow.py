import pytest
from fastapi.testclient import TestClient

from recoup_agent import api
from recoup_agent.recovery import assert_transition


def test_transition_rules():
    assert_transition("approved", "invoiced")
    assert_transition("approved", "recovered")
    assert_transition("invoiced", "recovered")
    assert_transition("invoiced", "disputed")
    assert_transition("disputed", "recovered")
    assert_transition("disputed", "written_off")
    assert_transition("invoiced", "written_off")
    assert_transition("approved", "written_off")
    for bad in [("open", "recovered"), ("open", "invoiced"), ("recovered", "disputed"),
                ("written_off", "recovered"), ("rejected", "approved"), ("invoiced", "approved")]:
        with pytest.raises(ValueError):
            assert_transition(*bad)


def _client(monkeypatch):
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    return TestClient(api.app)


def test_recovered_requires_payment_evidence(monkeypatch):
    client = _client(monkeypatch)
    headers = {"X-Recoup-Sample": "1"}
    findings = client.get("/api/findings", headers=headers).json()
    fid = findings[0]["finding_id"]

    resp = client.post(f"/api/findings/{fid}/recovered", headers=headers)
    assert resp.status_code == 422

    resp = client.post(f"/api/findings/{fid}/recovered", headers=headers,
                       json={"paid_amount": 0})
    assert resp.status_code == 422

    resp = client.post(f"/api/findings/{fid}/recovered", headers=headers,
                       json={"paid_amount": 3200.0, "payment_ref": "pmt-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "recovered"
    assert body["recovered_amount"] == 3200.0
    assert body["payment"]["ref"] == "pmt-1"


def test_invoiced_requires_invoice_ref(monkeypatch):
    client = _client(monkeypatch)
    headers = {"X-Recoup-Sample": "1"}
    findings = client.get("/api/findings", headers=headers).json()
    fid = findings[0]["finding_id"]

    resp = client.post(f"/api/findings/{fid}/invoiced", headers=headers,
                       json={"invoice_ref": "  ", "invoice_amount": 3200.0})
    assert resp.status_code == 422

    resp = client.post(f"/api/findings/{fid}/invoiced", headers=headers,
                       json={"invoice_ref": "INV-1042", "invoice_amount": 3200.0,
                             "invoice_date": "2026-07-15"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "invoiced"
    assert body["corrective_invoice"]["ref"] == "INV-1042"


def test_disputed_and_written_off_shapes(monkeypatch):
    client = _client(monkeypatch)
    headers = {"X-Recoup-Sample": "1"}
    findings = client.get("/api/findings", headers=headers).json()
    fid = findings[0]["finding_id"]

    resp = client.post(f"/api/findings/{fid}/disputed", headers=headers, json={"reason": "customer disputes"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "disputed"

    resp = client.post(f"/api/findings/{fid}/written-off", headers=headers, json={"reason": "uncollectible"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "written_off"
