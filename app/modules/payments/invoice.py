"""Professional PDF invoice / payment receipt for one payment (reportlab, built-in Helvetica: real bold, no font files).

Layout follows a classic Indian tax-invoice sheet: logo + issuer block, coloured information band, Bill to / Paid to
columns, coloured item table with a sub-total strip, payment details on the left and the totals on the right,
amount in words, thank-you line. Odeslo colours, the real logo and a faint logo watermark on every page.
"""

import io
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core import pdf_brand as brand
from app.core.pdf_brand import BODY, GREEN, INK, LILAC, LILAC_LINE, LINE, MUTED, RED, VIOLET, VIOLET_DARK


def esc(value) -> str:
    """Text for a Paragraph. Helvetica prints Latin letters only; anything else (e.g. Devanagari) becomes '?'."""
    return escape(str(value if value is not None else "-").encode("cp1252", "replace").decode("cp1252"))


def inr(value: float) -> str:
    """Rs. 1,40,000.00 (Indian digit grouping)."""
    neg = value < 0
    whole, frac = f"{abs(value):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts) + "," + tail
    return f"{'-' if neg else ''}Rs. {whole}.{frac}"


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
         "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_1000(n: int) -> str:
    out = []
    if n >= 100:
        out.append(_ONES[n // 100] + " Hundred")
        n %= 100
    if n >= 20:
        out.append(_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else ""))
    elif n:
        out.append(_ONES[n])
    return " ".join(out)


def amount_in_words(value: float) -> str:
    """INR Two Thousand Three Hundred Only (Indian system: thousand, lakh, crore)."""
    rupees, paise = int(value), round((value - int(value)) * 100)
    if rupees == 0 and paise == 0:
        return "INR Zero Only"
    parts = []
    for div, name in ((10_000_000, "Crore"), (100_000, "Lakh"), (1000, "Thousand")):
        if rupees >= div:
            parts.append(f"{_below_1000(rupees // div)} {name}")
            rupees %= div
    if rupees:
        parts.append(_below_1000(rupees))
    text = " ".join(parts) if parts else "Zero"
    if paise:
        text += f" and {_below_1000(paise)} Paise"
    return f"INR {text} Only"


def _dt(v, fmt="%d %b %Y, %I:%M %p") -> str:
    if not v:
        return "-"
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    return v.strftime(fmt).replace(" 0", " ")


def build_invoice_pdf(d: dict) -> bytes:
    st = lambda name, **kw: ParagraphStyle(name, fontName=kw.pop("font", "Helvetica"), textColor=kw.pop("color", INK), **kw)  # noqa: E731
    body = st("body", fontSize=9.5, leading=14)
    right = st("right", fontSize=9.5, leading=14, alignment=TA_RIGHT)
    right_bold = st("rb", fontSize=9.5, leading=14, alignment=TA_RIGHT, font="Helvetica-Bold")
    h2 = st("h2", fontSize=13, leading=17)
    th = st("th", fontSize=9.5, leading=12, color=colors.white)
    th_r = st("thr", fontSize=9.5, leading=12, color=colors.white, alignment=TA_RIGHT)

    paid = d["status"] == "success"
    status_label, status_color = ("PAID", GREEN) if paid else (d["status"].upper(), RED if d["status"] == "failed" else MUTED)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm, bottomMargin=16 * mm,
                            title=f"Invoice {d['invoice_no']}", author="Odeslo", subject="Payment invoice")
    W = A4[0] - 28 * mm
    story: list = []
    payer, soc, to, g = d["payer"] or {}, d["society"], d["paid_to"], d["gateway"]
    meta = d["purpose"]["meta"]
    net = d["amount"] - d["refunded_total"]

    # ---- 1. title row
    top = Table([[Paragraph("INVOICE / RECEIPT", st("t1", fontSize=14, leading=18)),
                  Paragraph(f'<font color="{status_color.hexval()}">{status_label}</font>', st("t2", fontSize=14, leading=18, alignment=TA_RIGHT, font="Helvetica-Bold"))]],
                colWidths=[W * 0.6, W * 0.4])
    top.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story += [top, Spacer(1, 3 * mm)]

    # ---- 2. logo + issuer block
    sub_style = st("iss", fontSize=9.5, leading=14, color=BODY)
    issuer = [
        Paragraph("Odeslo", st("iss0", fontSize=24, leading=28, font="Helvetica-Bold", color=VIOLET_DARK)),
        Paragraph("Society management platform", sub_style),
        Paragraph(f"On behalf of <b>{esc(soc['name'])}</b> ({esc(soc['code'])})", sub_style),
        Paragraph(esc(f"{soc['address']}, {soc['area']}, {soc['city']}, {soc['state']} {soc['pincode']}"), sub_style),
    ]
    if soc.get("registration_number"):
        issuer.append(Paragraph(f"Registration no.: {esc(soc['registration_number'])}", sub_style))
    logo_box = Table([[brand.logo(24)]], colWidths=[28 * mm], rowHeights=[28 * mm])
    logo_box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), LILAC), ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    head = Table([[logo_box, issuer]], colWidths=[34 * mm, W - 34 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [head, Spacer(1, 4 * mm), HRFlowable(width="100%", thickness=3, color=VIOLET, spaceAfter=0)]

    # ---- 3. information band
    band = Table([[Paragraph(f"Invoice No:&nbsp;&nbsp;<b>{esc(d['invoice_no'])}</b>", body),
                   Paragraph(f"Transaction:&nbsp;&nbsp;<b>{esc(d['txn_id'])}</b>", st("bm", fontSize=9.5, leading=14, alignment=1)),
                   Paragraph(f"Date:&nbsp;&nbsp;<b>{_dt(d['paid_at'] or d['created_at'], '%d %b %Y')}</b>", right)]],
                 colWidths=[W * 0.36, W * 0.32, W * 0.32])
    band.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), LILAC), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                              ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7)]))
    story += [band, Spacer(1, 5 * mm)]

    # ---- 4. bill to / paid to
    billed = [Paragraph("BILL TO", h2), Spacer(1, 1.5 * mm), Paragraph(f"<b>{esc(payer.get('name') or '-')}</b>", body)]
    if payer.get("unit"):
        billed.append(Paragraph(f"Flat: {esc(payer['unit'])}", body))
    if payer.get("resident_type"):
        billed.append(Paragraph(f"Resident type: {esc(payer['resident_type'].title())}", body))
    if payer.get("mobile"):
        billed.append(Paragraph(f"Mobile: {esc(payer['mobile'])}", body))
    if payer.get("email"):
        billed.append(Paragraph(f"Email: {esc(payer['email'])}", body))
    paid_to = [Paragraph("PAID TO", h2), Spacer(1, 1.5 * mm), Paragraph(f"<b>{esc(to['name'])}</b>", body), Paragraph(esc(to["detail"]), body),
               Paragraph(f"Through society: {esc(soc['name'])}", body)]
    cols = Table([[billed, paid_to]], colWidths=[W * 0.5, W * 0.5])
    cols.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 6)]))
    story += [cols, Spacer(1, 6 * mm)]

    # ---- 5. items
    rows = [[Paragraph("Items", th), Paragraph("Quantity", th_r), Paragraph("Rate", th_r), Paragraph("Amount", th_r)]]
    for ln in d["purpose"]["lines"]:
        desc = f"<b>{esc(ln['description'])}</b>" + (f'<br/><font size="8" color="#6e6e78">{esc(ln["detail"])}</font>' if ln.get("detail") else "")
        rows.append([Paragraph(desc, body), Paragraph(esc(ln["qty"]), right), Paragraph(inr(ln["rate"]), right), Paragraph(inr(ln["amount"]), right)])
    items_total = sum(ln["amount"] for ln in d["purpose"]["lines"])
    items = Table(rows, colWidths=[W * 0.46, W * 0.14, W * 0.2, W * 0.2], repeatRows=1)
    items.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), VIOLET), ("TOPPADDING", (0, 0), (-1, 0), 7), ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, LINE), ("TOPPADDING", (0, 1), (-1, -1), 9), ("BOTTOMPADDING", (0, 1), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
    ]))
    sub = Table([[Paragraph("Sub Total", body), "", "", Paragraph(f"<b>{inr(items_total)}</b>", right)]], colWidths=[W * 0.46, W * 0.14, W * 0.2, W * 0.2])
    sub.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 2.2, VIOLET), ("LINEBELOW", (0, 0), (-1, 0), 2.2, VIOLET), ("TOPPADDING", (0, 0), (-1, -1), 6),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7)]))
    story += [items, Spacer(1, 5 * mm), sub, Spacer(1, 6 * mm)]

    # ---- 6. payment details (left) and totals (right)
    detail = [("Payment mode", d["mode_label"]), ("Status", status_label.title()), ("Paid on", _dt(d["paid_at"]))]
    if g.get("name"):
        detail.append(("Gateway", g["name"].title()))
    if g.get("payment_id"):
        detail.append(("Gateway payment ID", g["payment_id"]))
    if g.get("order_id"):
        detail.append(("Gateway order ID", g["order_id"]))
    if g.get("reference_no"):
        detail.append(("Reference no.", g["reference_no"]))
    if d["recorded_by"]:
        detail.append(("Recorded by", f"{d['recorded_by']['name']} ({d['recorded_by']['type'].replace('_', ' ')})"))
    if d["ref_type"] == "booking":
        detail.append(("Booking", f"{meta['booking_no']} ({str(meta['booking_status']).replace('_', ' ')})"))
    else:
        detail.append(("Bill", f"{meta['bill_no']} ({str(meta['bill_status']).replace('_', ' ')})"))
    if d.get("notes"):
        detail.append(("Notes", d["notes"]))
    if d.get("failure_reason"):
        detail.append(("Failure reason", d["failure_reason"]))
    left = [Paragraph("Payment details", h2), Spacer(1, 1.5 * mm)] + [Paragraph(f'<font color="#6e6e78">{esc(k)}:</font>&nbsp;&nbsp;{esc(v)}', body) for k, v in detail]

    tot = []
    if d["ref_type"] == "maintenance":
        tot.append(("Bill total", inr(meta["bill_total"]), False))
        tot.append(("Paid in this payment", inr(d["amount"]), True))
    else:
        tot.append(("Total amount", inr(d["amount"]), True))
    if d["refunded_total"]:
        tot.append(("Refunded", f"-{inr(d['refunded_total'])}", False))
        tot.append(("Net paid", inr(net), True))
    tt = Table([[Paragraph(k, right_bold if b else right), Paragraph(v, right_bold if b else right)] for k, v, b in tot], colWidths=[W * 0.2, W * 0.2])
    tt.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LINEBELOW", (0, 0), (-1, -2), 0.5, LILAC_LINE),
                            ("BACKGROUND", (0, -1), (-1, -1), LILAC), ("LINEABOVE", (0, -1), (-1, -1), 1.2, VIOLET), ("LINEBELOW", (0, -1), (-1, -1), 1.2, VIOLET)]))
    words = Paragraph(f'<font color="#6e6e78">Total amount in words</font><br/><b>{esc(amount_in_words(net))}</b>', st("words", fontSize=9.5, leading=14, alignment=TA_RIGHT))
    right_col = Table([[tt], [Spacer(1, 4 * mm)], [words]], colWidths=[W * 0.4])
    right_col.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    bottom = Table([[left, right_col]], colWidths=[W * 0.58, W * 0.42])
    bottom.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [KeepTogether([bottom])]

    # ---- 7. refunds (if any)
    if d["refunds"]:
        rr = [[Paragraph("Refund", th), Paragraph("Status", th), Paragraph("Date", th), Paragraph("Amount", th_r)]]
        for r in d["refunds"]:
            rr.append([Paragraph(esc(r["refund_no"] + (f" - {r['reason']}" if r.get("reason") else "")), body), Paragraph(esc(r["status"].title()), body),
                       Paragraph(_dt(r["completed_at"] or r["created_at"]), body), Paragraph(inr(r["amount"]), right)])
        rt = Table(rr, colWidths=[W * 0.46, W * 0.14, W * 0.24, W * 0.16])
        rt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), VIOLET_DARK), ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE), ("TOPPADDING", (0, 0), (-1, -1), 5),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("LEFTPADDING", (0, 0), (-1, -1), 7)]))
        story += [Spacer(1, 7 * mm), KeepTogether([Paragraph("Refunds on this payment", h2), Spacer(1, 2 * mm), rt])]

    # ---- 8. thank you
    story += [Spacer(1, 12 * mm),
              Paragraph("Thank you for your payment!", st("ty", fontSize=11, leading=15, alignment=TA_RIGHT, color=VIOLET_DARK, font="Helvetica-Bold")),
              Paragraph("Authorised by Odeslo (computer generated invoice, no signature needed)", st("ty2", fontSize=8.5, leading=12, alignment=TA_RIGHT, color=MUTED))]

    def page(canvas, doc_):
        brand.watermark(canvas, A4)
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.line(14 * mm, 12 * mm, A4[0] - 14 * mm, 12 * mm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(14 * mm, 8 * mm, "Odeslo - society management platform")
        canvas.drawRightString(A4[0] - 14 * mm, 8 * mm, f"{d['invoice_no']}  |  Page {doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=page, onLaterPages=page)
    return buf.getvalue()
