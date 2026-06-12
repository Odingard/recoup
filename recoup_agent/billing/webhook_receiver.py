from fastapi import FastAPI, Request
from pydantic import BaseModel
from typing import List
from .models import NormalizedUsage, NormalizedInvoice

app = FastAPI()

class WebhookPayload(BaseModel):
    event_type: str
    data: dict

@app.post("/webhook/billing")
async def receive_billing_event(payload: WebhookPayload):
    """
    Secure REST API endpoint for enterprise companies to push billing events
    (e.g., "usage recorded", "invoice generated") directly into our normalized schema.
    """
    # Example logic to ingest usage
    if payload.event_type == "usage.recorded":
        usage = NormalizedUsage(**payload.data)
        # Save to database (not implemented in this stub)
        return {"status": "success", "recorded_units": usage.total_units}
    
    elif payload.event_type == "invoice.generated":
        invoice = NormalizedInvoice(**payload.data)
        # Save to database (not implemented in this stub)
        return {"status": "success", "recorded_invoice": invoice.invoice_id}
        
    return {"status": "ignored", "message": "unhandled event type"}

# Run this file with `uvicorn recoup_agent.billing.webhook_receiver:app --reload`
