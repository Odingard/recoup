"""Customer-facing audit report: headline recoverable total, per-customer
findings with contract clause grounding and math, plus a clearly separated
needs-review section. Rendered as self-contained HTML or a PDF.
No dates appear anywhere in the report."""
from __future__ import annotations

import html
import io

from .money import quantize

FEE_PCT = 0.20

_LEAK_LABELS = {
    "unenforced_minimum": "Unenforced minimum",
    "unbilled_overage": "Unbilled overage",
    "expired_discount": "Expired discount",
    "missed_escalator": "Missed escalator",
}

FOOTER = ("Recoup is paid 20% of dollars actually recovered — nothing on "
          "findings that are not collected. No upfront fee.")


def _group_math(findings: list[dict], total: float) -> str:
    months = len(findings)
    maths = [f.get("math") or f.get("detail", "") for f in findings]
    if months == 1:
        return maths[0]
    amounts = [f["monthly_recoverable"] for f in findings]
    if len(set(amounts)) == 1:
        return f"{maths[0]} × {months} months = ${total:,.0f}"
    parts = " + ".join(f"${a:,.0f}" for a in amounts)
    return f"{' ; '.join(maths)} → {parts} = ${total:,.0f} across {months} months"


def build_report(findings_by_period: dict[str, list[dict]],
                 needs_review: list[dict],
                 contracts: list[dict]) -> dict:
    """Group findings across periods by (customer_id, type)."""
    grouped: dict[tuple[str, str], list[dict]] = {}
    names: dict[str, str] = {}
    for period in sorted(findings_by_period):
        for f in findings_by_period[period]:
            grouped.setdefault((f["customer_id"], f.get("type", "other")), []).append(f)
            names[f["customer_id"]] = f.get("customer_name", f["customer_id"])

    customers: dict[str, dict] = {}
    for (cid, ftype), fs in grouped.items():
        total = quantize(sum(f["monthly_recoverable"] for f in fs))
        row = {
            "amount": total,
            "leak_type": _LEAK_LABELS.get(ftype, ftype),
            "clause_text": fs[0].get("clause_text") or fs[0].get("detail", ""),
            "math": _group_math(fs, total),
            "assumption": next((f.get("assumption") for f in fs if f.get("assumption")), None),
            "months": len(fs),
        }
        entry = customers.setdefault(cid, {
            "customer_id": cid,
            "customer_name": names.get(cid, cid),
            "total": 0.0,
            "rows": [],
        })
        entry["total"] = quantize(entry["total"] + total)
        entry["rows"].append(row)

    review_items = [{
        "customer_name": item.get("customer_name"),
        "term": item.get("term"),
        "reason": item.get("reason"),
        "suggested_action": item.get("suggested_action", ""),
    } for item in needs_review]

    contract_names = {c["customer_id"]: c.get("customer_name", c["customer_id"]) for c in contracts}
    for item in needs_review:
        cid = item.get("customer_id")
        if cid and cid not in customers:
            customers[cid] = {
                "customer_id": cid,
                "customer_name": item.get("customer_name") or contract_names.get(cid, cid),
                "total": 0.0,
                "rows": [],
            }

    ordered = sorted(customers.values(), key=lambda c: c["total"], reverse=True)
    return {
        "headline_total": quantize(sum(c["total"] for c in ordered)),
        "finding_count": sum(len(c["rows"]) for c in ordered),
        "customers": ordered,
        "needs_review": review_items,
        "fee_pct": FEE_PCT,
    }


def _e(value) -> str:
    return html.escape("" if value is None else str(value))


