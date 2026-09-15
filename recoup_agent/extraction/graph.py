"""Assemble a per-counterparty agreement bundle from extracted documents.

Model output classifies each document; this module deterministically groups
documents, orders amendments, and applies precedence rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..identity import canonical_key
from ..ingestion_doc import ContractEntitlements, Entitlement
from .extractor import DocumentProfile, PageAnchoredEntitlement
from .pages import Page

SINGLE_VALUE_TERMS = {
    "included_units", "overage_rate", "escalator", "seat_price", "committed_seats",
    "term_start", "term_end", "auto_renewal", "renewal_notice_days",
}
SUPPLY_ROLES = {"order_form", "exhibit", "sow", "other"}


@dataclass
class ExtractedDocument:
    file_name: str
    profile: DocumentProfile
    entitlements: list = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)
    customer_name: Optional[str] = None
    structural_verification: dict | None = None
    document_id: str | None = None


@dataclass
class AgreementBundle:
    customer_name: str
    entitlements: list[Entitlement]
    documents: list[dict]
    term_history: dict[str, list[dict]]
    graph_needs_review: list[dict]
    file_name: Optional[str] = None


def _profile(doc: ExtractedDocument) -> DocumentProfile:
    p = doc.profile
    if isinstance(p, DocumentProfile):
        return p
    if isinstance(p, dict):
        return DocumentProfile.model_validate(p)
    return DocumentProfile()


def _group_key(doc: ExtractedDocument) -> str:
    p = _profile(doc)
    return canonical_key(p.counterparty or doc.customer_name or "")


def _mention(reference: str, title: str) -> bool:
    ref, t = canonical_key(reference), canonical_key(title)
    return bool(ref and t and (t in ref or ref in t))


def _sort_key(doc: ExtractedDocument):
    return (_profile(doc).effective_date is None, _profile(doc).effective_date or "")


def _as_entitlement(raw, source_file: str) -> Entitlement:
    if isinstance(raw, Entitlement):
        data = raw.model_dump()
    elif isinstance(raw, PageAnchoredEntitlement):
        data = raw.model_dump()
    else:
        data = dict(raw)
    data["source_file"] = source_file
    return Entitlement(**data)


def assemble(documents: list[ExtractedDocument]) -> list[AgreementBundle]:
    groups: dict[str, list[ExtractedDocument]] = {}
    orphans: list[ExtractedDocument] = []
    for doc in documents:
        key = _group_key(doc)
        if key:
            groups.setdefault(key, []).append(doc)
        else:
            orphans.append(doc)

    # Attach unkeyed documents to a group when their references name its master.
    master_titles = {
        key: _profile(m).title or ""
        for key, docs in groups.items()
        for m in docs if _profile(m).role == "master"
    }
    for doc in orphans:
        p = _profile(doc)
        matched = next((key for key, title in master_titles.items()
                        if any(_mention(ref, title) for ref in p.references)), None)
        if matched:
            groups[matched].append(doc)
        else:
            groups[f"__ungrouped_{len(groups)}"] = [doc]
            doc._graph_note = (  # type: ignore[attr-defined]
                f"Could not tell which agreement this {p.role} belongs to")

    bundles: list[AgreementBundle] = []
    for key, docs in groups.items():
        needs_review: list[dict] = []
        profiles = {id(d): _profile(d) for d in docs}
        for d in docs:
            note = getattr(d, "_graph_note", None)
            if note:
                needs_review.append({"term": "document_graph", "reason": note,
                                     "suggested_action": "Upload the agreement documents together, or re-upload the master.",
                                     "file_name": d.file_name})

        masters = [d for d in docs if profiles[id(d)].role == "master"]
        masters.sort(key=_sort_key)
        master = masters[-1] if masters else None
        superseded_masters = masters[:-1]
        for old in superseded_masters:
            needs_review.append({"term": "document_graph",
                                 "reason": f"Multiple master agreements for this counterparty; '{master.file_name}' ({profiles[id(master)].effective_date}) treated as current and '{old.file_name}' superseded.",
                                 "suggested_action": "Confirm the correct master agreement.",
                                 "file_name": old.file_name})
        if master is None:
            # No explicit master: earliest dated doc anchors the bundle.
            candidates = sorted(docs, key=_sort_key)
            master = candidates[0]

        amendments = [d for d in docs if profiles[id(d)].role == "amendment" and d is not master]
        undated = [d for d in amendments if not profiles[id(d)].effective_date]
        for d in undated:
            needs_review.append({"term": "document_graph",
                                 "reason": "Amendment has no effective date; cannot apply precedence",
                                 "suggested_action": "Confirm the amendment effective date and re-upload.",
                                 "file_name": d.file_name})
        dated_amendments = sorted((d for d in amendments if profiles[id(d)].effective_date),
                                  key=lambda d: profiles[id(d)].effective_date)

        supply = [d for d in docs if profiles[id(d)].role in SUPPLY_ROLES and d is not master]
        ordered_sources = [master] + sorted(supply, key=_sort_key)

        selected: dict[str, Entitlement] = {}
        minimums: list[Entitlement] = []
        others: list[Entitlement] = []  # multi-value terms (discount, overage_tier)
        master_terms: dict[str, Entitlement] = {}
        term_history: dict[str, list[dict]] = {}

        def _hist(term: str, ent: Entitlement, file_name: str):
            term_history.setdefault(term, []).append({
                "value": ent.value, "effective_date": ent.effective_date,
                "source_file": file_name, "page": ent.page})

        for d in ordered_sources:
            fname = d.file_name
            is_master = d is master
            for raw in d.entitlements:
                ent = _as_entitlement(raw, fname)
                tt = ent.term_type
                if tt == "committed_minimum":
                    if is_master or tt not in master_terms:
                        minimums.append(ent)
                        _hist(tt, ent, fname)
                    elif master_terms[tt].value != ent.value:
                        needs_review.append({"term": tt,
                                             "reason": "Exhibit value conflicts with master",
                                             "suggested_action": "Confirm which document's term applies.",
                                             "file_name": fname})
                    if is_master:
                        master_terms[tt] = ent
                elif tt in SINGLE_VALUE_TERMS:
                    if tt not in selected:
                        selected[tt] = ent
                        _hist(tt, ent, fname)
                        if is_master:
                            master_terms[tt] = ent
                    elif is_master:
                        selected[tt] = ent
                        _hist(tt, ent, fname)
                        master_terms[tt] = ent
                    elif tt in master_terms and master_terms[tt].value != ent.value:
                        needs_review.append({"term": tt,
                                             "reason": "Exhibit value conflicts with master",
                                             "suggested_action": "Confirm which document's term applies.",
                                             "file_name": fname})
                else:
                    others.append(ent)
                    if is_master:
                        master_terms[tt] = ent

        # Amendments override in effective-date order; minimums append.
        for d in dated_amendments:
            fname = d.file_name
            for raw in d.entitlements:
                ent = _as_entitlement(raw, fname)
                tt = ent.term_type
                if tt == "committed_minimum":
                    minimums.append(ent)
                    _hist(tt, ent, fname)
                elif tt in SINGLE_VALUE_TERMS:
                    selected[tt] = ent
                    _hist(tt, ent, fname)
                else:
                    others.append(ent)

        entitlements = minimums + list(selected.values()) + others
        # Deterministic order for downstream normalization.
        entitlements.sort(key=lambda e: (e.effective_date is not None, e.effective_date or ""))
        doc_records = [{
            "structural_verification": d.structural_verification,
            "document_id": d.document_id,
            "file_name": d.file_name,
            "role": profiles[id(d)].role,
            "title": profiles[id(d)].title,
            "effective_date": profiles[id(d)].effective_date,
            "pages": len(d.pages) if d.pages else None,
            "amendment_number": profiles[id(d)].amendment_number,
        } for d in (superseded_masters + [master] + sorted(supply, key=_sort_key) + dated_amendments + undated)]
        customer_name = (profiles[id(master)].counterparty or master.customer_name
                         or next((profiles[id(d)].counterparty or d.customer_name
                                  for d in docs if profiles[id(d)].counterparty or d.customer_name), "Unknown"))
        bundles.append(AgreementBundle(customer_name=customer_name or "Unknown",
                                       entitlements=entitlements,
                                       documents=doc_records,
                                       term_history=term_history,
                                       graph_needs_review=needs_review,
                                       file_name=master.file_name))
    return bundles


def bundle_to_contract(bundle: AgreementBundle) -> ContractEntitlements:
    return ContractEntitlements(customer_name=bundle.customer_name,
                                entitlements=bundle.entitlements)
