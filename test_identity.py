"""Identity resolution: canonical_key and CustomerResolver."""
from recoup_agent.identity import CustomerResolver, canonical_key


def test_canonical_key_strips_suffixes_and_punctuation():
    assert canonical_key("NorthPeak Logistics LLC") == "northpeak_logistics"
    assert canonical_key("northpeak-logistics") == "northpeak_logistics"
    assert canonical_key("NorthPeak Logistics, LLC.") == "northpeak_logistics"


def test_canonical_key_expands_abbreviations():
    assert canonical_key("Sterling Dental Grp") == "sterling_dental"
    assert canonical_key("Sterling Dental Group") == "sterling_dental"
    assert canonical_key("Sterling Dental") == "sterling_dental"
    assert canonical_key("Harbor Freight Co.") == "harbor_freight"
    assert canonical_key("Harbor Freight") == "harbor_freight"


def test_canonical_key_empty():
    assert canonical_key("") == ""
    assert canonical_key("LLC") == ""


_CONTRACTS = [
    {"customer_id": "northpeak_logistics", "customer_name": "NorthPeak Logistics LLC"},
    {"customer_id": "sterling_dental", "customer_name": "Sterling Dental Group"},
    {"customer_id": "cascade_analytics", "customer_name": "Cascade Analytics, Inc."},
    {"customer_id": "meridian_foods", "customer_name": "Meridian Foods"},
]


def test_resolver_exact_and_prefix():
    r = CustomerResolver(_CONTRACTS)
    assert r.resolve("NorthPeak Logistics") == "northpeak_logistics"
    assert r.resolve("northpeak-logistics") == "northpeak_logistics"
    assert r.resolve("Sterling Dental") == "sterling_dental"
    assert r.resolve("sterling-dental-grp") == "sterling_dental"
    assert r.resolve("Cascade Analytics Inc") == "cascade_analytics"
    assert r.resolve("cascade-analytics") == "cascade_analytics"
    assert r.resolve("Meridian Foods LLC") == "meridian_foods"


def test_resolver_unmatched_returns_none():
    r = CustomerResolver(_CONTRACTS)
    assert r.resolve("Acme Corp") is None
    assert "no contract matches" in r.explain("Acme Corp")


def test_resolver_ambiguous_returns_none():
    contracts = _CONTRACTS + [
        {"customer_id": "sterling_dental_llc", "customer_name": "Sterling Dental LLC"},
    ]
    r = CustomerResolver(contracts)
    # Suffix-stripped key "sterling_dental" is shared by two contracts.
    assert r.resolve("Sterling Dental Inc.") is None
    assert "ambiguous" in r.explain("Sterling Dental Inc.")
    # No first-token/prefix matching: "sterling" alone never resolves.
    assert r.resolve("sterling") is None
    assert "no contract matches" in r.explain("sterling")


def test_unmatched_csv_label_lands_in_needs_review(tmp_path):
    from recoup_agent.ingest_csv import load_usage_csv

    csv_file = tmp_path / "usage.csv"
    csv_file.write_text(
        "account,month,metric,qty\n"
        "northpeak-logistics,2026-06,api_calls,1340000\n"
        "unknown-widget-co,2026-06,seats,5\n"
        "unknown-widget-co,2026-07,seats,7\n"
    )
    usage, needs_review = load_usage_csv(csv_file, CustomerResolver(_CONTRACTS))
    assert usage == [{"customer_id": "northpeak_logistics", "period": "2026-06", "units": 1340000.0}]
    unmatched = [i for i in needs_review if i["term"] == "customer_identity"]
    assert len(unmatched) == 1  # once per distinct label, not per row
    assert unmatched[0]["customer_name"] == "unknown-widget-co"
    assert "no contract matches" in unmatched[0]["reason"]


def test_usage_csv_without_customer_column_single_counterparty(tmp_path):
    from recoup_agent.ingest_csv import load_usage_csv

    csv_file = tmp_path / "usage.csv"
    csv_file.write_text("period,units\n2026-06,1200\n2026-07,900\n")
    usage, needs_review = load_usage_csv(
        csv_file,
        CustomerResolver([{"customer_id": "ironclad_manufacturing",
                           "customer_name": "Ironclad Manufacturing, LLC"}]),
        default_customer="Ironclad Manufacturing, LLC")
    assert needs_review == []
    assert len(usage) == 2
    assert all(r["customer_id"] == "ironclad_manufacturing" for r in usage)
    assert all(r["customer_inferred"] is True for r in usage)


def test_usage_csv_without_customer_column_fails_closed(tmp_path):
    import pytest
    from recoup_agent.ingest_csv import IngestError, load_usage_csv

    csv_file = tmp_path / "usage.csv"
    csv_file.write_text("period,units\n2026-06,1200\n")
    with pytest.raises(IngestError, match="2 counterparties"):
        load_usage_csv(csv_file, CustomerResolver(
            _CONTRACTS[:2]))
    with pytest.raises(IngestError, match="0 counterparties"):
        load_usage_csv(csv_file, CustomerResolver([]))


def test_usage_csv_with_customer_column_ignores_default(tmp_path):
    from recoup_agent.ingest_csv import load_usage_csv

    csv_file = tmp_path / "usage.csv"
    csv_file.write_text(
        "customer,period,units\nMeridian Foods,2026-06,50\n")
    usage, _nr = load_usage_csv(
        csv_file, CustomerResolver(_CONTRACTS),
        default_customer="NorthPeak Logistics LLC")
    assert usage[0]["customer_id"] == "meridian_foods"
    assert "customer_inferred" not in usage[0]
