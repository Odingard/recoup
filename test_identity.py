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
        {"customer_id": "sterling_cooper", "customer_name": "Sterling Cooper"},
    ]
    r = CustomerResolver(contracts)
    assert r.resolve("sterling") is None
    assert "ambiguous" in r.explain("sterling")
