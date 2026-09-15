"""Category-4 pilot defects: seat quantity from invoice line text, and the
focused recall pass for missing right families."""
from types import SimpleNamespace

from recoup_agent.extraction.extractor import extract_pages
from recoup_agent.extraction.pages import Page
from recoup_agent.identity import CustomerResolver
from recoup_agent.ingest_csv import load_invoices_csv
from recoup_agent.reconciliation import reconcile

_RESOLVER = CustomerResolver([
    {"customer_id": "redwood_field_services",
     "customer_name": "Redwood Field Services, LLC"},
])


def _billing(tmp_path, rows, header="invoice date,customer,line description,amount"):
    f = tmp_path / "billing.csv"
    f.write_text(header + "\n" + "\n".join(rows) + "\n")
    return load_invoices_csv(f, _RESOLVER)


def test_seat_quantity_parsed_from_line_description(tmp_path):
    invoices, _nr = _billing(tmp_path, [
        "2026-06-01,Redwood Field Services LLC,Enterprise license - 220 seats @ $65,14300",
        "2026-07-01,Redwood Field Services LLC,Enterprise license - 220 seats @ $65,14300",
    ])
    assert len(invoices) == 2
    for inv in invoices:
        assert inv["seat_units"] == 220.0
        assert inv["base_charge"] == 14300.0


def test_units_column_wins_over_description(tmp_path):
    invoices, _nr = _billing(
        tmp_path,
        ["2026-06-01,Redwood Field Services LLC,Enterprise license - 220 seats @ $65,14300,225"],
        header="invoice date,customer,line description,amount,units")
    assert invoices[0]["seat_units"] == 225.0


def test_seat_line_without_quantity_stays_unset(tmp_path):
    invoices, _nr = _billing(tmp_path, [
        "2026-06-01,Redwood Field Services LLC,Enterprise license - monthly,14300"])
    assert "seat_units" not in invoices[0]


def test_underbilled_seats_finding_from_parsed_quantity():
    contract = {
        "customer_id": "redwood_field_services",
        "customer_name": "Redwood Field Services, LLC",
        "committed_seats": 240, "seat_price": 65.0,
        "term_start": "2026-01-01", "term_end": "2026-12-31",
        "confirmed": True,
        "term_meta": {},
        "clauses": {"seats": "Customer commits to 240 seats at $65 per seat."},
    }
    invoice = {"customer_id": "redwood_field_services", "period": "2026-06",
               "seat_units": 220.0, "base_charge": 14300.0,
               "amount_billed": 14300.0}
    usage = {"customer_id": "redwood_field_services", "period": "2026-06",
             "units": 240.0}
    needs_review = []
    findings = reconcile(contract, usage, invoice, "2026-06",
                         needs_review=needs_review)
    seat_findings = [f for f in findings if f["type"] == "underbilled_seats"]
    assert len(seat_findings) == 1
    f = seat_findings[0]
    assert f["monthly_recoverable"] == 1300.0
    assert f["expected_value"] == 240 * 65.0
    assert f["actual_value"] == 220 * 65.0
    assert not any(g["type"] in ("unbilled_overage", "overage")
                 for g in findings)


# --- Focused recall pass --------------------------------------------------

_UNFOCUSED = (
    '{"customer_name":"Redwood Field Services, LLC","entitlements":['
    '{"term_type":"committed_seats","value":240,"effective_date":"2026-01-01","confidence_score":0.95,"provenance":"240 seats","page":1},'
    '{"term_type":"seat_price","value":65,"effective_date":"2026-01-01","confidence_score":0.95,"provenance":"$65 per seat","page":1},'
    '{"term_type":"term_start","value":0,"effective_date":"2026-01-01","confidence_score":0.9,"provenance":"Initial Term","page":1}]}')

_TERM_FAMILY = (
    '{"customer_name":null,"entitlements":['
    '{"term_type":"term_end","value":0,"effective_date":"2026-12-31","confidence_score":0.9,"provenance":"through December 31, 2026","page":1},'
    '{"term_type":"auto_renewal","value":12,"effective_date":null,"confidence_score":0.9,"provenance":"automatically renews","page":1},'
    '{"term_type":"renewal_notice_days","value":60,"effective_date":null,"confidence_score":0.9,"provenance":"60 days notice","page":1}]}')

_EMPTY = '{"customer_name":null,"entitlements":[]}'

_TEXT = ("Redwood Field Services, LLC committed to 240 seats at $65.00 per "
         "seat each month. The Initial Term begins January 1, 2026 and runs "
         "through December 31, 2026. This agreement automatically renews for "
         "twelve-month periods unless either party gives 60 days notice.")


class _RecallModels:
    def __init__(self):
        self.prompts = []

    def generate_content(self, **kwargs):
        contents = kwargs.get("contents")
        config = kwargs.get("config")
        schema = getattr(config, "response_schema", None)
        self.prompts.append(contents if isinstance(contents, str) else "")
        if getattr(schema, "__name__", "") == "DocumentProfile":
            return SimpleNamespace(
                text='{"role":"master","title":"Agreement",'
                     '"counterparty":"Redwood Field Services, LLC"}')
        if isinstance(contents, str) and contents.startswith("Focus only on: term_start"):
            return SimpleNamespace(text=_TERM_FAMILY)
        if isinstance(contents, str) and contents.startswith("Focus only on:"):
            return SimpleNamespace(text=_EMPTY)
        return SimpleNamespace(text=_UNFOCUSED)


class _RecallClient:
    def __init__(self):
        self.models = _RecallModels()


def test_focused_recall_pass_fills_missing_family():
    client = _RecallClient()
    result = extract_pages([Page(1, _TEXT)], "text", client=client)
    types = {e.term_type for e in result.entitlements}
    assert {"committed_seats", "seat_price", "term_start", "term_end",
            "auto_renewal", "renewal_notice_days"} <= types
    # Families whose signal phrases are absent get no focused call.
    assert not any(p.startswith("Focus only on: included_units")
                   for p in client.models.prompts)
    assert not any(p.startswith("Focus only on: escalator")
                   for p in client.models.prompts)
