"""Excel and PDF versions of a report snapshot (see reports.build_report): same data, same filters, tables with
real dates and numbers (Excel) / proper formatted tables and charts (PDF)."""

import io
from datetime import date, datetime

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, PieChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

RUPEE = '"₹"#,##0.00'
INT = "#,##0"
PCT = '0.0"%"'
DATE = "dd-mmm-yyyy"
DATETIME = "dd-mmm-yyyy hh:mm"

INK = "1C1C22"
VIOLET = "7B6EF6"
PANEL = "F6F6F8"
_thin = Side(style="thin", color="E7E7EC")
BORDER = Border(bottom=_thin)


def _title(v: str) -> str:
    return v.replace("_", " ").title() if v else ""


def _dt(v):
    return datetime.fromisoformat(v) if isinstance(v, str) else v


def _filters_lines(r: dict) -> list[tuple[str, str]]:
    f = r["filters"]
    return [
        ("Period", f"{date.fromisoformat(f['date_from']):%d %b %Y} to {date.fromisoformat(f['date_to']):%d %b %Y}"),
        ("Societies", ", ".join(f["societies"])),
        ("Deal category", _title(f["category"]) if f["category"] else "All"),
        ("Payment mode", _title(f["mode"]) if f["mode"] else "All"),
        ("Grouped by", f["group_by"].title()),
        ("Generated", f"{datetime.fromisoformat(r['generated_at']):%d %b %Y, %I:%M %p}"),
    ]


# ------------------------------------------------------------------ Excel
def _sheet_table(ws, row: int, headers: list[str], rows: list[list], formats: list[str | None], totals: dict[int, str] | None = None,
                 widths: list[int] | None = None, title: str | None = None) -> tuple[int, int]:
    """Write a styled table; returns (header_row, last_row). `totals` = {column index: number format} to sum under the table."""
    if title:
        ws.cell(row=row, column=1, value=title).font = Font(bold=True, size=12, color=INK)
        row += 1
    head_fill = PatternFill("solid", fgColor=INK)
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=row, column=c, value=h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(horizontal="left" if formats[c - 1] is None else "right", vertical="center", wrap_text=True)
    header_row = row
    for i, values in enumerate(rows, 1):
        for c, v in enumerate(values, 1):
            cell = ws.cell(row=row + i, column=c, value=v)
            if formats[c - 1]:
                cell.number_format = formats[c - 1]
                cell.alignment = Alignment(horizontal="right")
            cell.border = BORDER
            if i % 2 == 0:
                cell.fill = PatternFill("solid", fgColor=PANEL)
    last = row + len(rows)
    if totals and rows:
        t = last + 1
        ws.cell(row=t, column=1, value="Total").font = Font(bold=True)
        for c, fmt in totals.items():
            col = get_column_letter(c)
            cell = ws.cell(row=t, column=c, value=f"=SUM({col}{row + 1}:{col}{last})")
            cell.number_format = fmt
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="right")
        for c in range(1, len(headers) + 1):
            ws.cell(row=t, column=c).border = Border(top=Side(style="medium", color=INK))
        last = t
    if widths:
        for c, w in enumerate(widths, 1):
            cur = ws.column_dimensions[get_column_letter(c)].width or 0
            ws.column_dimensions[get_column_letter(c)].width = max(cur, w)
    return header_row, last


