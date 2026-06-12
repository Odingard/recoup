from dataclasses import dataclass
from typing import List, Optional
from datetime import date

@dataclass
class NormalizedCustomer:
    customer_id: str
    customer_name: str
    email: Optional[str] = None

@dataclass
class NormalizedSubscription:
    subscription_id: str
    customer_id: str
    plan_name: str
    status: str
    start_date: date
    end_date: Optional[date] = None

@dataclass
class NormalizedUsage:
    customer_id: str
    period: str  # e.g., "2026-06"
    total_units: int

@dataclass
class NormalizedInvoice:
    invoice_id: str
    customer_id: str
    period: str
    amount_billed: float
    status: str
    line_items: List[dict]
