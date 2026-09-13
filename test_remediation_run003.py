"""Run-003 defect remediation regression tests, grouped by D-id."""
import io
import zipfile
from types import SimpleNamespace

import firebase_admin.auth as firebase_auth
import pytest
from fastapi.testclient import TestClient

from recoup_agent import api, db
from recoup_agent.identity import CustomerResolver
from recoup_agent.ingest_csv import IngestError, load_invoices_csv, load_usage_csv
from recoup_agent.money import (is_supported, normalize_currency, quantize,
                                to_cents)
from recoup_agent.reconciliation import reconcile


def _contract(**over):
    c = {
        "customer_id": "acme", "customer_name": "Acme",
        "committed_minimum_monthly": 1000.0,
        "clauses": {"committed_minimum": "Client commits to $1,000/mo minimum.",
                    "escalator": "Fees increase 5% annually.",
                    "seats": "100 committed seats at $10/seat."},
        "term_meta": {},
    }
    c.update(over)
    return c


def _usage(**over):
    u = {"customer_id": "acme", "period": "2026-06", "units": 0}
    u.update(over)
    return u


def _invoice(**over):
    i = {"customer_id": "acme", "period": "2026-06", "base_charge": 500.0,
         "overage_charge": 0.0, "discounts_applied": [], "credits_applied": [],
         "tax_excluded": 0.0, "prorated": False, "proration_amount": 0.0}
    i.update(over)
    return i


# ---------------- D-05: shared money utility ----------------

def test_money_quantize_half_up():
    assert quantize(0.125) == 0.13
    assert quantize(2.675) == 2.68
    assert quantize(1.005) == 1.01
    assert quantize(19.99) == 19.99


def test_money_to_cents():
    assert to_cents(19.99) == 1999
    assert to_cents(0.125) == 13
    assert to_cents(1.005) == 101


def test_money_currency_helpers():
    assert normalize_currency("usd") == "USD"
    assert normalize_currency(" eur ") == "EUR"
    assert normalize_currency("US") is None
    assert normalize_currency(None) is None
    assert is_supported("USD")
    assert not is_supported("EUR")
    assert not is_supported("XYZ")


# ---------------- D-01: customer identity ----------------

def test_d01_exact_and_unique_stripped():
    contracts = [
        {"customer_id": "northpeak_logistics", "customer_name": "NorthPeak Logistics LLC"},
        {"customer_id": "c_acme_north", "customer_name": "Acme North"},
        {"customer_id": "c_acme_ho", "customer_name": "Acme Holdings"},
    ]
    r = CustomerResolver(contracts)
    assert r.resolve("NorthPeak Logistics LLC") == "northpeak_logistics"
    assert r.resolve("northpeak_logistics") == "northpeak_logistics"
    # "Acme N" and "Acme Ho" must never resolve via prefix/first-token tricks.
    assert r.resolve("Acme N") is None
    assert r.resolve("Acme Ho") is None
    assert r.resolve("Acme") is None


def test_d01_ambiguous_message():
    contracts = [
        {"customer_id": "sdg", "customer_name": "Sterling Dental Group"},
        {"customer_id": "sdl", "customer_name": "Sterling Dental LLC"},
    ]
    r = CustomerResolver(contracts)
    assert r.resolve("Sterling Dental") is None
    assert r.explain("Sterling Dental") == \
        "ambiguous: 'Sterling Dental' matches sdg, sdl"


def test_d01_ambiguous_csv_no_assignment(tmp_path):
    contracts = [
        {"customer_id": "sdg", "customer_name": "Sterling Dental Group"},
        {"customer_id": "sdl", "customer_name": "Sterling Dental LLC"},
    ]
    r = CustomerResolver(contracts)
    f = tmp_path / "usage.csv"
    f.write_text("account,month,metric,qty\nsterling dental,2026-06,seats,5\n")
    usage, nr = load_usage_csv(f, r)
    assert usage == []
    assert len(nr) == 1 and nr[0]["term"] == "customer_identity"
    assert nr[0]["customer_id"] is None


# ---------------- D-02: currency fail closed ----------------

def test_d02_unsupported_invoice_currency():
    nr = []
    findings = reconcile(_contract(), _usage(), _invoice(currency="EUR"),
                         "2026-06", nr)
    assert findings == []
    assert any(e["term"] == "currency" for e in nr)


def test_d02_mixed_currency_flag():
    nr = []
    findings = reconcile(_contract(), _usage(),
                         _invoice(currency="USD", currency_mixed=True),
                         "2026-06", nr)
    assert findings == []
    assert any(e["term"] == "currency" for e in nr)


