from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from statistics import median

from .cloud.documents import Page, Point, TextBlock, TextSpan

NEEDS_VERIFICATION = "Needs_Verification"
CRITICAL_TERMS = re.compile(
    r"\b(?:renew\w*|notice|sixty|60|days?|commit\w*|minimum|seats?|"
    r"licen[cs]\w*|fees?|rates?|prices?|overage|included|discount\w*|"
    r"escalat\w*|refund\w*|credit\w*|rebate\w*|term|expires?|expiration)\b|[$€£%]",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class StructuralIssue:
    page: int
    code: str
    reason: str
    block: int | None = None
    observed: float | None = None
    limit: float | None = None


class LowConfidenceGateException(Exception):
    def __init__(self, issues: list[StructuralIssue]):
        super().__init__("Document structure requires verification before reconciliation.")
        self.issues = issues

    def payload(self) -> dict:
        return {
            "state": NEEDS_VERIFICATION,
            "reason": str(self),
            "issues": [asdict(issue) for issue in self.issues],
            "suggested_action": "Inspect the flagged pages and obtain a clear replacement for review.",
        }


def bounds(polygon: tuple[Point, ...]) -> tuple[float, float, float, float]:
    return (min(p.x for p in polygon), min(p.y for p in polygon),
            max(p.x for p in polygon), max(p.y for p in polygon))


def polygon_valid(polygon: tuple[Point, ...]) -> bool:
    if len(polygon) < 3 or any(
        not math.isfinite(v) or not 0 <= v <= 1
        for p in polygon for v in (p.x, p.y)
    ):
        return False
    if len(set(polygon)) != len(polygon):
        return False
    for i, start in enumerate(polygon):
        end = polygon[(i+1) % len(polygon)]
        for j in range(i+2, len(polygon)):
            if i == 0 and j == len(polygon)-1:
                continue
            other, last = polygon[j], polygon[(j+1) % len(polygon)]
            if (_cross(start, end, other) * _cross(start, end, last) < 0
                    and _cross(other, last, start) * _cross(other, last, end) < 0):
                return False
    crosses = []
    for i, point in enumerate(polygon):
        nxt, end = polygon[(i + 1) % len(polygon)], polygon[(i + 2) % len(polygon)]
        crosses.append((nxt.x - point.x) * (end.y - nxt.y)
                       - (nxt.y - point.y) * (end.x - nxt.x))
    return (any(c > 1e-10 for c in crosses) and all(c >= -1e-10 for c in crosses)
            or any(c < -1e-10 for c in crosses) and all(c <= 1e-10 for c in crosses))


def _cross(start: Point, end: Point, point: Point) -> float:
    return ((end.x - start.x) * (point.y - start.y)
            - (end.y - start.y) * (point.x - start.x))


def _anchored(text: str, spans: tuple[TextSpan, ...], source: str) -> bool:
    end = -1
    for span in spans:
        if not 0 <= span.start < span.end <= len(source) or span.start < end:
            return False
        end = span.end
    return bool(spans) and "".join(source[s.start:s.end] for s in spans) == text


def critical_blocks(page: Page) -> list[tuple[int, TextBlock]]:
    return [(i, block) for i, block in enumerate(page.blocks)
            if CRITICAL_TERMS.search(block.text)]


def inspect_structure(pages: list[Page]) -> list[StructuralIssue]:
    issues: list[StructuralIssue] = []
    for page in pages:
        targets = critical_blocks(page)
        if not page.layout_available:
            issues.append(StructuralIssue(
                page.number, "layout_unavailable", "Visual document has no auditable layout metadata."))
            continue
        expected_keywords = Counter(m.group().lower() for m in CRITICAL_TERMS.finditer(page.text))
        mapped_keywords = Counter(m.group().lower() for _, b in targets
                                  for m in CRITICAL_TERMS.finditer(b.text))
        if expected_keywords - mapped_keywords:
            issues.append(StructuralIssue(
                page.number, "unmapped_critical_text", "Critical text is absent from the layout blocks."))
        source = page.layout_text if page.layout_text is not None else page.text
        for index, block in targets:
            def flag(code: str, reason: str, observed=None, limit=None):
                issues.append(StructuralIssue(page.number, code, reason, index, observed, limit))

            if not polygon_valid(block.polygon):
                flag("irregular_polygon", "Critical block polygon is missing, degenerate, or invalid.")
                continue
            if not _anchored(block.text, block.spans, source):
                flag("anchor_drift", "Critical block text does not match its source anchors.")
                continue
            tokens = block.tokens
            if not tokens:
                flag("missing_tokens", "Critical block has no aligned tokens.")
                continue
            x0, y0, x1, y1 = bounds(block.polygon)
            covered: set[int] = set()
            heights = []
            for token in tokens:
                if not polygon_valid(token.polygon):
                    flag("irregular_token_polygon", "Critical token polygon is missing or invalid.")
                    break
                tx0, ty0, tx1, ty1 = bounds(token.polygon)
                if tx0 < x0 - 0.003 or ty0 < y0 - 0.003 or tx1 > x1 + 0.003 or ty1 > y1 + 0.003:
                    flag("token_boundary_drift", "Token geometry extends beyond its critical block.")
                    break
                if not _anchored(token.text, token.spans, source) or any(
                    not any(b.start <= s.start < s.end <= b.end for b in block.spans)
                    for s in token.spans
                ):
                    flag("token_anchor_drift", "Critical token anchors disagree with the block.")
                    break
                positions = {p for s in token.spans for p in range(s.start, s.end)
                             if not source[p].isspace()}
                if covered & positions:
                    flag("overlapping_token_anchors", "Critical tokens duplicate source characters.")
                    break
                covered.update(positions)
                heights.append(ty1 - ty0)
            else:
                expected = {p for s in block.spans for p in range(s.start, s.end)
                            if not source[p].isspace()}
                if covered != expected:
                    flag("token_coverage_drift", "Critical token anchors do not cover the block text.")
                words = len(re.findall(r"\S+", block.text))
                ratio = len(tokens) / max(words, 1)
                if len(tokens) >= 8 and ratio > 2.5:
                    flag("subword_fragmentation", "Critical text is fragmented into sub-word tokens.", ratio, 2.5)
                if heights:
                    typical = median(heights)
                    tiny = sum(h < max(typical * 0.3, 0.0015) for h in heights) / len(heights)
                    density = sum(len(t.text.strip()) for t in tokens) * typical**2 / ((x1-x0)*(y1-y0))
                    if len(tokens) >= 4 and tiny >= 0.4:
                        flag("microscopic_tokens", "Critical block contains excessive microscopic tokens.", tiny, 0.4)
                    if density > 12:
                        flag("token_density", "Token density exceeds the critical block's geometry.", density, 12)
        valid = [(i, b) for i, b in enumerate(page.blocks) if polygon_valid(b.polygon)]
        for index, block in targets:
            if not polygon_valid(block.polygon):
                continue
            x0, y0, x1, y1 = bounds(block.polygon)
            nearby = [(i, b) for i, b in valid
                      if bounds(b.polygon)[0] < x1 + 0.03
                      and bounds(b.polygon)[2] > x0 - 0.03
                      and bounds(b.polygon)[1] < y1 + 0.015
                      and bounds(b.polygon)[3] > y0 - 0.015]
            shards = [b for _, b in nearby if len(b.tokens) <= 1
                      and bounds(b.polygon)[3] - bounds(b.polygon)[1] < 0.008]
            ratio = sum(len(b.tokens) for _, b in nearby) / max(len(nearby), 1)
            if len(shards) >= 6 and len(shards) / len(nearby) >= 0.6 and ratio <= 2:
                issues.append(StructuralIssue(
                    page.number, "block_fragmentation",
                    "Critical clause is surrounded by microscopic layout blocks.", index, ratio, 2))
    return issues


def require_sound_structure(pages: list[Page]) -> None:
    issues = inspect_structure(pages)
    if issues:
        raise LowConfidenceGateException(issues)


def require_verified_document(document: dict | None) -> None:
    if document and document.get("state") == NEEDS_VERIFICATION:
        raise LowConfidenceGateException([
            StructuralIssue(**issue) for issue in document.get("issues", [])
        ])


def held_contracts(contracts: list[dict], customer_id: str | None = None) -> list[dict]:
    return [c for c in contracts if c.get("verification_state") == NEEDS_VERIFICATION
            and (customer_id is None or c.get("verification_scope") == "account"
                 or c.get("customer_id") == customer_id
                 or customer_id in c.get("verification_customer_ids", []))]


def require_verified_contracts(contracts: list[dict], customer_id: str | None = None) -> None:
    held = held_contracts(contracts, customer_id)
    if held:
        raise LowConfidenceGateException([
            StructuralIssue(**issue) for contract in held
            for issue in (contract.get("structural_verification") or {}).get("issues", [])
        ])
