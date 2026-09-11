from __future__ import annotations

import csv
import io
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from recoup_agent import api
from recoup_agent.identity import CustomerResolver
from recoup_agent.ingest_csv import load_invoices_csv, load_usage_csv, resolve_columns
from recoup_agent.templates import TEMPLATES


@pytest.fixture(autouse=True)
def sample_mode(monkeypatch):
    monkeypatch.setenv("RECOUP_SAMPLE_MODE", "1")


SYSTEMS = ["quickbooks", "xero", "stripe"]
KINDS = ["invoices", "usage"]


@pytest.mark.parametrize("system", SYSTEMS)
@pytest.mark.parametrize("kind", KINDS)
def test_template_download(system, kind):
    res = TestClient(api.app).get(f"/api/templates/{system}/{kind}.csv")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "attachment" in res.headers["content-disposition"]


def test_template_404():
    res = TestClient(api.app).get("/api/templates/netsuite/invoices.csv")
    assert res.status_code == 404


@pytest.mark.parametrize("system", SYSTEMS)
@pytest.mark.parametrize("kind", KINDS)
def test_template_headers_resolve(system, kind):
    spec = TEMPLATES[system][kind]
    normalized = [_h for _h in spec["header"]]
    if kind == "invoices":
        cols = resolve_columns(normalized, required=["customer", "amount"],
                               optional=["invoice_id", "period_start", "description"], where="template")
        assert cols["amount"] is not None
    else:
        cols = resolve_columns(normalized, required=["customer", "units"],
                               optional=["period", "period_start", "metric"], where="template")


@pytest.mark.parametrize("system", SYSTEMS)
@pytest.mark.parametrize("kind", KINDS)
def test_template_rows_round_trip(system, kind):
    """Each template's example rows must load through the real CSV loaders."""
    spec = TEMPLATES[system][kind]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(spec["header"])
    writer.writerows(spec["rows"])

    existing = [{"customer_id": "acme", "customer_name": "Acme Corp"}]
    resolver = CustomerResolver(existing)
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as tmp:
        tmp.write(buf.getvalue())
        path = Path(tmp.name)
    try:
        if kind == "invoices":
            rows, _ = load_invoices_csv(path, resolver)
        else:
            rows, _ = load_usage_csv(path, resolver)
    finally:
        path.unlink(missing_ok=True)
    assert rows, f"{system}/{kind} template produced no rows"
    assert all(r["customer_id"] == "acme" for r in rows)
