"""Generates Recoup's synthetic customer book and contract corpus.

Three hand-built anchor customers carry the reproducible demo numbers:
  - Acme Corp   -> $14,200/mo of leakage (the headline demo number)
  - Globex Inc  -> clean (proves Recoup doesn't just flag everything)
  - Initech LLC -> $1,000/mo (a missed annual escalator)

Plus ~25 templated contracts so the Vertex AI Search corpus is worth searching.
The templated customers have NO usage/invoice records, so they never produce
findings -- they exist only to give the RAG index volume.

No real customer data. Run:  python -m recoup_agent.synthetic_data
"""
import json
import random
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
CORPUS_DIR = DATA_DIR / "corpus"

# --- Anchor contracts (carry the demo numbers; do not change the figures) ---
ANCHORS = [
    {
        "customer_id": "acme", "customer_name": "Acme Corp", "contract_id": "C-ACME-2025",
        "effective_date": "2025-01-01",
        "committed_minimum_monthly": 50000, "included_units": 10000, "overage_rate": 3.50,
        "annual_escalator_pct": 0.0, "escalator_effective_date": None,
        "discounts": [
            {"name": "Q1 2025 onboarding promo", "type": "percent", "value": 0.05,
             "applies_to": "base", "expires": "2026-03-31"}
        ],
        "clauses": {
            "committed_minimum": "Section 3.1 - Customer commits to a minimum monthly platform fee of $50,000, billed regardless of actual usage.",
            "overage": "Section 4.2 - Usage above the included 10,000 monthly units is billed at $3.50 per additional unit.",
            "discount": "Section 7.4 - A 5% onboarding discount applies to the base fee through 2026-03-31; standard rates resume thereafter.",
            "escalator": "Section 5.3 - Annual fees increase by the stated escalator on each contract anniversary.",
        },
    },
    {
        "customer_id": "globex", "customer_name": "Globex Inc", "contract_id": "C-GLOBEX-2025",
        "effective_date": "2025-06-01",
        "committed_minimum_monthly": 20000, "included_units": 10000, "overage_rate": 2.00,
        "annual_escalator_pct": 0.0, "escalator_effective_date": None, "discounts": [],
        "clauses": {
            "committed_minimum": "Section 3.1 - Minimum monthly fee of $20,000.",
            "overage": "Section 4.2 - Usage above 10,000 units billed at $2.00 per unit.",
            "discount": "", "escalator": "",
        },
    },
    {
        "customer_id": "initech", "customer_name": "Initech LLC", "contract_id": "C-INITECH-2024",
        "effective_date": "2024-05-01",
        "committed_minimum_monthly": 25000, "included_units": 5000, "overage_rate": 5.00,
        "annual_escalator_pct": 0.04, "escalator_effective_date": "2026-05-01", "discounts": [],
        "clauses": {
            "committed_minimum": "Section 3.1 - Minimum monthly fee of $25,000.",
            "overage": "Section 4.2 - Usage above 5,000 units billed at $5.00 per unit.",
            "discount": "",
            "escalator": "Section 5.3 - Fees increase 4% annually effective each May 1.",
        },
    },
]

USAGE = [
    {"customer_id": "acme", "period": "2026-06", "units": 11200},
    {"customer_id": "globex", "period": "2026-06", "units": 8000},
    {"customer_id": "initech", "period": "2026-06", "units": 5000},
]

INVOICES = [
    {"customer_id": "acme", "period": "2026-06", "base_charge": 42000, "overage_charge": 0,
     "discounts_applied": [{"name": "Q1 2025 onboarding promo", "amount": 2000}]},
    {"customer_id": "globex", "period": "2026-06", "base_charge": 20000, "overage_charge": 0,
     "discounts_applied": []},
    {"customer_id": "initech", "period": "2026-06", "base_charge": 25000, "overage_charge": 0,
     "discounts_applied": []},
]

_NAMES = [
    "Soylent Systems", "Hooli", "Pied Piper", "Vehement Capital", "Massive Dynamic",
    "Stark Industries", "Wonka Industries", "Cyberdyne", "Tyrell Corp", "Wayne Enterprises",
    "Nakatomi Trading", "Oscorp", "Gekko and Co", "Bluth Company", "Dunder Mifflin",
    "Prestige Worldwide", "Vandelay Industries", "Sterling Cooper", "Aperture Labs",
    "Monarch Sciences", "Encom", "Weyland Corp", "Virtucon", "Abstergo", "Initrode",
]


def _templated(i: int, name: str) -> dict:
    rng = random.Random(1000 + i)
    cid = name.lower().replace(" ", "_")
    minimum = rng.randrange(10000, 80001, 5000)
    included = rng.randrange(2000, 20001, 1000)
    rate = round(rng.uniform(1.0, 6.0), 2)
    return {
        "customer_id": cid, "customer_name": name,
        "contract_id": f"C-{cid.upper()[:10]}-2025", "effective_date": "2025-01-01",
        "committed_minimum_monthly": minimum, "included_units": included, "overage_rate": rate,
        "annual_escalator_pct": rng.choice([0.0, 0.03, 0.04, 0.05]),
        "escalator_effective_date": rng.choice([None, "2026-01-01", "2026-05-01"]),
        "discounts": [],
        "clauses": {
            "committed_minimum": f"Section 3.1 - {name} commits to a minimum monthly platform fee of ${minimum:,}, billed regardless of actual usage.",
            "overage": f"Section 4.2 - Usage above the included {included:,} monthly units is billed at ${rate:.2f} per additional unit.",
            "discount": "",
            "escalator": "Section 5.3 - Annual fees increase by the stated escalator on each contract anniversary.",
        },
    }


def all_contracts() -> list:
    return ANCHORS + [_templated(i, n) for i, n in enumerate(_NAMES)]


def build_corpus(contracts: list) -> None:
    """Write one .txt per contract for indexing in Vertex AI Search."""
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    for c in contracts:
        lines = [f"CONTRACT {c['contract_id']} - {c['customer_name']}", ""]
        for key in ("committed_minimum", "overage", "discount", "escalator"):
            txt = c["clauses"].get(key)
            if txt:
                lines.append(txt)
        (CORPUS_DIR / f"{c['customer_id']}.txt").write_text("\n".join(lines))


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    contracts = all_contracts()
    for name, obj in [("contracts.json", contracts), ("usage.json", USAGE), ("invoices.json", INVOICES)]:
        with open(DATA_DIR / name, "w") as fh:
            json.dump(obj, fh, indent=2)
    build_corpus(contracts)
    print(f"Wrote {len(contracts)} contracts + corpus to {DATA_DIR}")


if __name__ == "__main__":
    main()
