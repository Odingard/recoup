"""Golden regression: the clean and messy books must keep producing the
known-good audit numbers ($11,010/mo across 2026-06..2026-07)."""
from pathlib import Path

import pytest

from recoup_agent.book_loader import load_book, book_periods
from recoup_agent.pipeline import run_book

GOLDEN = Path(__file__).parent / "golden"


def _totals(findings_by_period):
    total = sum(f["monthly_recoverable"] for fs in findings_by_period.values() for f in fs)
    flagged = {f["customer_name"] for fs in findings_by_period.values() for f in fs}
    return round(total, 2), flagged


def test_golden_clean_book():
    contracts, usage, invoices = load_book(GOLDEN / "clean")
    findings_by_period, needs_review = run_book(contracts, usage, invoices)
    total, flagged = _totals(findings_by_period)
    assert total == pytest.approx(11010.00, abs=0.01)
    assert "Volt Robotics" not in " ".join(flagged)
    assert "Harbor" not in " ".join(flagged)
    assert len(needs_review) == 0


def test_golden_messy_book_cache_only(monkeypatch):
    import recoup_agent.ingest_dir as ingest_dir

    def _boom(path):
        raise AssertionError("extraction called; golden test must be cache-only")

    monkeypatch.setattr(ingest_dir, "extract_entitlements", _boom)
    contracts, usage, invoices, seed_review = ingest_dir.load_book_from_dir(GOLDEN / "messy")
    findings_by_period, needs_review = run_book(contracts, usage, invoices, seed_review=seed_review)
    total, flagged = _totals(findings_by_period)
    all_findings = [f for fs in findings_by_period.values() for f in fs]
    # Sterling's messy contract escalates from 2025-03-01 (first anniversary of a
    # 2024-03-01 commencement): compounding gives 2 missed steps = $489.60/mo per
    # period, vs $240 in the clean set where the escalator starts 2026-03.
    assert total == pytest.approx(11509.20, abs=0.01)
    assert len(all_findings) == 8
    assert len(needs_review) == 2
    assert "Volt Robotics" not in " ".join(flagged)
    assert "Harbor" not in " ".join(flagged)
    for f in all_findings:
        assert (f.get("clause_text") or "").strip()
        assert (f.get("provenance") or "").strip()
