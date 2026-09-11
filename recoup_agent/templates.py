"""Native billing-system CSV export templates (header + 2 example rows).

Each row set is loadable by ingest_csv's column aliases, so these double as
round-trip fixtures.
"""
from __future__ import annotations

TEMPLATES: dict[str, dict[str, dict]] = {
    "quickbooks": {
        "invoices": {
            "header": ["Txn Date", "Doc Number", "Customer", "Memo/Description", "Amount", "Product/Service"],
            "rows": [
                ["06/01/2026", "INV-1001", "Acme Corp", "Platform subscription June", "4800.00", "SaaS Platform"],
                ["06/01/2026", "INV-1001", "Acme Corp", "Sales tax", "384.00", "Tax"],
            ],
        },
        "usage": {
            "header": ["Txn Date", "Customer", "Qty", "Item"],
            "rows": [
                ["06/30/2026", "Acme Corp", "14200", "API calls"],
                ["06/30/2026", "Acme Corp", "14200", "API calls"],
            ],
        },
    },
    "xero": {
        "invoices": {
            "header": ["InvoiceNumber", "ContactName", "InvoiceDate", "LineAmount", "Description"],
            "rows": [
                ["INV-2001", "Acme Corp", "2026-06-01", "4800.00", "Platform subscription June"],
                ["INV-2001", "Acme Corp", "2026-06-01", "-480.00", "Launch promo discount"],
            ],
        },
        "usage": {
            "header": ["ContactName", "Date", "Quantity", "Description"],
            "rows": [
                ["Acme Corp", "2026-06-30", "14200", "API calls"],
                ["Acme Corp", "2026-07-31", "15500", "API calls"],
            ],
        },
    },
    "stripe": {
        "invoices": {
            "header": ["invoice_id", "customer", "period_start", "amount", "description", "status"],
            "rows": [
                ["in_3001", "Acme Corp", "2026-06-01", "4800.00", "Platform subscription June", "paid"],
                ["in_3001", "Acme Corp", "2026-06-01", "-480.00", "Launch promo discount", "paid"],
            ],
        },
        "usage": {
            "header": ["customer", "period", "units", "metric"],
            "rows": [
                ["Acme Corp", "2026-06", "14200", "api_calls"],
                ["Acme Corp", "2026-07", "15500", "api_calls"],
            ],
        },
    },
}