def render_html(report: dict) -> str:
    pages = []
    pages.append(
        '<section class="page cover">'
        f'<h1>Recoup found ${report["headline_total"]:,.0f} in recoverable revenue '
        f'across {report["finding_count"]} findings</h1>'
        '<p class="sub">Revenue leakage audit — every figure below is tied to a contract clause.</p>'
        "</section>"
    )
    for cust in report["customers"]:
        if cust["rows"]:
            rows = "".join(
                "<tr>"
                f"<td>{_e(cust['customer_name'])}</td>"
                f"<td class=\"num\">${row['amount']:,.0f}</td>"
                f"<td>{_e(row['leak_type'])}</td>"
                f"<td class=\"clause\">{_e(row['clause_text'])}</td>"
                f"<td>{_e(row['math'])}"
                + (f'<br><span class="assumption">{_e(row["assumption"])}</span>' if row.get("assumption") else "")
                + "</td></tr>"
                for row in cust["rows"]
            )
            body = (
                '<table><thead><tr><th>Customer</th><th>Amount</th><th>Leak type</th>'
                "<th>Contract clause (exact text)</th><th>Math</th></tr></thead>"
                f"<tbody>{rows}</tbody></table>"
            )
        else:
            body = '<p class="none">No confident findings — see Needs review.</p>'
        pages.append(
            '<section class="page">'
            f"<h2>{_e(cust['customer_name'])} — ${cust['total']:,.0f} recoverable</h2>"
            f"{body}</section>"
        )

    if report["needs_review"]:
        review_rows = "".join(
            "<tr>"
            f"<td>{_e(item['customer_name'])}</td>"
            f"<td>{_e(item['term'])}</td>"
            f"<td>{_e(item['reason'])}</td>"
            f"<td>{_e(item['suggested_action'])}</td>"
            "</tr>"
            for item in report["needs_review"]
        )
        review_body = (
            '<table><thead><tr><th>Customer</th><th>Term</th><th>Reason</th>'
            f"<th>Suggested action</th></tr></thead><tbody>{review_rows}</tbody></table>"
        )
    else:
        review_body = "<p>Nothing needs review.</p>"
    pages.append(
        '<section class="page needs-review"><h2>Needs review</h2>'
        "<p>The engine was not confident about these items. "
        "They are NOT included in the headline number.</p>"
        f"{review_body}</section>"
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Recoup audit report</title>
<style>
body {{ font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; color: #1a1a2e; margin: 0; }}
.page {{ padding: 48px 56px 80px; max-width: 1000px; margin: 0 auto; }}
.cover h1 {{ font-size: 34px; line-height: 1.25; margin-top: 20vh; }}
.cover .sub {{ color: #555; font-size: 16px; }}
h2 {{ font-size: 22px; border-bottom: 2px solid #1a1a2e; padding-bottom: 8px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th, td {{ border: 1px solid #ccc; padding: 8px 10px; text-align: left; vertical-align: top; }}
th {{ background: #f4f4f8; }}
td.num {{ white-space: nowrap; font-weight: 600; }}
td.clause {{ font-size: 12px; color: #333; }}
.assumption {{ font-size: 11px; font-style: italic; color: #666; }}
.none {{ color: #555; font-style: italic; }}
.needs-review h2 {{ border-color: #b45309; }}
.footer {{ position: fixed; bottom: 0; left: 0; right: 0; padding: 10px 56px;
  font-size: 11px; color: #666; border-top: 1px solid #ddd; background: #fff; }}
@media print {{ .page {{ page-break-after: always; }} }}
</style></head><body>
{''.join(pages)}
<div class="footer">{_e(FOOTER)}</div>
</body></html>"""


def render_pdf(report: dict) -> bytes:
    """PDF version of the audit report via reportlab platypus."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle, PageBreak)

    styles = getSampleStyleSheet()
    h1 = styles["Title"]
    h2 = styles["Heading2"]
    body = styles["BodyText"]
    small = ParagraphStyle("small", parent=body, fontSize=8, textColor=colors.HexColor("#555555"))
    cell = ParagraphStyle("cell", parent=body, fontSize=9, leading=12)
    cell_italic = ParagraphStyle("cell_italic", parent=cell, fontName="Helvetica-Oblique",
                                 fontSize=8, textColor=colors.HexColor("#666666"))

    def P(text, style=cell):
        return Paragraph(html.escape("" if text is None else str(text)), style)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                            topMargin=0.75 * inch, bottomMargin=0.9 * inch)
    story = [
        Spacer(1, 2 * inch),
        Paragraph(
            f"Recoup found ${report['headline_total']:,.0f} in recoverable revenue "
            f"across {report['finding_count']} findings", h1),
        Spacer(1, 0.3 * inch),
        Paragraph("Revenue leakage audit — every figure below is tied to a contract clause.", body),
        PageBreak(),
    ]

    def table(data, widths):
        t = Table(data, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f4f4f8")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        return t

    col_w = [1.1 * inch, 0.8 * inch, 1.0 * inch, 2.4 * inch, 1.7 * inch]
    for cust in report["customers"]:
        story.append(Paragraph(f"{cust['customer_name']} — ${cust['total']:,.0f} recoverable", h2))
        story.append(Spacer(1, 0.15 * inch))
        if cust["rows"]:
            data = [[P("Customer"), P("Amount"), P("Leak type"),
                     P("Contract clause (exact text)"), P("Math")]]
            for row in cust["rows"]:
                math_cell = [P(row["math"])]
                if row.get("assumption"):
                    math_cell.append(P(row["assumption"], cell_italic))
                data.append([P(cust["customer_name"]), P(f"${row['amount']:,.0f}"),
                             P(row["leak_type"]), P(row["clause_text"], small), math_cell])
            story.append(table(data, col_w))
        else:
            story.append(Paragraph("No confident findings — see Needs review.", body))
        story.append(PageBreak())

    story.append(Paragraph("Needs review", h2))
    story.append(Paragraph("The engine was not confident about these items. "
                           "They are NOT included in the headline number.", body))
    story.append(Spacer(1, 0.15 * inch))
    if report["needs_review"]:
        data = [[P("Customer"), P("Term"), P("Reason"), P("Suggested action")]]
        for item in report["needs_review"]:
            data.append([P(item["customer_name"]), P(item["term"]),
                         P(item["reason"]), P(item["suggested_action"])])
        story.append(table(data, [1.3 * inch, 1.2 * inch, 2.5 * inch, 2.0 * inch]))
    else:
        story.append(Paragraph("Nothing needs review.", body))

    def _footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawString(0.75 * inch, 0.45 * inch, FOOTER)
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()