def to_xlsx(r: dict) -> bytes:
    wb = Workbook()
    k, f = r["kpis"], r["filters"]
    gb = f["group_by"]
    period_fmt = "mmm yyyy" if gb == "month" else DATE

    # ---- Summary
    ws = wb.active
    ws.title = "Summary"
    ws.sheet_properties.tabColor = VIOLET
    ws["A1"] = "Odeslo platform report"
    ws["A1"].font = Font(bold=True, size=18, color=INK)
    ws["A2"] = "Society management platform"
    ws["A2"].font = Font(color="6E6E78")
    row = 4
    for label, value in _filters_lines(r):
        ws.cell(row=row, column=1, value=label).font = Font(bold=True, color="6E6E78")
        ws.cell(row=row, column=2, value=value)
        row += 1
    kpi_rows = [
        ["Gross revenue", k["gross_revenue"]], ["  Group deals", k["booking_revenue"]], ["  Maintenance", k["maintenance_revenue"]],
        ["Refunded", k["refunded"]], ["Net revenue", k["net_revenue"]], ["Successful payments", k["payments"]], ["Failed payments", k["failed_payments"]],
        ["Average payment", k["avg_payment"]], ["Bookings", k["bookings"]], ["Units booked", k["units_booked"]], ["Deals created", k["deals_created"]],
        ["Deals cancelled", k["deals_cancelled"]], ["Maintenance billed", k["maintenance_billed"]], ["Maintenance collected", k["maintenance_collected"]],
        ["Collection rate", k["collection_rate"]], ["Residents joined", k["residents_joined"]], ["Complaints raised", k["complaints"]],
        ["Complaints resolved", k["complaints_resolved"]], ["Resolution rate", k["resolution_rate"]], ["Jobs", k["jobs"]],
        ["Jobs completed", k["jobs_completed"]], ["Average vendor rating", k["avg_rating"] if k["avg_rating"] is not None else "-"],
    ]
    money_rows = {"Gross revenue", "  Group deals", "  Maintenance", "Refunded", "Net revenue", "Average payment", "Maintenance billed", "Maintenance collected"}
    pct_rows = {"Collection rate", "Resolution rate"}
    h, last = _sheet_table(ws, row + 1, ["Metric", "Value"], kpi_rows, [None, None], widths=[30, 22], title="Key numbers")
    for i, (label, _v) in enumerate(kpi_rows, 1):
        c = ws.cell(row=h + i, column=2)
        c.number_format = RUPEE if label in money_rows else PCT if label in pct_rows else INT if isinstance(c.value, int) else "0.00"
        c.alignment = Alignment(horizontal="right")
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 34

    # ---- Revenue by period
    wr = wb.create_sheet("Revenue")
    wr.sheet_properties.tabColor = "2F7A34"
    ts = r["timeseries"]
    rev_rows = [[date.fromisoformat(t["date"]), t["booking"], t["maintenance"], t["refunds"], t["net"], t["payments"]] for t in ts]
    rh, rl = _sheet_table(wr, 1, ["Period", "Group deals", "Maintenance", "Refunds", "Net", "Payments"], rev_rows,
                          [period_fmt, RUPEE, RUPEE, RUPEE, RUPEE, INT], totals={2: RUPEE, 3: RUPEE, 4: RUPEE, 5: RUPEE, 6: INT}, widths=[16, 18, 18, 16, 18, 12])
    for i in range(len(rev_rows)):
        wr.cell(row=rh + 1 + i, column=1).alignment = Alignment(horizontal="left")
    wr.freeze_panes = wr.cell(row=rh + 1, column=1)
    if rev_rows:
        chart = LineChart()
        chart.title = "Revenue over time"
        chart.height, chart.width = 8.5, 20
        data = Reference(wr, min_col=2, max_col=5, min_row=rh, max_row=rh + len(rev_rows))
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(Reference(wr, min_col=1, min_row=rh + 1, max_row=rh + len(rev_rows)))
        ws.add_chart(chart, "D4")

    # ---- Payments (every transaction)
    wp = wb.create_sheet("Payments")
    wp.sheet_properties.tabColor = "3558C4"
    pay_rows = [[_dt(p["paid_at"]), p["txn_id"], p["society"], p["resident"], _title(p["type"]), p["description"], _title(p["mode"]), p["amount"], p["reference"]] for p in r["payments"]]
    ph, pl = _sheet_table(wp, 1, ["Paid on", "Transaction ID", "Society", "Resident", "Type", "For", "Mode", "Amount", "Reference"], pay_rows,
                          [DATETIME, None, None, None, None, None, None, RUPEE, None], totals={8: RUPEE}, widths=[20, 24, 26, 22, 14, 34, 14, 16, 26])
    wp.freeze_panes = wp.cell(row=ph + 1, column=1)
    if pay_rows:
        wp.auto_filter.ref = f"A{ph}:I{ph + len(pay_rows)}"
    if r["payments_truncated"]:
        wp.cell(row=pl + 2, column=1, value=f"Showing the latest {len(pay_rows)} of {r['payments_total']} payments. Narrow the dates to see the rest.")

    # ---- Societies
    wsoc = wb.create_sheet("Societies")
    wsoc.sheet_properties.tabColor = "B5561E"
    soc_rows = [[s["society"], s["code"], s["city"], _title(s["status"]), s["units"], s["residents_joined"], s["deals"], s["bookings"], s["units_booked"],
                 s["booking_revenue"], s["maintenance_revenue"], s["total_revenue"], s["refunded"], s["net_revenue"], s["billed"], s["collected"],
                 s["collection_rate"], s["complaints"], s["complaints_resolved"], s["jobs"]] for s in r["societies"]]
    sh, sl = _sheet_table(wsoc, 1, ["Society", "Code", "City", "Status", "Units", "Residents joined", "Deals", "Bookings", "Units booked", "Group deal revenue",
                                    "Maintenance revenue", "Total revenue", "Refunded", "Net revenue", "Maintenance billed", "Maintenance collected",
                                    "Collection %", "Complaints", "Resolved", "Jobs"], soc_rows,
                          [None, None, None, None, INT, INT, INT, INT, INT, RUPEE, RUPEE, RUPEE, RUPEE, RUPEE, RUPEE, RUPEE, PCT, INT, INT, INT],
                          totals={5: INT, 6: INT, 7: INT, 8: INT, 9: INT, 10: RUPEE, 11: RUPEE, 12: RUPEE, 13: RUPEE, 14: RUPEE, 15: RUPEE, 16: RUPEE, 18: INT, 19: INT, 20: INT},
                          widths=[28, 10, 16, 12, 9, 11, 8, 10, 11, 18, 18, 16, 14, 16, 18, 18, 12, 11, 10, 8])
    wsoc.freeze_panes = wsoc.cell(row=sh + 1, column=2)
    if soc_rows:
        wsoc.auto_filter.ref = f"A{sh}:T{sh + len(soc_rows)}"
        bar = BarChart()
        bar.type = "bar"
        bar.title = "Revenue by society"
        bar.height, bar.width = 9, 20
        top = min(len(soc_rows), 10)
        bar.add_data(Reference(wsoc, min_col=10, max_col=11, min_row=sh, max_row=sh + top), titles_from_data=True)
        bar.set_categories(Reference(wsoc, min_col=1, min_row=sh + 1, max_row=sh + top))
        bar.grouping, bar.overlap = "stacked", 100
        ws.add_chart(bar, "D22")

    # ---- Bookings & deals
    wb_ = wb.create_sheet("Bookings & deals")
    bh, bl = _sheet_table(wb_, 1, ["Booking status", "Bookings"], [[_title(x["name"]), x["value"]] for x in r["booking_status"]], [None, INT], totals={2: INT}, widths=[26, 14], title="Bookings by status")
    if r["booking_status"]:
        pie = PieChart()
        pie.title = "Bookings by status"
        pie.height, pie.width = 8.5, 11
        pie.add_data(Reference(wb_, min_col=2, min_row=bh, max_row=bh + len(r["booking_status"])), titles_from_data=True)
        pie.set_categories(Reference(wb_, min_col=1, min_row=bh + 1, max_row=bh + len(r["booking_status"])))
        ws.add_chart(pie, "N4")
    nxt = bl + 3
    ch, cl = _sheet_table(wb_, nxt, ["Deal category", "Deals", "Bookings", "Units", "Paid bookings value"],
                          [[_title(x["category"]), x["deals"], x["bookings"], x["units"], x["gmv"]] for x in r["deals_by_category"]], [None, INT, INT, INT, RUPEE],
                          totals={2: INT, 3: INT, 4: INT, 5: RUPEE}, widths=[26, 12, 12, 12, 22], title="Deals by category")
    nxt = cl + 3
    _sheet_table(wb_, nxt, ["Deal", "Society", "Category", "Units", "Price / unit", "Paid value", "Status"],
                 [[x["deal"], x["society"], _title(x["category"]), x["units"], x["price_per_unit"], x["gmv"], _title(x["status"])] for x in r["top_deals"]],
                 [None, None, None, INT, RUPEE, RUPEE, None], widths=[34, 26, 16, 10, 16, 16, 14], title="Top deals by paid value")

    # ---- Maintenance
    wm = wb.create_sheet("Maintenance")
    _sheet_table(wm, 1, ["Period", "Bills", "Paid bills", "Overdue bills", "Billed", "Collected", "Collection %"],
                 [[x["period"], x["bills"], x["paid"], x["overdue"], x["billed"], x["collected"], x["rate"]] for x in r["maintenance_by_period"]],
                 [None, INT, INT, INT, RUPEE, RUPEE, PCT], totals={2: INT, 3: INT, 4: INT, 5: RUPEE, 6: RUPEE}, widths=[14, 10, 12, 14, 18, 18, 14])

    # ---- Residents
    wres = wb.create_sheet("Residents")
    _, rl2 = _sheet_table(wres, 1, ["Resident type", "Joined"], [[_title(x["name"]), x["value"]] for x in r["residents_by_type"]], [None, INT], totals={2: INT}, widths=[24, 12], title="Joined by type")
    _, rl3 = _sheet_table(wres, rl2 + 3, ["Status", "Residents"], [[_title(x["name"]), x["value"]] for x in r["residents_by_status"]], [None, INT], totals={2: INT}, title="Joined by status")
    _sheet_table(wres, rl3 + 3, ["Period", "Residents joined"], [[date.fromisoformat(t["date"]), t["residents"]] for t in ts], [period_fmt, INT], totals={2: INT}, title="Joined over time")

    # ---- Complaints
    wc = wb.create_sheet("Complaints")
    _, c1 = _sheet_table(wc, 1, ["Category", "Complaints"], [[_title(x["name"]), x["value"]] for x in r["complaints_by_category"]], [None, INT], totals={2: INT}, widths=[24, 14], title="By category")
    _, c2 = _sheet_table(wc, c1 + 3, ["Status", "Complaints"], [[_title(x["name"]), x["value"]] for x in r["complaints_by_status"]], [None, INT], totals={2: INT}, title="By status")
    _, c3 = _sheet_table(wc, c2 + 3, ["Priority", "Complaints"], [[_title(x["name"]), x["value"]] for x in r["complaints_by_priority"]], [None, INT], totals={2: INT}, title="By priority")
    _sheet_table(wc, c3 + 3, ["Period", "Raised", "Resolved"], [[date.fromisoformat(t["date"]), t["complaints"], t["resolved"]] for t in ts], [period_fmt, INT, INT], totals={2: INT, 3: INT}, title="Over time")

    # ---- Jobs & vendors
    wj = wb.create_sheet("Jobs & vendors")
    _, j1 = _sheet_table(wj, 1, ["Job status", "Jobs"], [[_title(x["name"]), x["value"]] for x in r["jobs_by_status"]], [None, INT], totals={2: INT}, widths=[26, 12, 12, 14, 14], title="Jobs by status")
    _, j2 = _sheet_table(wj, j1 + 3, ["Rating", "Jobs"], [[x["name"], x["value"]] for x in r["rating_distribution"]], [None, INT], totals={2: INT}, title="Ratings")
    _sheet_table(wj, j2 + 3, ["Vendor", "Jobs", "Completed", "Average rating", "Payout"],
                 [[v["vendor"], v["jobs"], v["completed"], v["avg_rating"], v["payout"]] for v in r["vendors"]], [None, INT, INT, "0.00", RUPEE],
                 totals={2: INT, 3: INT, 5: RUPEE}, title="Vendors by jobs")

    # ---- Refunds
    wrf = wb.create_sheet("Refunds")
    _sheet_table(wrf, 1, ["Refund status", "Refunds"], [[_title(x["name"]), x["value"]] for x in r["refund_status"]], [None, INT], totals={2: INT}, widths=[24, 12])

    for sheet in wb.worksheets:
        sheet.sheet_view.showGridLines = False
        sheet.sheet_view.zoomScale = 100
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ PDF
def to_pdf(r: dict) -> bytes:
    from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart
    from reportlab.graphics.charts.piecharts import Pie
    from reportlab.graphics.shapes import Drawing, String
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    def esc(v) -> str:
        return escape(str(v if v is not None else "-").encode("cp1252", "replace").decode("cp1252"))

    def rs(v: float) -> str:
        whole, frac = f"{abs(v):.2f}".split(".")
        if len(whole) > 3:
            head, tail, parts = whole[:-3], whole[-3:], []
            while len(head) > 2:
                parts.insert(0, head[-2:])
                head = head[:-2]
            if head:
                parts.insert(0, head)
            whole = ",".join(parts) + "," + tail
        return f"{'-' if v < 0 else ''}Rs. {whole}.{frac}"

    from app.core import pdf_brand as brand

    ink, muted, line, panel, violet = colors.HexColor("#" + INK), colors.HexColor("#6e6e78"), colors.HexColor("#e7e7ec"), colors.HexColor("#" + PANEL), colors.HexColor("#" + VIOLET)
    palette = [colors.HexColor(c) for c in ("#7b6ef6", "#2f9e44", "#3558c4", "#e8a33d", "#c2334d", "#6e6e78", "#14b8a6", "#b5561e")]
    st = lambda n, **kw: ParagraphStyle(n, fontName=kw.pop("font", "Helvetica"), textColor=kw.pop("color", ink), **kw)  # noqa: E731
    body, small = st("b", fontSize=8.5, leading=11), st("s", fontSize=7.5, leading=10, color=muted)
    h2 = st("h2", fontSize=12, leading=15, font="Helvetica-Bold")
    th, thr = st("th", fontSize=7.5, leading=9, color=colors.white, font="Helvetica-Bold"), st("thr", fontSize=7.5, leading=9, color=colors.white, font="Helvetica-Bold", alignment=TA_RIGHT)
    num = st("n", fontSize=8, leading=10, alignment=TA_RIGHT)

    page = landscape(A4)
    W = page[0] - 28 * mm
    buf = io.BytesIO()
    f, k = r["filters"], r["kpis"]
    doc = SimpleDocTemplate(buf, pagesize=page, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=16 * mm,
                            title="Odeslo platform report", author="Odeslo")
    story: list = []

    def table(headers, rows, widths, right_cols=(), total=None, total_w=None):
        data = [[Paragraph(esc(h), thr if i in right_cols else th) for i, h in enumerate(headers)]]
        for row in rows:
            data.append([Paragraph(esc(v), num if i in right_cols else body) for i, v in enumerate(row)])
        if total:
            data.append([Paragraph(f"<b>{esc(v)}</b>", num if i in right_cols else body) for i, v in enumerate(total)])
        t = Table(data, colWidths=[(total_w or W) * w for w in widths], repeatRows=1)
        style = [("BACKGROUND", (0, 0), (-1, 0), violet), ("LINEBELOW", (0, 1), (-1, -1), 0.4, line), ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                 ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5), ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
        for i in range(2, len(data), 2):
            style.append(("BACKGROUND", (0, i), (-1, i), panel))
        if total:
            style += [("LINEABOVE", (0, -1), (-1, -1), 0.8, ink), ("BACKGROUND", (0, -1), (-1, -1), brand.LILAC)]
        t.setStyle(TableStyle(style))
        return t

    # ---- header
    brand_cell = Table([[brand.logo(13), Paragraph('<font name="Helvetica-Bold" size="20" color="#5446d9">Odeslo</font><br/><font size="8" color="#6e6e78">Platform report</font>', st("hd", fontSize=20, leading=22))]],
                       colWidths=[17 * mm, 80 * mm])
    brand_cell.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    head = Table([[brand_cell,
                   Paragraph(f'<font name="Helvetica-Bold" size="12">{date.fromisoformat(f["date_from"]):%d %b %Y} to {date.fromisoformat(f["date_to"]):%d %b %Y}</font><br/>'
                             f'<font size="8" color="#6e6e78">Generated {datetime.fromisoformat(r["generated_at"]):%d %b %Y, %I:%M %p}</font>', st("hr", fontSize=10, leading=14, alignment=TA_RIGHT))]],
                 colWidths=[W * 0.5, W * 0.5])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [head, Spacer(1, 3 * mm), HRFlowable(width="100%", thickness=3, color=violet, spaceAfter=3 * mm)]
    filt = " | ".join(f"<b>{esc(a)}:</b> {esc(b)}" for a, b in _filters_lines(r)[1:5])
    story += [Paragraph(filt, small), Spacer(1, 4 * mm)]

    # ---- KPI tiles
    tiles = [("Gross revenue", rs(k["gross_revenue"])), ("Net revenue", rs(k["net_revenue"])), ("Refunded", rs(k["refunded"])), ("Payments", f"{k['payments']:,}"),
             ("Bookings", f"{k['bookings']:,}"), ("Maintenance collected", rs(k["maintenance_collected"])), ("Collection rate", f"{k['collection_rate']}%"),
             ("Residents joined", f"{k['residents_joined']:,}"), ("Complaints resolved", f"{k['complaints_resolved']} / {k['complaints']}"),
             ("Jobs completed", f"{k['jobs_completed']} / {k['jobs']}"), ("Avg vendor rating", f"{k['avg_rating']}" if k["avg_rating"] is not None else "-"), ("Societies", f"{k['societies']}")]
    cells = [[Paragraph(f'<font size="7" color="#6e6e78">{esc(a)}</font><br/><font name="Helvetica-Bold" size="11">{esc(b)}</font>', st("t", fontSize=11, leading=14)) for a, b in tiles[i:i + 4]] for i in range(0, 12, 4)]
    kt = Table(cells, colWidths=[W / 4] * 4)
    kt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), brand.LILAC), ("BOX", (0, 0), (-1, -1), 0.5, brand.LILAC_LINE), ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.white),
                            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LEFTPADDING", (0, 0), (-1, -1), 9)]))
    story += [kt, Spacer(1, 3 * mm)]

    # ---- charts
    ts = r["timeseries"]
    if ts:
        shown = ts if len(ts) <= 36 else ts[-36:]
        d = Drawing(W, 128)
        bc = VerticalBarChart()
        bc.x, bc.y, bc.width, bc.height = 45, 26, W - 60, 76
        bc.data = [[t["booking"] for t in shown], [t["maintenance"] for t in shown]]
        bc.categoryAxis.categoryNames = [t["label"] for t in shown]
        bc.categoryAxis.labels.fontSize = 6
        bc.categoryAxis.labels.angle = 40 if len(shown) > 12 else 0
        bc.categoryAxis.labels.boxAnchor = "ne" if len(shown) > 12 else "n"
        bc.valueAxis.labels.fontSize = 6.5
        bc.valueAxis.labelTextFormat = lambda v: (f"{v / 1000:.1f}k" if v >= 1000 else f"{v:,.0f}")
        bc.categoryAxis.labels.fontName = bc.valueAxis.labels.fontName = "Helvetica"
        bc.bars[0].fillColor, bc.bars[1].fillColor = palette[0], palette[1]
        bc.bars.strokeColor = None
        bc.groupSpacing = 3
        bc.valueAxis.valueMin = 0
        d.add(bc)
        d.add(String(45, 118, "Revenue over time (group deals = violet, maintenance = green)" + (f"  - last {len(shown)} of {len(ts)} periods" if len(ts) > len(shown) else ""), fontSize=8, fontName="Helvetica-Bold"))
        story += [d, Spacer(1, 2 * mm)]

    def pie(title, items):
        dr = Drawing(W / 2 - 6, 104)
        dr.add(String(0, 94, title, fontSize=8, fontName="Helvetica-Bold"))
        if not items or not sum(i["value"] for i in items):
            dr.add(String(0, 45, "No data for these filters", fontSize=8, fontName="Helvetica", fillColor=muted))
            return dr
        p = Pie()
        p.x, p.y, p.width, p.height = 5, 4, 80, 80
        p.data = [i["value"] for i in items]
        p.labels = None
        p.slices.strokeColor = colors.white
        for i in range(len(items)):
            p.slices[i].fillColor = palette[i % len(palette)]
        dr.add(p)
        for i, it in enumerate(items[:8]):
            dr.add(String(105, 74 - i * 11, f"{_title(it['name'])}: {it['value']}", fontSize=7.5, fontName="Helvetica"))
        return dr

    story.append(Table([[pie("Bookings by status", r["booking_status"]), pie("Complaints by status", r["complaints_by_status"])]], colWidths=[W / 2, W / 2]))
    story.append(Spacer(1, 2 * mm))

    soc = r["revenue_by_society"][:10]
    if soc:
        d = Drawing(W, 25 + 16 * len(soc))
        hb = HorizontalBarChart()
        hb.x, hb.y, hb.width, hb.height = 150, 12, W - 175, 12 * len(soc) + 8
        hb.data = [[s["total"] for s in reversed(soc)]]
        hb.categoryAxis.categoryNames = [s["name"][:28] for s in reversed(soc)]
        hb.categoryAxis.labels.fontSize = 7
        hb.categoryAxis.labels.fontName = hb.valueAxis.labels.fontName = "Helvetica"
        hb.valueAxis.labels.fontSize = 6.5
        hb.valueAxis.valueMin = 0
        hb.bars[0].fillColor = palette[0]
        hb.bars.strokeColor = None
        d.add(hb)
        d.add(String(0, 25 + 16 * len(soc) - 10, "Revenue by society (top 10)", fontSize=8, fontName="Helvetica-Bold"))
        story += [Spacer(1, 3 * mm), d]

    # ---- tables
    story += [PageBreak(), Paragraph("Societies", h2), Spacer(1, 2 * mm)]
    story.append(table(["Society", "Code", "City", "Units", "Joined", "Bookings", "Group deals", "Maintenance", "Refunded", "Net revenue", "Coll. %", "Complaints"],
                       [[s["society"], s["code"], s["city"], s["units"], s["residents_joined"], s["bookings"], rs(s["booking_revenue"]), rs(s["maintenance_revenue"]), rs(s["refunded"]),
                         rs(s["net_revenue"]), f"{s['collection_rate']}%", f"{s['complaints_resolved']}/{s['complaints']}"] for s in r["societies"]],
                       [0.17, 0.06, 0.09, 0.05, 0.05, 0.06, 0.1, 0.1, 0.09, 0.1, 0.06, 0.07], right_cols=(3, 4, 5, 6, 7, 8, 9, 10, 11),
                       total=["Total", "", "", sum(s["units"] for s in r["societies"]), k["residents_joined"], k["bookings"], rs(k["booking_revenue"]), rs(k["maintenance_revenue"]),
                              rs(k["refunded"]), rs(k["net_revenue"]), f"{k['collection_rate']}%", f"{k['complaints_resolved']}/{k['complaints']}"] if r["societies"] else None))

    if r["revenue_by_mode"] or r["deals_by_category"]:
        story += [Spacer(1, 6 * mm)]
        left = [Paragraph("Revenue by payment mode", h2), Spacer(1, 2 * mm), table(["Mode", "Payments", "Amount"], [[_title(m["name"]), m["count"], rs(m["amount"])] for m in r["revenue_by_mode"]], [0.4, 0.25, 0.35], right_cols=(1, 2), total_w=W / 2 - 10)]
        right = [Paragraph("Deals by category", h2), Spacer(1, 2 * mm), table(["Category", "Deals", "Bookings", "Paid value"], [[_title(c["category"]), c["deals"], c["bookings"], rs(c["gmv"])] for c in r["deals_by_category"]], [0.34, 0.16, 0.2, 0.3], right_cols=(1, 2, 3), total_w=W / 2 - 10)]
        two = Table([[left, right]], colWidths=[W / 2, W / 2])
        two.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (0, 0), 8)]))
        story.append(two)

    if r["top_deals"]:
        story += [Spacer(1, 6 * mm), KeepTogether([Paragraph("Top deals by paid value", h2), Spacer(1, 2 * mm),
                  table(["Deal", "Society", "Category", "Units", "Price / unit", "Paid value", "Status"],
                        [[x["deal"], x["society"], _title(x["category"]), x["units"], rs(x["price_per_unit"]), rs(x["gmv"]), _title(x["status"])] for x in r["top_deals"]],
                        [0.26, 0.2, 0.12, 0.07, 0.12, 0.13, 0.1], right_cols=(3, 4, 5))])]

    if r["maintenance_by_period"]:
        story += [Spacer(1, 6 * mm), KeepTogether([Paragraph("Maintenance", h2), Spacer(1, 2 * mm),
                  table(["Period", "Bills", "Paid", "Overdue", "Billed", "Collected", "Collection %"],
                        [[x["period"], x["bills"], x["paid"], x["overdue"], rs(x["billed"]), rs(x["collected"]), f"{x['rate']}%"] for x in r["maintenance_by_period"]],
                        [0.16, 0.1, 0.1, 0.1, 0.18, 0.18, 0.18], right_cols=(1, 2, 3, 4, 5, 6))])]

    if r["vendors"]:
        story += [Spacer(1, 6 * mm), KeepTogether([Paragraph("Vendors", h2), Spacer(1, 2 * mm),
                  table(["Vendor", "Jobs", "Completed", "Avg rating", "Payout"], [[v["vendor"], v["jobs"], v["completed"], v["avg_rating"] if v["avg_rating"] is not None else "-", rs(v["payout"])] for v in r["vendors"]],
                        [0.4, 0.12, 0.14, 0.14, 0.2], right_cols=(1, 2, 3, 4))])]

    pays = r["payments"][:250]
    if pays:
        story += [PageBreak(), Paragraph(f"Payments ({len(pays)} of {r['payments_total']}, newest first)", h2), Spacer(1, 2 * mm),
                  table(["Paid on", "Transaction", "Society", "Resident", "For", "Mode", "Amount"],
                        [[f"{_dt(p['paid_at']):%d %b %Y %H:%M}", p["txn_id"], p["society"], p["resident"], p["description"], _title(p["mode"]), rs(p["amount"])] for p in pays],
                        [0.12, 0.15, 0.17, 0.14, 0.2, 0.08, 0.14], right_cols=(6,))]
        if r["payments_total"] > len(pays):
            story.append(Paragraph("The Excel export has every payment.", small))

    def footer(canvas, doc_):
        brand.watermark(canvas, page, size_mm=120)
        canvas.saveState()
        canvas.setStrokeColor(line)
        canvas.line(14 * mm, 11 * mm, page[0] - 14 * mm, 11 * mm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(muted)
        canvas.drawString(14 * mm, 7 * mm, "Odeslo platform report. Computer generated.")
        canvas.drawRightString(page[0] - 14 * mm, 7 * mm, f"Page {doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
