import csv
import json
from typing import List, Optional
from datetime import datetime
from .models import NormalizedCustomer, NormalizedSubscription, NormalizedUsage, NormalizedInvoice
from .provider import BillingProvider

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
        return [
            NormalizedCustomer(
                customer_id=row['customer_id'],
                customer_name=row['customer_name'],
                email=row.get('email')
            ) for row in data
        ]

    def get_subscriptions(self, customer_id: str) -> List[NormalizedSubscription]:
        data = self._load_json(self.subscriptions_file)
        subs = []
        for row in data:
            if row.get('customer_id') == customer_id:
                start = datetime.strptime(row['start_date'], '%Y-%m-%d').date()
                end = datetime.strptime(row['end_date'], '%Y-%m-%d').date() if row.get('end_date') else None
                subs.append(NormalizedSubscription(
                    subscription_id=row.get('subscription_id', 'unknown'),
                    customer_id=customer_id,
                    plan_name=row.get('plan_name', 'default'),
                    status=row.get('status', 'active'),
                    start_date=start,
                    end_date=end
                ))
        return subs

    def get_usage(self, customer_id: str, period: str) -> NormalizedUsage:
        data = self._load_json(self.usage_file)
        for row in data:
            if row.get('customer_id') == customer_id and row.get('period') == period:
                return NormalizedUsage(
                    customer_id=customer_id,
                    period=period,
                    total_units=int(row.get('total_units', 0))
                )
        return NormalizedUsage(customer_id=customer_id, period=period, total_units=0)

    def get_invoices(self, customer_id: str, period: str) -> List[NormalizedInvoice]:
        data = self._load_json(self.invoices_file)
        invoices = []
        for row in data:
            if row.get('customer_id') == customer_id and row.get('period') == period:
                line_items = row.get('line_items')
                if isinstance(line_items, str):
                    line_items = json.loads(line_items)
                invoices.append(NormalizedInvoice(
                    invoice_id=row.get('invoice_id', 'unknown'),
                    customer_id=customer_id,
                    period=period,
                    amount_billed=float(row.get('amount_billed', 0.0)),
                    status=row.get('status', 'paid'),
                    line_items=line_items or []
                ))
        return invoices