def test_d02_mismatched_contract_invoice_currency():
    nr = []
    c = _contract(currency="USD")
    findings = reconcile(c, _usage(), _invoice(currency="EUR"), "2026-06", nr)
    assert findings == []
    assert any(e["term"] == "currency" for e in nr)


def test_d02_usd_finding_carries_currency():
    nr = []
    findings = reconcile(_contract(), _usage(), _invoice(), "2026-06", nr)
    assert findings and all(f["currency"] == "USD" for f in findings)


def _spec(over=None):
    from recoup_agent.rights_discovery.models import RightSpec
    spec = {
        "spec_version": 1, "right_id": "r1",
        "right_family": "volume_rebate",
        "holder_party_id": "Buyer", "obligor_party_id": "Supplier",
        "trigger": {"op": "gt", "observation": "units_purchased",
                    "value": {"constant": "commitment"}},
        "calculation": {"type": "per_unit",
                        "rate": {"constant": "rebate_rate"},
                        "quantity_observation": "units_purchased",
                        "above": {"constant": "commitment"}},
        "required_observations": ["units_purchased"],
        "contractual_constants": [
            {"name": "commitment", "value": 100000, "kind": "quantity"},
            {"name": "rebate_rate", "value": 2.25, "kind": "rate"}],
        "currency": "USD", "effective_from": "2026-01-01",
    }
    spec.update(over or {})
    return RightSpec.from_dict(spec)


def test_d02_runtime_currency_mismatch():
    from recoup_agent.rights_discovery.runtime import evaluate_right
    obs = [{"observation_type": "units_purchased", "period": "2026-06",
            "value": 120000, "currency": "EUR"}]
    result = evaluate_right(_spec(), obs, "2026-06")
    assert result.status == "not_evaluable"
    assert result.calculation_trace["reason"] == "currency_mismatch"


def test_d11_runtime_negative_quantity():
    from recoup_agent.rights_discovery.runtime import evaluate_right
    obs = [{"observation_type": "units_purchased", "period": "2026-06",
            "value": -5}]
    result = evaluate_right(_spec(), obs, "2026-06")
    assert result.status == "not_evaluable"
    assert result.calculation_trace["reason"] == "negative_quantity"


def test_d02_compiler_rejects_foreign_currency_quote():
    from recoup_agent.rights_discovery.compiler import (CompileFailure,
                                                        compile_candidate_right)
    from recoup_agent.rights_discovery.models import CandidateFinancialRight
    quote = "Client pays a minimum of EUR 500 per month, invoiced monthly."
    candidate = CandidateFinancialRight(
        candidate_id="c1", account_id="acct", source_id="src",
        holder_party_id="Client", obligor_party_id="Vendor",
        right_name="credit", right_family="service_level_credit",
        trigger_spec={"op": "event_exists", "observation": "invoice_issued"},
        calculation_spec={"type": "fixed_amount",
                          "amount": {"constant": "amount"}},
        required_observations=["invoice_issued"],
        source_quote=quote,
        status="verified",
        metadata={"constants": [
            {"name": "amount", "value": "EUR 500", "kind": "amount"}],
            "actual_observation": "invoice_amount"},
    )
    out = compile_candidate_right(candidate, quote)
    assert isinstance(out, CompileFailure)
    assert out.status in ("needs_review", "unsupported")


# ---------------- D-03: contract date boundaries ----------------

def test_d03_period_before_effective_date():
    nr = []
    c = _contract(effective_date="2026-07-01", term_start="2026-07-01")
    findings = reconcile(c, _usage(), _invoice(), "2026-06", nr)
    assert findings == []
    assert any(e["term"] == "effective_date" for e in nr)


def test_d03_post_term_non_renewing():
    nr = []
    c = _contract(term_end="2026-05-31", auto_renew_months=0)
    findings = reconcile(c, _usage(), _invoice(), "2026-06", nr)
    assert findings == []
    assert any(e["term"] == "term_end" for e in nr)


def test_d03_post_term_auto_renew_still_evaluates():
    nr = []
    c = _contract(term_end="2026-05-31", auto_renew_months=12)
    findings = reconcile(c, _usage(), _invoice(), "2026-06", nr)
    assert findings  # minimum check still runs


