from __future__ import annotations

import io
import zipfile

from recoup_agent.ingest_bulk import expand_zip, ingest_files
from recoup_agent.ingestion_doc import ContractEntitlements, Entitlement


def fake_extract(path: str) -> ContractEntitlements:
    return ContractEntitlements(
        customer_name="Acme Corp",
        entitlements=[
            Entitlement(
                term_type="committed_minimum",
                value=5000,
                effective_date=None,
                confidence_score=0.95,
                provenance="Clause 2",
            )
        ],
    )


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


INVOICES_CSV = b"customer_id,invoice_id,period_start,amount,description\nacme,inv-1,2026-06-01,5000,Platform subscription\n"
USAGE_CSV = b"customer_id,period,units,metric\nacme,2026-06,12000,api_calls\n"


def test_ingest_files_direct_mixed():
    result = ingest_files(
        [
            ("contract.txt", b"Acme contract text"),
            ("invoices.csv", INVOICES_CSV),
            ("usage.csv", USAGE_CSV),
            ("notes.xlsx", b"PK-not-really-xlsx"),
        ],
        [],
        fake_extract,
    )
    assert len(result.contracts) == 1
    assert result.contracts[0]["customer_id"] == "acme"
    assert len(result.invoices) == 1
    assert result.invoices[0]["customer_id"] == "acme"
    assert len(result.usage) == 1

    kinds = {f["name"]: f for f in result.files}
    assert kinds["contract.txt"]["status"] == "success"
    assert kinds["invoices.csv"]["kind"] == "invoices"
    assert kinds["notes.xlsx"]["status"] == "skipped"
    assert result.needs_review


def test_zip_expansion_and_nested_dirs():
    archive = _zip(
        {
            "contracts/acme.txt": b"Acme contract text",
            "billing/invoices.csv": INVOICES_CSV,
            "usage.csv": USAGE_CSV,
            "__MACOSX/junk": b"junk",
            ".hidden": b"junk",
        }
    )
    result = ingest_files([("dump.zip", archive)], [], fake_extract)
    assert len(result.contracts) == 1
    assert len(result.invoices) == 1
    assert len(result.usage) == 1
    names = [f["name"] for f in result.files]
    assert "contracts/acme.txt" in names
    assert "__MACOSX/junk" not in names
    assert ".hidden" not in names


def test_zip_slip_rejected():
    archive = _zip({"../../evil.txt": b"oops"})
    result = ingest_files([("evil.zip", archive)], [], fake_extract)
    assert not result.contracts
    entry = next(f for f in result.files if f["name"] == "evil.zip")
    assert entry["status"] == "error"
    assert result.needs_review


def test_nested_zip_skipped():
    inner = _zip({"inner.txt": b"text"})
    outer = _zip({"outer.txt": b"contract", "nested.zip": inner})
    result = ingest_files([("outer.zip", outer)], [], fake_extract)
    entry = next(f for f in result.files if f["name"] == "nested.zip")
    assert entry["status"] == "skipped"


def test_unclassifiable_csv_is_error_not_exception():
    result = ingest_files(
        [("contract.txt", b"text"), ("mystery.csv", b"foo,bar\n1,2\n")],
        [],
        fake_extract,
    )
    entry = next(f for f in result.files if f["name"] == "mystery.csv")
    assert entry["status"] in ("skipped", "error")
    assert result.needs_review


def test_expand_zip_size_guard():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for i in range(3):
            zf.writestr(f"f{i}.txt", b"x" * (80 * 1024 * 1024))
    items, error = expand_zip("big.zip", buf.getvalue())
    assert error
    assert not items


def test_extraction_failure_is_error_entry():
    def bad_extract(path):
        raise RuntimeError("boom")

    result = ingest_files([("bad.txt", b"text")], [], bad_extract)
    assert result.files[0]["status"] == "error"
    assert result.needs_review
