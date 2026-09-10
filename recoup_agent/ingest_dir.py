"""Load a whole customer book from a directory of contract documents and
billing/usage CSV exports (the "messy" real-world shape).

Contract text goes through Gemini extraction; results are cached under
<dir>/.recoup_cache/ keyed by file content so re-runs don't call the API.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .identity import CustomerResolver
from .ingest_csv import IngestError, classify_csv, load_invoices_csv, load_usage_csv
from .ingestion_doc import ContractEntitlements, extract_entitlements
from .normalizer import normalize_contract_entitlements

CONTRACT_SUFFIXES = {".txt", ".pdf", ".docx", ".md"}


def _extract_cached(path: Path, cache_dir: Path) -> ContractEntitlements:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    cache_file = cache_dir / f"{digest}.json"
    if cache_file.exists():
        return ContractEntitlements.model_validate_json(cache_file.read_text())
    extracted = extract_entitlements(str(path))
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(extracted.model_dump(), indent=2))
    return extracted


def load_book_from_dir(directory: Path) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """-> (contracts, usage, invoices, needs_review) in internal schema."""
    directory = Path(directory)
    cache_dir = directory / ".recoup_cache"

    contract_files = sorted(
        p for base in (directory, directory / "contracts")
        if base.is_dir()
        for p in base.iterdir()
        if p.is_file() and p.suffix.lower() in CONTRACT_SUFFIXES
    )

    contracts: list[dict] = []
    needs_review: list[dict] = []
    for path in contract_files:
        try:
            extracted = _extract_cached(path, cache_dir)
            contracts.append(normalize_contract_entitlements(extracted))
        except Exception as exc:
            needs_review.append({
                "customer_id": None,
                "customer_name": path.name,
                "term": "contract_extraction",
                "reason": str(exc),
                "suggested_action": "Check the document and re-run, or enter the terms manually",
            })

    resolver = CustomerResolver(contracts)
    usage: list[dict] = []
    invoices: list[dict] = []
    for path in sorted(directory.glob("*.csv")):
        kind = classify_csv(path)
        if kind == "invoices":
            inv, nr = load_invoices_csv(path, resolver)
        elif kind == "usage":
            inv, nr = load_usage_csv(path, resolver)
            usage.extend(inv)
            needs_review.extend(nr)
            continue
        else:
            with open(path, newline="") as fh:
                header = fh.readline().strip()
            raise IngestError(f"{path}: could not classify CSV (headers: {header})")
        invoices.extend(inv)
        needs_review.extend(nr)

    return contracts, usage, invoices, needs_review