def test_d03_credits_surfaced_on_early_return():
    nr = []
    c = _contract(effective_date="2026-07-01")
    inv = _invoice(currency="EUR",
                   credits_applied=[{"description": "refund", "amount": 50.0}])
    findings = reconcile(c, _usage(), inv, "2026-06", nr)
    assert findings == []
    assert any(e["term"] == "credits_applied" for e in nr)
    assert any(e["term"] == "currency" for e in nr)


# ---------------- D-11: negative quantities fail closed ----------------

def test_d11_negative_usage_units_reconcile():
    nr = []
    c = _contract(included_units=100, overage_rate=0.10,
                  clauses={**_contract()["clauses"], "overage": "overage $0.10/unit"})
    findings = reconcile(c, _usage(units=-5), _invoice(base_charge=1000.0),
                         "2026-06", nr)
    assert not any(f["type"] == "unbilled_overage" for f in findings)
    assert any("negative" in e["reason"] for e in nr)


def test_d11_negative_usage_units_csv(tmp_path):
    r = CustomerResolver([{"customer_id": "acme", "customer_name": "Acme"}])
    f = tmp_path / "usage.csv"
    f.write_text("account,month,qty\nacme,2026-06,-5\nacme,2026-06,10\n")
    usage, nr = load_usage_csv(f, r)
    assert usage and usage[0]["units"] == 10
    assert any(e["term"] == "usage_units" for e in nr)


def test_d11_negative_seat_units_csv(tmp_path):
    r = CustomerResolver([{"customer_id": "acme", "customer_name": "Acme"}])
    f = tmp_path / "inv.csv"
    f.write_text("customer,period,amount,description,units\n"
                 "acme,2026-06,500,Base plan seats,-3\n")
    invoices, nr = load_invoices_csv(f, r)
    assert invoices and "seat_units" not in invoices[0]
    assert any(e["term"] == "seat_units" for e in nr)


def test_d11_negative_committed_seats():
    nr = []
    c = _contract(committed_seats=-5, seat_price=10.0)
    findings = reconcile(c, _usage(), _invoice(), "2026-06", nr)
    assert not any(f["type"] == "underbilled_seats" for f in findings)
    assert any(e["term"] == "committed_seats" and "negative" in e["reason"]
               for e in nr)


def test_d11_negative_billed_seats():
    nr = []
    c = _contract(committed_seats=100, seat_price=10.0)
    findings = reconcile(c, _usage(), _invoice(seat_units=-4), "2026-06", nr)
    assert not any(f["type"] == "underbilled_seats" for f in findings)
    assert any(e["term"] == "seat_units" for e in nr)


