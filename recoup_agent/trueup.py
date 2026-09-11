"""True-up pack: the collection letter + schedule an operator sends to their
customer. No Recoup branding, no fee footer, no dates other than billing
periods."""
from __future__ import annotations

import html
import io

from .report import _LEAK_LABELS

_COLLECTIBLE_STATUSES = {"approved", "invoiced", "disputed"}


def build_trueup(customer_id: str, findings: list[dict], contracts: list[dict],
                 *, sender: str | None = None, include_open: bool = False) -> dict | None:
    """Select collectible findings for one customer and build the pack."""
    statuses = _COLLECTIBLE_STATUSES | ({"open"} if include_open else set())
    selected = [f for f in findings
                if f.get("customer_id") == customer_id and (f.get("status") or "open") in statuses]
    if not selected:
        return None

    names = {c.get("customer_id"): c.get("customer_name", c.get("customer_id")) for c in contracts}
    customer_name = next((f.get("customer_name") for f in selected if f.get("customer_name")),
                         names.get(customer_id, customer_id))

    rows = []
    for f in sorted(selected, key=lambda x: (x.get("period", ""), x.get("type", ""))):
        rows.append({
            "period": f.get("period", ""),
            "leak_type": _LEAK_LABELS.get(f.get("type"), f.get("type", "")),
            "amount": round(float(f.get("monthly_recoverable") or 0), 2),
            "clause_text": f.get("clause_text") or f.get("provenance") or "",
            "math": f.get("math") or f.get("detail", ""),
            "term": f.get("term") or f.get("clause_ref") or "",
            "status": f.get("status", "open"),
            "invoice_ref": (f.get("corrective_invoice") or {}).get("ref") or None,
        })
    total = round(sum(r["amount"] for r in rows), 2)
    periods = sorted({r["period"] for r in rows if r["period"]})
    periods_str = ", ".join(periods[:-1]) + f" and {periods[-1]}" if len(periods) > 1 else (periods[0] if periods else "the billing periods covered")

    bullets = "\n".join(
        f"- {r['period']}: {r['leak_type']} — ${r['amount']:,.2f} ({r['math']})" for r in rows)

    letter = (
        f"To the accounts payable team at {customer_name},\n\n"
        f"During a routine reconciliation of our agreement against invoices issued for "
        f"{periods_str}, we identified the following amounts that were billed below the "
        f"contracted terms.\n\n"
        f"{bullets}\n\n"
        f"The total outstanding under the agreement is ${total:,.2f}. The contract clauses "
        f"supporting each item are reproduced in the attached schedule.\n\n"
        f"Please issue payment against the corrective invoice, or contact us within 15 days "
        f"if you believe any item is incorrect.\n\n"
        f"Regards,\n{sender or '[Your company]'}"
    )

    return {
        "customer_id": customer_id,
        "customer_name": customer_name,
        "sender": sender,
        "total": total,
        "rows": rows,
        "periods": periods,
        "letter": letter,
    }


def render_trueup_pdf(pack: dict) -> bytes:
    """Page 1 = letter, page 2+ = schedule. Footer: 'Schedule n of m' only."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle, PageBreak)
    from reportlab.pdfgen import canvas as canvas_module

    styles = getSampleStyleSheet()
    h2 = styles["Heading2"]
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=11, leading=16)
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=10,
                           textColor=colors.HexColor("#555555"))
    cell = ParagraphStyle("cell", parent=body, fontSize=9, leading=12)

    def P(text, style=cell):
        return Paragraph(html.escape("" if text is None else str(text)), style)

    class _NumberedCanvas(canvas_module.Canvas):
        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved)
            for state in self._saved:
                self.__dict__.update(state)
                self.setFont("Helvetica", 8)
                self.setFillColor(colors.HexColor("#666666"))
                self.drawRightString(letter[0] - 0.85 * inch, 0.45 * inch,
                                     f"Schedule {self._pageNumber} of {total}")
                canvas_module.Canvas.showPage(self)
            canvas_module.Canvas.save(self)

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved = []

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            leftMargin=0.85 * inch, rightMargin=0.85 * inch,
                            topMargin=0.85 * inch, bottomMargin=0.9 * inch)

    story = []
    for para in pack["letter"].split("\n\n"):
        story.append(Paragraph("<br/>".join(html.escape(para).split("\n")), body))
        story.append(Spacer(1, 0.12 * inch))
    story.append(PageBreak())

    story.append(Paragraph("Schedule of amounts due", h2))
    story.append(Spacer(1, 0.15 * inch))
    data = [[P("Period"), P("Item"), P("Amount"), P("Contract clause (quoted)"), P("Calculation")]]
    for row in pack["rows"]:
        data.append([P(row["period"]), P(row["leak_type"]), P(f"${row['amount']:,.2f}"),
                     P(row["clause_text"], small), P(row["math"], small)])
    data.append([P(""), P("Total"), P(f"${pack['total']:,.2f}"), P(""), P("")])
    t = Table(data, colWidths=[0.7 * inch, 1.2 * inch, 0.85 * inch, 2.3 * inch, 1.8 * inch],
              repeatRows=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f4f4f8")),
        ("FONTNAME", (0, -1), (2, -1), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(t)

    doc.build(story, canvasmaker=_NumberedCanvas)
    return buffer.getvalue()
