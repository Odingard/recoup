"""Generates Recoup's synthetic customer book.

Three customers, deliberately seeded so the demo is reproducible:
  - Acme Corp   -> $14,200/mo of leakage (the headline demo number)
  - Globex Inc  -> clean (proves Recoup doesn't just flag everything)
  - Initech LLC -> $1,000/mo (a missed annual escalator on a different rule)

No real customer data. Run:  python -m recoup_agent.synthetic_data
"""
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

CONTRACTS = [
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


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name, obj in [("contracts.json", CONTRACTS), ("usage.json", USAGE), ("invoices.json", INVOICES)]:
        with open(DATA_DIR / name, "w") as fh:
            json.dump(obj, fh, indent=2)
    print(f"Wrote synthetic data to {DATA_DIR}")


if __name__ == "__main__":
    main()