def test_d11_api_rejects_negative_units(monkeypatch):
    monkeypatch.setenv("RECOUP_SAMPLE_MODE", "1")
    client = TestClient(api.app)
    # Pydantic ge=0 fails validation; the app's envelope returns 200 + a
    # needs_review payload flagging the offending field.
    resp = client.post("/api/ingest/usage",
                       json={"customer_id": "acme", "period": "2026-06",
                             "units": -5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "needs_review"
    assert any(f["field"] == "units" for f in body["fields"])
    resp = client.post("/api/ingest/invoice",
                       json={"customer_id": "acme", "period": "2026-06",
                             "base_charge": -10.0})
    assert resp.json()["status"] == "needs_review"


# ---------------- D-14: credits/refunds always surfaced ----------------

def test_d14_credits_surfaced_without_findings():
    nr = []
    # base equals minimum -> no findings, but credits must still be flagged.
    inv = _invoice(base_charge=1000.0,
                   credits_applied=[{"description": "refund", "amount": 75.0}])
    findings = reconcile(_contract(), _usage(), inv, "2026-06", nr)
    assert findings == []
    entries = [e for e in nr if e["term"] == "credits_applied"]
    assert len(entries) == 1
    assert entries[0]["amount"] == 75.0


def test_d14_credits_never_netted():
    nr = []
    inv = _invoice(base_charge=500.0,
                   credits_applied=[{"description": "credit", "amount": 300.0}])
    findings = reconcile(_contract(), _usage(), inv, "2026-06", nr)
    # unenforced_minimum amount must be 1000-500 = 500, untouched by the credit.
    assert any(f["monthly_recoverable"] == 500.0 for f in findings)
    assert any(e["term"] == "credits_applied" for e in nr)


# ---------------- D-15: escalator precision ----------------

def _esc_contract(date_str):
    return _contract(
        annual_escalator_pct=0.05, escalator_effective_date=date_str,
        clauses={**_contract()["clauses"],
                 "committed_minimum": "minimum $1,000/mo",
                 "escalator": "5% annual escalator"})


def test_d15_mid_period_anniversary_reviews_no_finding():
    nr = []
    c = _esc_contract("2025-03-15")
    findings = reconcile(c, _usage(), _invoice(base_charge=1000.0, period="2026-03"),
                         "2026-03", nr)
    assert not any(f["type"] == "missed_escalator" for f in findings)
    assert any(e["term"] == "escalator_effective_date" for e in nr)


def test_d15_next_month_one_step():
    nr = []
    c = _esc_contract("2025-03-15")
    findings = reconcile(c, _usage(), _invoice(base_charge=1000.0, period="2026-04"),
                         "2026-04", nr)
    esc = [f for f in findings if f["type"] == "missed_escalator"]
    assert len(esc) == 1
    assert esc[0]["escalator_steps"] == 1
    assert esc[0]["monthly_recoverable"] == 50.0


def test_d15_two_years_two_steps():
    nr = []
    c = _esc_contract("2025-03-15")
    findings = reconcile(c, _usage(), _invoice(base_charge=1000.0, period="2027-04"),
                         "2027-04", nr)
    esc = [f for f in findings if f["type"] == "missed_escalator"]
    assert len(esc) == 1 and esc[0]["escalator_steps"] == 2
    assert esc[0]["monthly_recoverable"] == quantize(1000 * 1.05 ** 2 - 1000)


def test_d15_day_one_escalator_unchanged():
    nr = []
    c = _esc_contract("2025-03-01")
    findings = reconcile(c, _usage(), _invoice(base_charge=1000.0, period="2026-04"),
                         "2026-04", nr)
    esc = [f for f in findings if f["type"] == "missed_escalator"]
    # Legacy day-01 formula: two anniversaries (2025-03-01, 2026-03-01) elapsed.
    assert len(esc) == 1 and esc[0]["escalator_steps"] == 2
    assert not any(e["term"] == "escalator_effective_date" for e in nr)


def test_d15_book_loader_preserves_explicit_date():
    from recoup_agent.book_loader import normalize_contract
    raw = {"customer_id": "acme", "customer_name": "Acme",
           "annual_escalator_pct": 0.05,
           "escalator_effective_date": "2025-03-15"}
    assert normalize_contract(raw)["escalator_effective_date"] == "2025-03-15"
    raw2 = {"customer_id": "acme", "customer_name": "Acme",
            "annual_escalator_pct": 0.05,
            "escalator_effective_month": "2025-03"}
    assert normalize_contract(raw2)["escalator_effective_date"] == "2025-03-01"


# ---------------- D-04/D-07: transactional lifecycle + uniform 404 ----------------

class _Snap:
    def __init__(self, data):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return dict(self._data) if self._data else None


class _DocRef:
    def __init__(self, store, coll, doc_id):
        self._store, self._coll, self._id = store, coll, doc_id

    def get(self, transaction=None):
        return _Snap(self._store[self._coll].get(self._id))

    def set(self, data, merge=False):
        if merge and self._id in self._store[self._coll]:
            self._store[self._coll][self._id].update(data)
        else:
            self._store[self._coll][self._id] = dict(data)

    def update(self, fields):
        self._store[self._coll][self._id].update(fields)


class _Coll:
    def __init__(self, store, coll):
        self._store, self._coll = store, coll
        self._n = 0

    def document(self, doc_id=None):
        if doc_id is None:
            self._n += 1
            doc_id = f"auto{self._n}"
        return _DocRef(self._store, self._coll, doc_id)


class _Txn:
    """Minimal transaction facade matching what firestore.transactional needs."""
    _read_only = False
    _max_attempts = 5

    def __init__(self, store=None):
        self._store = store
        self._id = None

    def _clean_up(self):
        pass

    def _begin(self, retry_id=None):
        self._id = retry_id or "txn-1"

    def _commit(self):
        pass

    def _rollback(self):
        pass

    def update(self, ref, fields):
        ref.update(fields)

    def set(self, ref, data):
        ref.set(data)


class _FakeDbRoot:
    """Routes accounts/{acct}.collection(name).document(id) into an
    account-scoped store dict."""

    def __init__(self, store):
        self._store = store

    def collection(self, name):
        return _Coll(self._store, name)


class _FakeClient:
    """In-memory stand-in keyed accounts/{acct}/{coll}/{doc}."""

    def __init__(self):
        self.accounts: dict[str, dict] = {}

    def seed(self, acct, coll, doc_id, data):
        store = self.accounts.setdefault(acct, {"findings": {}, "audit_log": {}})
        store[coll][doc_id] = data

    def store(self, acct):
        return self.accounts.setdefault(acct, {"findings": {}, "audit_log": {}})

    def collection(self, name):
        return self

    def document(self, acct):
        return _FakeDbRoot(self.store(acct))

    def transaction(self):
        return _Txn(None)

    def batch(self):
        return _FakeBatch()


class _FakeBatch:
    def set(self, ref, data, merge=False):
        ref.set(data, merge=merge)

    def commit(self):
        pass


def _fake_client():
    return _FakeClient()


def test_d04_transition_happy_path(monkeypatch):
    fake = _fake_client()
    fake.seed("acct1", "findings", "f1", {"status": "open"})
    monkeypatch.setattr(db, "get_client", lambda: fake)
    out = db.transition_finding_status("acct1", "f1", "approved", "ui_approval")
    assert out["status"] == "approved"
    audit = list(fake.store("acct1")["audit_log"].values())
    assert len(audit) == 1 and audit[0]["decision"] == "approved"


def test_d07_concurrent_approval_one_409(monkeypatch):
    fake = _fake_client()
    fake.seed("acct1", "findings", "f1", {"status": "open"})
    monkeypatch.setattr(db, "get_client", lambda: fake)
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    monkeypatch.setattr(firebase_auth, "verify_id_token",
                        lambda t, **kw: {"uid": "u1", "email": "e@x",
                                         "account_id": "acct1"})
    client = TestClient(api.app)
    r1 = client.post("/api/findings/f1/approve",
                     headers={"Authorization": "Bearer x"})
    r2 = client.post("/api/findings/f1/approve",
                     headers={"Authorization": "Bearer x"})
    assert sorted([r1.status_code, r2.status_code]) == [200, 409]


def test_d07_uniform_404(monkeypatch):
    fake = _fake_client()
    fake.seed("acct1", "findings", "f1", {"status": "open"})
    monkeypatch.setattr(db, "get_client", lambda: fake)
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    monkeypatch.setattr(firebase_auth, "verify_id_token",
                        lambda t, **kw: {"uid": "u1", "email": "e@x",
                                         "account_id": "acct1"})
    client = TestClient(api.app)
    r_missing = client.post("/api/findings/ghost/approve",
                            headers={"Authorization": "Bearer x"})
    # cross-tenant: acct1's f1 does not exist under account "other"
    monkeypatch.setattr(firebase_auth, "verify_id_token",
                        lambda t, **kw: {"uid": "u2", "email": "f@x",
                                         "account_id": "other"})
    r_foreign = client.post("/api/findings/f1/approve",
                            headers={"Authorization": "Bearer x"})
    assert r_missing.status_code == 404
    assert r_missing.json()["detail"] == "Finding not found."
    assert r_foreign.status_code == 404
    assert r_foreign.json()["detail"] == "Finding not found."


def test_d07_illegal_transition_raises(monkeypatch):
    fake = _fake_client()
    fake.seed("acct1", "findings", "f1", {"status": "rejected"})
    monkeypatch.setattr(db, "get_client", lambda: fake)
    with pytest.raises(db.IllegalTransition) as exc:
        db.transition_finding_status("acct1", "f1", "approved", "ev")
    assert "Illegal transition: rejected -> approved" in str(exc.value)


def test_d07_missing_finding_raises_not_found(monkeypatch):
    fake = _fake_client()
    monkeypatch.setattr(db, "get_client", lambda: fake)
    with pytest.raises(db.FindingNotFound):
        db.transition_finding_status("acct1", "ghost", "approved", "ev")


# ---------------- D-06: save_findings preserves workflow state ----------------

def test_d06_save_findings_preserves_workflow(monkeypatch):
    fake = _fake_client()
    fake.seed("acct1", "findings", "f1", {
        "status": "approved", "created_at": "2026-01-01T00:00:00",
        "corrective_invoice": {"ref": "in_1"}, "detail": "old",
    })
    monkeypatch.setattr(db, "get_client", lambda: fake)
    db.save_findings("acct1", [{
        "finding_id": "f1", "customer_id": "acme", "customer_name": "Acme",
        "type": "unenforced_minimum", "period": "2026-06", "title": "t",
        "detail": "new detail", "monthly_recoverable": 500.0,
        "status": "open",
    }])
    doc = fake.store("acct1")["findings"]["f1"]
    assert doc["status"] == "approved"
    assert doc["created_at"] == "2026-01-01T00:00:00"
    assert doc["corrective_invoice"] == {"ref": "in_1"}
    assert doc["detail"] == "new detail"

    db.save_findings("acct1", [{
        "finding_id": "f2", "customer_id": "acme", "customer_name": "Acme",
        "type": "unbilled_overage", "period": "2026-06", "title": "t2",
        "detail": "d", "monthly_recoverable": 10.0,
    }])
    assert fake.store("acct1")["findings"]["f2"]["status"] == "open"


# ---------------- D-19: sample-mode approvals ----------------

def test_d19_sample_approve_not_persisted(monkeypatch):
    monkeypatch.setenv("RECOUP_SAMPLE_MODE", "1")
    monkeypatch.setattr(api, "_offline_findings",
                        lambda: [{"finding_id": "f1"}])
    client = TestClient(api.app)
    resp = client.post("/api/findings/f1/approve")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "not_persisted"
    assert body["mode"] == "sample"
    assert body["finding_id"] == "f1"
    assert "not recorded" in body["message"]


def test_d19_sample_unknown_finding_404(monkeypatch):
    monkeypatch.setenv("RECOUP_SAMPLE_MODE", "1")
    monkeypatch.setattr(api, "_offline_findings", lambda: [{"finding_id": "f1"}])
    client = TestClient(api.app)
    resp = client.post("/api/findings/ghost/approve")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Finding not found."


# ---------------- D-08: Stripe OAuth unconfigured ----------------

def test_d08_oauth_unconfigured_503(monkeypatch):
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    for var in ("RECOUP_STRIPE_APP_CLIENT_ID", "RECOUP_STRIPE_APP_STATE_SECRET",
                "RECOUP_STRIPE_APP_SECRET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    monkeypatch.setattr(firebase_auth, "verify_id_token",
                        lambda t, **kw: {"uid": "u1", "email": "e@x",
                                         "account_id": "acct1"})
    client = TestClient(api.app)
    resp = client.post("/api/connector/stripe/oauth/start",
                       headers={"Authorization": "Bearer x"})
    assert resp.status_code == 503
    assert resp.json() == {
        "status": "needs_config",
        "message": "Stripe OAuth is not configured for this deployment."}


# ---------------- D-12: CSV ingestion edges ----------------

def test_d12_empty_csvs_raise(tmp_path):
    r = CustomerResolver([{"customer_id": "acme", "customer_name": "Acme"}])
    cases = {
        "empty.csv": "",
        "header.csv": "customer,period,amount\n",
        "ws.csv": "  \n\t\n",
        "bom.csv": "﻿",
    }
    for name, text in cases.items():
        f = tmp_path / name
        f.write_text(text)
        with pytest.raises(IngestError):
            load_invoices_csv(f, r)


def test_d12_empty_csv_via_ingest_files(tmp_path):
    from recoup_agent.ingest_bulk import ingest_files
    result = ingest_files([("empty.csv", b"")], [],
                          lambda p: None)
    assert not result.invoices and not result.usage
    assert any(e["term"] in ("csv", "csv_classification") or
               "unable to read" in e["reason"] or "no data rows" in e["reason"]
               for e in result.needs_review)


# ---------------- D-17: ZIP member safety ----------------

def _zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files:
            zf.writestr(name, data)
    return buf.getvalue()


def test_d17_member_header_over_limit(monkeypatch):
    from recoup_agent import ingest_bulk
    monkeypatch.setattr(ingest_bulk, "MAX_ZIP_MEMBER_BYTES", 8)
    data = _zip_bytes([("big.csv", b"x" * 100)])
    items, err = ingest_bulk.expand_zip("a.zip", data)
    assert items == [] and "exceeds" in err


def test_d17_member_actual_over_limit(monkeypatch):
    """A member whose header claims a small size but whose stream yields more
    than the cap must be rejected after read."""
    from recoup_agent import ingest_bulk
    monkeypatch.setattr(ingest_bulk, "MAX_ZIP_MEMBER_BYTES", 8)

    info = SimpleNamespace(filename="evil.csv", file_size=4, is_dir=lambda: False)

    class _Fh:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b"x" * n

    class _Zf:
        def infolist(self):
            return [info]

        def open(self, _info):
            return _Fh()

    orig = zipfile.ZipFile
    monkeypatch.setattr(ingest_bulk.zipfile, "ZipFile", lambda *a, **k: _Zf())
    items, err = ingest_bulk.expand_zip("a.zip", b"fake")
    monkeypatch.setattr(ingest_bulk.zipfile, "ZipFile", orig)
    assert items == [] and "exceeds" in err
