from abc import ABC, abstractmethod
from typing import List, Optional
from .models import NormalizedCustomer, NormalizedSubscription, NormalizedUsage, NormalizedInvoice

class BillingProvider(ABC):
    """Abstract base class for all billing integrations (CSV, API/MCP, Webhook DB)."""

    @abstractmethod
    def get_customer(self, customer_id: str) -> Optional[NormalizedCustomer]:
        pass

    @abstractmethod
    def list_customers(self) -> List[NormalizedCustomer]:
        pass

    @abstractmethod
    def get_subscriptions(self, customer_id: str) -> List[NormalizedSubscription]:
        pass

    @abstractmethod
    def get_usage(self, customer_id: str, period: str) -> NormalizedUsage:
        pass

    @abstractmethod
    def get_invoices(self, customer_id: str, period: str) -> List[NormalizedInvoice]:
        pass
