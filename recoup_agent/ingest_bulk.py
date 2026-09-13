"""Bulk ingestion: many files (or ZIPs of files) at once.

Contracts go through document extraction; CSVs are classified as invoices or
usage via ingest_csv; everything unclassifiable lands in needs_review — never
silently dropped.
"""
from __future__ import annotations

import io
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

from .identity import CustomerResolver
from .ingest_csv import IngestError, classify_csv, load_invoices_csv, load_usage_csv
from .ingestion_doc import ContractEntitlements
from .normalizer import normalize_contract_entitlements

CONTRACT_SUFFIXES = {".pdf", ".docx", ".txt", ".md", ".png", ".jpg", ".jpeg"}
MAX_ZIP_FILES = 500
MAX_ZIP_BYTES = 200 * 1024 * 1024  # 200 MB uncompressed
MAX_ZIP_MEMBER_BYTES = 25 * 1024 * 1024


@dataclass
class BulkResult:
    contracts: list[dict] = field(default_factory=list)
    invoices: list[dict] = field(default_factory=list)
    usage: list[dict] = field(default_factory=list)
    needs_review: list[dict] = field(default_factory=list)
    files: list[dict] = field(default_factory=list)


def expand_zip(name: str, content: bytes) -> tuple[list[tuple[str, bytes]], str | None]:
    """-> (items, error). Skips dirs, __MACOSX, dotfiles, nested zips, zip-slip."""
    items: list[tuple[str, bytes]] = []
    total = 0
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        return [], "not a valid ZIP archive"
    for info in zf.infolist():
        path = PurePosixPath(info.filename)
        if info.is_dir() or path.name.startswith(".") or "__MACOSX" in path.parts:
            continue
        if path.is_absolute() or ".." in path.parts:
            return [], f"unsafe path in archive: {info.filename}"
        if path.suffix.lower() == ".zip":
            items.append((str(path), b""))  # nested zip: flagged, not expanded
            continue
        if info.file_size > MAX_ZIP_MEMBER_BYTES:
            return [], f"member {info.filename} exceeds 25 MB"
        total += info.file_size
        if len(items) >= MAX_ZIP_FILES or total > MAX_ZIP_BYTES:
            return [], f"archive exceeds limits ({MAX_ZIP_FILES} files / 200 MB)"
        with zf.open(info) as fh:
            data = fh.read(MAX_ZIP_MEMBER_BYTES + 1)
        if len(data) > MAX_ZIP_MEMBER_BYTES:
            return [], f"member {info.filename} exceeds 25 MB"
        items.append((str(path), data))
    return items, None


def _expand(items: list[tuple[str, bytes]]) -> tuple[list[tuple[str, bytes]], list[dict]]:
    """Expand .zip items one level deep; nested zips become skipped file rows."""
    flat: list[tuple[str, bytes]] = []
    skipped: list[dict] = []
    for name, content in items:
        if name.lower().endswith(".zip"):
            expanded, error = expand_zip(name, content)
            if error:
                skipped.append({"name": name, "kind": "zip", "status": "error", "message": error})
                continue
            for inner_name, inner in expanded:
                if inner_name.lower().endswith(".zip"):
                    skipped.append({"name": inner_name, "kind": "zip",
                                    "status": "skipped", "message": "Nested archives are not expanded."})
                    continue
                flat.append((inner_name, inner))
        else:
            flat.append((name, content))
    return flat, skipped


def ingest_files(items: list[tuple[str, bytes]], existing_contracts: list[dict],
                 extract: Callable[[str], object]) -> BulkResult:
    """items = [(filename, bytes)]; extract = extract_entitlements(path)->ContractEntitlements."""
    result = BulkResult()
    flat, skipped = _expand(items)
    result.files.extend(skipped)
    for row in skipped:
        if row["status"] == "error":
            result.needs_review.append({
                "customer_id": None, "customer_name": row["name"],
                "term": "zip_expansion", "reason": row["message"],
                "suggested_action": "Fix the archive and re-upload",
            })

    # Contracts first so the CSV resolver can see them.
    for name, content in flat:
        suffix = Path(name).suffix.lower()
        if suffix in CONTRACT_SUFFIXES:
            temp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            try:
                temp.write(content)
                temp.close()
                extracted = extract(temp.name)
            except Exception as exc:
                result.files.append({"name": name, "kind": "contract", "status": "error",
                                     "message": str(exc)})
                result.needs_review.append({
                    "customer_id": None, "customer_name": name,
                    "term": "contract_extraction",
                    "reason": f"Extraction failed: {exc}",
                    "suggested_action": "Enter terms manually.",
                })
                continue
            finally:
                Path(temp.name).unlink(missing_ok=True)
            if not isinstance(extracted, ContractEntitlements) or not extracted.entitlements:
                result.files.append({"name": name, "kind": "contract", "status": "error",
                                     "message": "No terms could be extracted."})
                result.needs_review.append({
                    "customer_id": None, "customer_name": name,
                    "term": "contract_extraction",
                    "reason": "No contract terms extracted; enter terms manually.",
                    "suggested_action": "Open the contract, enter the terms manually in Step 1, or upload a clearer/searchable copy.",
                })
                continue
            normalized = normalize_contract_entitlements(extracted)
            result.contracts.append(normalized)
            result.files.append({"name": name, "kind": "contract", "status": "success",
                                 "message": f"Extracted terms for {normalized.get('customer_name')}",
                                 "count": 1})

    resolver = CustomerResolver(existing_contracts + result.contracts)
    for name, content in flat:
        suffix = Path(name).suffix.lower()
        if suffix in CONTRACT_SUFFIXES:
            continue
        if suffix != ".csv":
            result.files.append({"name": name, "kind": "other", "status": "skipped",
                                 "message": f"Unsupported file type '{suffix or '(none)'}'."})
            result.needs_review.append({
                "customer_id": None, "customer_name": name,
                "term": "file_type",
                "reason": f"Unsupported file type '{suffix}'; upload PDF/DOCX/TXT/MD/images or CSV.",
                "suggested_action": "Convert to a supported format and re-upload",
            })
            continue
        temp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
        try:
            temp.write(content)
            temp.close()
            kind = classify_csv(Path(temp.name))
            if kind == "invoices":
                invoices, nr = load_invoices_csv(Path(temp.name), resolver)
                result.invoices.extend(invoices)
                result.needs_review.extend(nr)
                result.files.append({"name": name, "kind": "invoices", "status": "success",
                                     "message": f"{len(invoices)} invoice periods", "count": len(invoices)})
            elif kind == "usage":
                usage, nr = load_usage_csv(Path(temp.name), resolver)
                result.usage.extend(usage)
                result.needs_review.extend(nr)
                result.files.append({"name": name, "kind": "usage", "status": "success",
                                     "message": f"{len(usage)} usage periods", "count": len(usage)})
            else:
                result.files.append({"name": name, "kind": "csv", "status": "error",
                                     "message": "Could not classify CSV (need an amount or units column)."})
                result.needs_review.append({
                    "customer_id": None, "customer_name": name,
                    "term": "csv_classification",
                    "reason": "CSV has neither an amount nor a units column.",
                    "suggested_action": "Use one of the export templates",
                })
        except IngestError as exc:
            result.files.append({"name": name, "kind": "csv", "status": "error", "message": str(exc)})
            result.needs_review.append({
                "customer_id": None, "customer_name": name,
                "term": "csv_ingest", "reason": str(exc),
                "suggested_action": "Fix the column headers or use one of the export templates",
            })
        finally:
            Path(temp.name).unlink(missing_ok=True)

    return result
