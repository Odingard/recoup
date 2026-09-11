# Billing & usage CSV export templates

Recoup accepts CSV exports from QuickBooks, Xero, and Stripe. Download a
ready-to-fill template from the app (Step 1 upload panel) or directly:

- `GET /api/templates/{system}/{kind}.csv` — `system` ∈ `quickbooks|xero|stripe`,
  `kind` ∈ `invoices|usage`.

## Which export to run

- **QuickBooks**: Reports → "Sales by Customer Detail" (or an Invoice List
  export) for invoices; a usage/metering export for usage. Typically exports
  include `Txn Date`, `Doc Number`, `Customer`, `Memo/Description`, `Amount`.
- **Xero**: Accounting → Reports → "Receivable Invoice Detail" for invoices
  (typically `InvoiceNumber`, `ContactName`, `InvoiceDate`, `LineAmount`,
  `Description`); Business → Invoices → Export also works.
- **Stripe**: Billing → Invoices → Export → "Invoice line items" (typically
  `invoice_id`, `customer`, `period_start`, `amount`, `description`); for usage,
  a metering export with `customer`, `period`, `units`, `metric`.

## Column mapping

Headers are normalized to `lowercase_underscores` before matching, so
"Txn Date" and "txn_date" are the same column. If both a `subtotal` and a
`total` column exist, `subtotal` wins (Recoup wants the pre-tax line amount;
`tax`/`tax_amount` columns are ignored automatically — and a negative "Sales
tax" *line* is classified as tax and excluded from the billed base).

| Recoup role | QuickBooks | Xero | Stripe |
|---|---|---|---|
| customer | `Customer` | `ContactName` | `customer` |
| invoice_id | `Doc Number` | `InvoiceNumber` | `invoice_id` |
| period_start | `Txn Date` | `InvoiceDate` | `period_start` |
| amount | `Amount` / `LineAmount` | `LineAmount` | `amount` |
| description | `Memo/Description` | `Description` | `description` |
| units (usage) | `Qty` | `Quantity` | `units` |

## Notes

- Invoices need at least `customer` + `amount` + a period column (`period`,
  `period_start`, `Txn Date`, `InvoiceDate`, …). Usage needs `customer` +
  `units` + a period column.
- Dates: `2026-06-01`, `06/01/2026`, `Jun 1 2026`, `2026-06` all parse.
- Negative lines are classified: contract-discount name → discount; otherwise
  credit/refund. A "credit" line is excluded from `base_charge` and never
  counted as an applied discount.
- A `status` column of `void`, `draft`, or `uncollectible` skips the row and
  raises a review item.
- Uploads can also be bundled as a `.zip` (contracts + CSVs together) via the
  bulk dropzone or `POST /api/ingest/bulk`.
