from datetime import datetime, timezone

from fastapi.testclient import TestClient

from recoup_agent import success_fee
from recoup_agent.billing import recoup_billing


def _finding(fid, amount, status, recovered_at=None):
    f = {"finding_id": fid, "monthly_recoverable": amount, "status": status}
    if recovered_at:
        f["recovered_at"] = recovered_at
    return f


def test_success_fee_only_on_recovered():
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)
    findings = [
        _finding("a", 1000, "proposed"),
        _finding("b", 2000, "approved"),
        _finding("c", 5000, "recovered", "2026-07-02T00:00:00+00:00"),
        _finding("d", 3000, "recovered", "2026-06-02T00:00:00+00:00"),
        _finding("e", 9999, "rejected"),
    ]
    m = success_fee.compute_metrics(findings, now=now)

    assert m["success_fee_pct"] == 0.20
    assert m["recovered_to_date"] == 8000
    assert m["recovered_this_month"] == 5000  # only July recovery counts this month
    assert m["success_fee_this_month"] == 1000.0  # 20% of 5000
    assert m["success_fee_to_date"] == 1600.0  # 20% of 8000
    # potential excludes rejected only
    assert m["potential_monthly_recoverable"] == 1000 + 2000 + 5000 + 3000


def test_recoup_billing_needs_config_without_any_key(monkeypatch):
    for name in ("RECOUP_BILLING_STRIPE_API_KEY", "STRIPE_API_KEY", "STRIPE", "RECOUP_CONNECTOR_TEST_STRIPE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert recoup_billing.is_configured() is False
    result = recoup_billing.create_success_fee_invoice(
        customer_email="ops@example.com", amount_dollars=100.0, current_month="2026-07"
    )
    assert result["status"] == "needs_config"


def test_recoup_billing_uses_only_dedicated_key(monkeypatch):
    monkeypatch.setenv("RECOUP_BILLING_STRIPE_API_KEY", "RECOUP_BILLING_STRIPE_API_KEY=sk_test_dedicated_example")
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_customer_readonly")
    monkeypatch.setenv("STRIPE", "sk_test_customer_readonly_legacy")
    assert recoup_billing._billing_key() == "sk_test_dedicated_example"
    assert recoup_billing.is_configured() is True


def _sample_client(monkeypatch):
    monkeypatch.setenv("RECOUP_SAMPLE_MODE", "1")
    from recoup_agent import api

    return TestClient(api.app)


def test_metrics_and_export_sample_mode(monkeypatch):
    client = _sample_client(monkeypatch)

    metrics = client.get("/api/metrics")
    assert metrics.status_code == 200
    body = metrics.json()
    assert body["success_fee_pct"] == 0.20
    assert body["recovered_to_date"] == 0  # nothing recovered in the offline book

    export = client.get("/api/findings/export")
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/csv")
    assert "finding_id" in export.text


def test_charge_success_fee_sample_mode_needs_config(monkeypatch):
    monkeypatch.delenv("RECOUP_BILLING_STRIPE_API_KEY", raising=False)
    client = _sample_client(monkeypatch)
    res = client.post("/api/billing/charge-success-fee")
    assert res.status_code == 200
    body = res.json()
    assert "metrics" in body and "billing" in body
    # No recovered dollars in sample mode -> skipped (or needs_config if key absent)
    assert body["billing"]["status"] in {"skipped", "needs_config"}
