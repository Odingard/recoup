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
from .ingestion_doc import ContractEntitlements, Entitlement
from .extraction.graph import ExtractedDocument, assemble
from .extraction.extractor import DocumentProfile
from .extraction.pages import DocumentTooLargeError
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


def _append_bundle(result: BulkResult, bundle, existing: dict | None = None) -> None:
    normalized = normalize_contract_entitlements(ContractEntitlements(
        customer_name=bundle.customer_name, entitlements=bundle.entitlements))
    if existing:
        normalized["customer_id"] = existing.get("customer_id") or normalized["customer_id"]
        normalized["customer_name"] = existing.get("customer_name") or normalized["customer_name"]
    normalized["documents"] = bundle.documents
    normalized["term_history"] = bundle.term_history
    normalized["file_name"] = bundle.file_name
    normalized["source_entitlements"] = [e.model_dump() for e in bundle.entitlements]
    result.contracts.append(normalized)
    for item in bundle.graph_needs_review:
        item.setdefault("customer_id", normalized.get("customer_id"))
        item.setdefault("customer_name", normalized.get("customer_name"))
        result.needs_review.append(item)


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

    # Contracts first so the CSV resolver can see them. Documents are extracted
    # individually, then assembled into one agreement bundle per counterparty so
    # amendments/exhibits attach to their master.
    from .identity import canonical_key
    existing_by_id = {}
    for c in existing_contracts:
        key = c.get("customer_id") or canonical_key(c.get("customer_name") or "")
        if key:
            existing_by_id[key] = c
            name_key = canonical_key(c.get("customer_name") or "")
            if name_key:
                existing_by_id.setdefault(name_key, c)
    existing_keys = set(existing_by_id)
    extracted_docs: list[ExtractedDocument] = []
    for name, content in flat:
        suffix = Path(name).suffix.lower()
        if suffix in CONTRACT_SUFFIXES:
            temp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            try:
                temp.write(content)
                temp.close()
                extracted = extract(temp.name)
            except DocumentTooLargeError as exc:
                result.files.append({"name": name, "kind": "contract", "status": "error",
                                     "message": str(exc)})
                result.needs_review.append({
                    "customer_id": None, "customer_name": name,
                    "term": "document_size",
                    "reason": str(exc),
                    "suggested_action": "Agreement exceeds 600 pages; split it by section and re-upload",
                })
                continue
            except Exception as exc:
                result.files.append({"name": name, "kind": "contract", "status": "error",
                                     "message": str(exc)})
                result.needs_review.append({
                    "customer_id": None, "customer_name": name,
                    "term": "contract_extraction",
                    "reason": f"Extraction failed: {exc}",
                    "suggested_action": "Upload a clearer copy of the agreement (PDF or DOCX) so Recoup can read the terms.",
                })
                continue
            finally:
                Path(temp.name).unlink(missing_ok=True)
            if not isinstance(extracted, ContractEntitlements):
                result.files.append({"name": name, "kind": "contract", "status": "error",
                                     "message": "No terms could be extracted."})
                result.needs_review.append({
                    "customer_id": None, "customer_name": name,
                    "term": "contract_extraction",
                    "reason": "No contract terms extracted; the document may be unreadable.",
                    "suggested_action": "Upload a clearer copy of the agreement (PDF or DOCX) so Recoup can read the terms.",
                })
                continue
            try:
                profile = DocumentProfile.model_validate(extracted.document or {})
            except Exception:
                profile = DocumentProfile()
            extracted_docs.append(ExtractedDocument(
                file_name=name, profile=profile,
                entitlements=list(extracted.entitlements),
                customer_name=extracted.customer_name))
            result.files.append({"name": name, "kind": "contract", "status": "success",
                                 "message": f"Extracted terms for {extracted.customer_name}",
                                 "count": 1})

    # Standalone documents for customers already on file merge with the stored
    # contract's source_entitlements when no new master was uploaded.
    upload_master_keys = {
        canonical_key(d.profile.counterparty or d.customer_name or "")
        for d in extracted_docs if d.profile.role == "master"
    }
    for doc in extracted_docs:
        key = canonical_key(doc.profile.counterparty or doc.customer_name or "")
        if doc.profile.role == "master" or not key or key not in existing_keys or key in upload_master_keys:
            continue
        prior = existing_by_id[key]
        prior.setdefault("customer_id", prior.get("customer_id") or key)
        prior_ents = prior.get("source_entitlements")
        if not prior_ents:
            result.needs_review.append({
                "customer_id": key, "customer_name": prior.get("customer_name", key),
                "term": "document_graph",
                "reason": "Re-upload the master agreement together with this amendment",
                "suggested_action": "Upload the master agreement and amendment together.",
            })
            doc._merged_into_existing = True  # type: ignore[attr-defined]
            continue
        docs_for_prior = [
            ExtractedDocument(
                file_name=d.get("file_name") or prior.get("file_name") or "agreement",
                profile=DocumentProfile.model_validate({"role": d.get("role", "master"),
                                                        "title": d.get("title"),
                                                        "effective_date": d.get("effective_date"),
                                                        "amendment_number": d.get("amendment_number"),
                                                        "counterparty": prior.get("customer_name")}),
                entitlements=[Entitlement(**e) for e in prior_ents
                              if e.get("source_file") in (None, d.get("file_name"), prior.get("file_name"))],
                customer_name=prior.get("customer_name"))
            for d in (prior.get("documents") or [{"file_name": prior.get("file_name"), "role": "master"}])
        ]
        for bundle in assemble(docs_for_prior + [doc]):
            _append_bundle(result, bundle, existing=prior)
        doc._merged_into_existing = True  # type: ignore[attr-defined]

    for bundle in assemble([d for d in extracted_docs
                            if not getattr(d, "_merged_into_existing", False)]):
        _append_bundle(result, bundle)

    resolver = CustomerResolver(existing_contracts + result.contracts)
    party_ids = {c.get("customer_id") for c in resolver.contracts
                 if c.get("customer_id")}
    default_customer = None
    if len(party_ids) == 1:
        only = next(c for c in resolver.contracts
                    if c.get("customer_id") == next(iter(party_ids)))
        default_customer = only.get("customer_name") or only["customer_id"]
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
                usage, nr = load_usage_csv(Path(temp.name), resolver,
                                           default_customer=default_customer)
                result.usage.extend(usage)
                result.needs_review.extend(nr)
                message = f"{len(usage)} usage periods"
                if usage and all(r.get("customer_inferred") for r in usage):
                    message = (f"usage · {len(usage)} periods · attributed to "
                               f"{default_customer} (only counterparty)")
                result.files.append({"name": name, "kind": "usage", "status": "success",
                                     "message": message, "count": len(usage)})
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
