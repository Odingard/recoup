import csv
import json
from typing import List, Optional
from datetime import datetime
from .models import NormalizedCustomer, NormalizedSubscription, NormalizedUsage, NormalizedInvoice
from .provider import BillingProvider
from ..ingest_csv import IngestError, DATE_FORMATS, resolve_columns, parse_period

class CSVBillingProvider(BillingProvider):
    """A generic billing provider that reads from CSV/JSON exports."""

    def __init__(self, customers_file: str, subscriptions_file: str, usage_file: str, invoices_file: str):
        self.customers_file = customers_file
        self.subscriptions_file = subscriptions_file
        self.usage_file = usage_file
        self.invoices_file = invoices_file

    def _load_json(self, file_path: str) -> List[dict]:
        # For simplicity in this mock, we support JSON arrays or CSVs
        if file_path.endswith('.json'):
            with open(file_path, 'r') as f:
                return json.load(f)
        else:
            with open(file_path, 'r') as f:
                return list(csv.DictReader(f))

    def _resolve(self, file_path: str, data: List[dict], required: list, optional: list) -> dict:
        header = list(data[0].keys()) if data else []
        if not header:
            raise IngestError(f"{file_path}: no data rows")
        return resolve_columns(header, required, optional, where=file_path)

    @staticmethod
    def _parse_date(value: str, *, where: str):
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except (ValueError, AttributeError):
                continue
        raise IngestError(f"{where}: unrecognised date '{value}'")

    def get_customer(self, customer_id: str) -> Optional[NormalizedCustomer]:
        data = self._load_json(self.customers_file)
        for row in data:
            if row.get('customer_id') == customer_id:
                return NormalizedCustomer(
                    customer_id=row['customer_id'],
                    customer_name=row['customer_name'],
                    email=row.get('email')
                )
        return None

    def list_customers(self) -> List[NormalizedCustomer]:
        data = self._load_json(self.customers_file)
        if data and ('customer_id' not in data[0] or 'customer_name' not in data[0]):
            raise IngestError(
                f"{self.customers_file}: expected columns customer_id/customer_name; "
                f"found {list(data[0].keys())}")
        return [
            NormalizedCustomer(
                customer_id=row['customer_id'],
                customer_name=row['customer_name'],
                email=row.get('email')
            ) for row in data
        ]

    def get_subscriptions(self, customer_id: str) -> List[NormalizedSubscription]:
        data = self._load_json(self.subscriptions_file)
        if data and 'start_date' not in data[0]:
            cols = self._resolve(self.subscriptions_file, data,
                                 required=["customer", "period_start"], optional=["status"])
            start_col, status_col = cols["period_start"], cols.get("status")
        else:
            start_col, status_col = 'start_date', 'status'
        subs = []
        for i, row in enumerate(data, start=2):
            if row.get('customer_id') == customer_id:
                start = self._parse_date(row[start_col], where=f"{self.subscriptions_file} row {i}")
                end = self._parse_date(row['end_date'], where=f"{self.subscriptions_file} row {i}") \
                    if row.get('end_date') else None
                subs.append(NormalizedSubscription(
                    subscription_id=row.get('subscription_id', 'unknown'),
                    customer_id=customer_id,
                    plan_name=row.get('plan_name', 'default'),
                    status=row.get(status_col, 'active'),
                    start_date=start,
                    end_date=end
                ))
        return subs

    def get_usage(self, customer_id: str, period: str) -> NormalizedUsage:
        data = self._load_json(self.usage_file)
        cols = self._resolve(self.usage_file, data,
                             required=["customer", "units"], optional=["period", "period_start"])
        cust_col, units_col = cols["customer"], cols["units"]
        period_col = cols.get("period") or cols.get("period_start")
        for i, row in enumerate(data, start=2):
            if row.get(cust_col) == customer_id:
                row_period = parse_period(row[period_col], where=f"{self.usage_file} row {i}") \
                    if period_col else period
                if row_period == period:
                    return NormalizedUsage(
                        customer_id=customer_id,
                        period=period,
                        total_units=int(float(str(row.get(units_col, 0)).replace(',', '')))
                    )
        return NormalizedUsage(customer_id=customer_id, period=period, total_units=0)

    def get_invoices(self, customer_id: str, period: str) -> List[NormalizedInvoice]:
        data = self._load_json(self.invoices_file)
        cols = self._resolve(self.invoices_file, data,
                             required=["customer", "amount"],
                             optional=["period", "period_start", "invoice_id", "status"])
        cust_col, amount_col = cols["customer"], cols["amount"]
        period_col = cols.get("period") or cols.get("period_start")
        invoices = []
        for i, row in enumerate(data, start=2):
            if row.get(cust_col) == customer_id:
                row_period = parse_period(row[period_col], where=f"{self.invoices_file} row {i}") \
                    if period_col else period
                if row_period != period:
                    continue
                line_items = row.get('line_items')
                if isinstance(line_items, str):
                    line_items = json.loads(line_items)
                raw_amount = str(row.get(amount_col, 0.0)).replace('$', '').replace(',', '').strip()
                try:
                    amount_billed = float(raw_amount)
                except ValueError:
                    raise IngestError(
                        f"{self.invoices_file} row {i}: unparseable amount '{row.get(amount_col)}'")
                invoices.append(NormalizedInvoice(
                    invoice_id=row.get(cols.get("invoice_id", ''), 'unknown'),
                    customer_id=customer_id,
                    period=period,
                    amount_billed=amount_billed,
                    status=row.get(cols.get("status", ''), 'paid'),
                    line_items=line_items or []
                ))
        return invoices
