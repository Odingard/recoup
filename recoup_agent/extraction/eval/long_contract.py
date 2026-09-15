from __future__ import annotations

import random


def build_long_contract(seed: int, pages: int = 220) -> tuple[str, list[dict]]:
    rng = random.Random(seed)
    planted = {
        "minimum": rng.randint(8, pages - 35),
        "overage": rng.randint(25, pages - 30),
        "discount": rng.randint(45, pages - 25),
        "escalator": rng.randint(60, pages - 20),
        "term": rng.randint(75, pages - 15),
        "seats": rng.randint(90, pages - 10),
    }
    expected = [
        {"term_type": "committed_minimum", "value": 50000.0, "effective_date": "2026-01-01", "page": planted["minimum"]},
        {"term_type": "overage_tier", "value": 3.5, "tier_up_to": 10000.0, "page": planted["overage"]},
        {"term_type": "overage_tier", "value": 2.5, "tier_up_to": None, "page": planted["overage"]},
        {"term_type": "discount", "value": 2000.0, "start_date": "2026-01-01", "end_date": "2026-03-31", "page": planted["discount"]},
        {"term_type": "escalator", "value": 0.04, "effective_date": "2026-05-01", "page": planted["escalator"]},
        {"term_type": "term_start", "effective_date": "2026-01-01", "page": planted["term"]},
        {"term_type": "term_end", "effective_date": "2028-12-31", "page": planted["term"]},
        {"term_type": "auto_renewal", "value": 12.0, "page": planted["term"]},
        {"term_type": "renewal_notice_days", "value": 90.0, "page": planted["term"]},
        {"term_type": "committed_seats", "value": 125.0, "page": planted["seats"]},
        {"term_type": "seat_price", "value": 120.0, "page": planted["seats"]},
        {"term_type": "committed_minimum", "value": 42000.0, "effective_date": "2026-07-01", "page": pages - 4},
    ]
    pages_text = []
    definitions = planted["minimum"]
    for number in range(1, pages + 1):
        paragraphs = [
            f"Section {number}. General provisions. This synthesized agreement page contains boilerplate operational language for customer Acme Corp.",
            "Confidentiality, indemnity, service levels, audit rights, and limitation of liability apply as described elsewhere in this agreement.",
        ]
        if number == definitions:
            paragraphs.append('Definitions: "Committed Volume" means the monthly units purchased. "Platform Fee" means the committed monthly fee described below.')
            paragraphs.append('Section 3.1. The Customer shall pay no less than a $50,000 monthly Platform Fee effective 2026-01-01.')
            paragraphs.append('The annual cap is not to exceed $100,000 in any contract year; this cap is not a minimum commitment.')
        if number == planted["overage"]:
            paragraphs.append('Exhibit A §2. Included usage is 10,000 units. Units in excess of that amount are billed in tiers: $3.50 per unit for the first 10,000 additional units, then $2.50 per unit thereafter.')
        if number == planted["discount"]:
            paragraphs.append('Section 5.2. A $2,000 promotional discount applies from 2026-01-01 and expires 2026-03-31.')
        if number == planted["escalator"]:
            paragraphs.append('Section 5.3. The Platform Fee increases by a 4% escalator on each anniversary effective 2026-05-01.')
        if number == planted["term"]:
            paragraphs.append('Section 1. Initial Term begins 2026-01-01 and ends 2028-12-31. The agreement renews automatically for 12 months with 90 days notice to terminate.')
        if number == planted["seats"]:
            paragraphs.append('Exhibit B §2. The order form commits to 125 seats at a seat price of $120 per seat per month.')
        if number == pages - 4:
            paragraphs.append('Amendment No. 1. Effective 2026-07-01, the committed monthly Platform Fee is reduced to $42,000.')
        pages_text.append("\n".join(paragraphs))
    return "\n\f\n".join(pages_text), expected


def build_long_contract_bundle(seed: int, pages: int = 220) -> tuple[list[tuple[str, str]], list[dict]]:
    """Same planted terms as build_long_contract, but emitted as three separate
    documents (master, exhibit, amendment) for document-graph evaluation."""
    text, expected = build_long_contract(seed, pages)
    page_texts = text.split("\n\f\n")
    amendment_page = None
    exhibit_page = None
    for i, page in enumerate(page_texts):
        if "Amendment No. 1" in page:
            amendment_page = i
        if "Exhibit B" in page:
            exhibit_page = i
    amendment_text = page_texts.pop(amendment_page)
    exhibit_text = page_texts.pop(exhibit_page)
    master_text = "\n\f\n".join(page_texts)
    docs = [
        ("master-agreement.txt", "Master Subscription Agreement\n" + master_text),
        ("exhibit-b.txt", "Exhibit B\n" + exhibit_text),
        ("amendment-1.txt", amendment_text),
    ]
    return docs, expected
