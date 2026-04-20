"""Helpers for building booking receipt text and PDF output."""

from __future__ import annotations

import io
import math
import textwrap
from decimal import Decimal, InvalidOperation


def booking_receipt_reference(booking_id: int) -> str:
    return f"BCE-{int(booking_id):06d}"


STUDENT_ID_DISCLAIMER = "Please bring a valid student ID to the event to verify the 10% student discount."


def format_receipt_date(value) -> str:
    if not value:
        return "-"
    if hasattr(value, "strftime"):
        return value.strftime("%B %d, %Y")
    text = str(value).strip()
    return text or "-"


def format_receipt_datetime(value) -> str:
    if not value:
        return "-"
    if hasattr(value, "strftime"):
        return value.strftime("%B %d, %Y %H:%M")
    text = str(value).strip()
    return text or "-"


def normalize_receipt_text(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, Decimal):
        value = f"{value:.2f}"
    elif hasattr(value, "strftime"):
        value = format_receipt_datetime(value)
    else:
        value = str(value)
    text = value.strip()
    return text or "-"


def _to_money(value) -> Decimal:
    if value is None:
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"))
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (TypeError, InvalidOperation):
        return Decimal("0.00")


def pdf_escape_text(value) -> str:
    text = normalize_receipt_text(value).encode("latin-1", "replace").decode("latin-1")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def pdf_approx_text_width(text, size: float) -> float:
    return len(normalize_receipt_text(text)) * size * 0.52


def pdf_wrap_text(text, max_chars: int):
    paragraphs = normalize_receipt_text(text).splitlines()
    wrapped = []
    for paragraph in paragraphs:
        if not paragraph:
            wrapped.append("")
            continue
        wrapped.extend(
            textwrap.wrap(
                paragraph,
                width=max_chars,
                break_long_words=True,
                break_on_hyphens=False,
            )
            or [""]
        )
    return wrapped or [""]


def pdf_add_text(ops, x, y, text, *, size=11, bold=False, color=(0, 0, 0)):
    font = "/F2" if bold else "/F1"
    ops.append(f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} rg")
    ops.append(f"BT {font} {size} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm ({pdf_escape_text(text)}) Tj ET")


def pdf_add_line(ops, x1, y1, x2, y2, *, width=0.8, gray=0.82):
    ops.append(f"{gray:.3f} G")
    ops.append(f"{width:.2f} w")
    ops.append(f"{x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")


def pdf_add_paragraph(ops, x, y, text, *, size=11, leading=14, max_chars=84, bold=False, color=(0, 0, 0)):
    for line in pdf_wrap_text(text, max_chars):
        pdf_add_text(ops, x, y, line, size=size, bold=bold, color=color)
        y -= leading
    return y


def build_booking_receipt_pdf(booking):
    """Build the one-page PDF receipt shown and downloaded after booking."""
    booking_id = int(booking["booking_id"])
    receipt_reference = booking_receipt_reference(booking_id)
    page_width = 612.0
    page_height = 792.0
    margin = 42.0
    header_height = 108.0
    body_top = page_height - header_height - 28.0
    body_width_chars = max(50, int((page_width - (2 * margin)) / (11 * 0.52)))
    ops = []

    # Receipt banner
    ops.append("0.10 0.32 0.47 rg")
    ops.append(f"0 {page_height - header_height:.2f} {page_width:.2f} {header_height:.2f} re f")
    ops.append("1 1 1 rg")
    pdf_add_text(ops, margin, page_height - 38, "Bristol", size=20, bold=True, color=(1, 1, 1))
    ops.append("0.953 0.612 0.071 rg")
    pdf_add_text(
        ops,
        margin + pdf_approx_text_width("Bristol ", 20),
        page_height - 38,
        "Community Events",
        size=20,
        bold=True,
        color=(1, 1, 1),
    )
    ops.append("1 1 1 rg")
    pdf_add_text(ops, margin, page_height - 62, "Booking Receipt", size=12, bold=False, color=(1, 1, 1))
    pdf_add_text(ops, margin, page_height - 78, "Official confirmation and payment record", size=9, bold=False, color=(1, 1, 1))

    receipt_text = f"Receipt No. {receipt_reference}"
    booked_text = f"Booked {format_receipt_datetime(booking.get('booked_at') or booking.get('created_at'))}"
    # Keep both metadata lines anchored to the same x position so they read as a matched block.
    meta_x = page_width - margin - max(pdf_approx_text_width(receipt_text, 11), pdf_approx_text_width(booked_text, 10)) - 4
    pdf_add_text(ops, meta_x, page_height - 62, receipt_text, size=11, bold=True, color=(1, 1, 1))
    pdf_add_text(ops, meta_x, page_height - 74, booked_text, size=10, bold=False, color=(1, 1, 1))
    ops.append("0 0 0 rg")

    y = body_top
    y = pdf_add_paragraph(
        ops,
        margin,
        y,
        f"Official confirmation for booking #{booking_id}",
        size=12,
        leading=15,
        max_chars=body_width_chars,
        bold=True,
    )
    y = pdf_add_paragraph(
        ops,
        margin,
        y - 2,
        f"Reference: {receipt_reference}",
        size=10,
        leading=13,
        max_chars=body_width_chars,
    )
    y -= 6
    pdf_add_line(ops, margin, y, page_width - margin, y, width=0.9, gray=0.78)
    y -= 18

    def add_section(title):
        nonlocal y
        ops.append("0.10 0.32 0.47 rg")
        y = pdf_add_paragraph(
            ops,
            margin,
            y,
            title,
            size=13,
            leading=16,
            max_chars=body_width_chars,
            bold=True,
            color=(0.10, 0.32, 0.47),
        )
        ops.append("0 0 0 rg")
        y -= 2
        pdf_add_line(ops, margin, y, page_width - margin, y, width=0.6, gray=0.88)
        y -= 14

    def add_field(label, value):
        nonlocal y
        y = pdf_add_paragraph(
            ops,
            margin,
            y,
            f"{label}: {normalize_receipt_text(value)}",
            size=11,
            leading=14,
            max_chars=body_width_chars,
        )
        y -= 2

    add_section("Booking Summary")
    add_field("Booking ID", booking_id)
    add_field("Status", booking.get("status") or "Confirmed")
    add_field("Payment Status", booking.get("payment_status") or "Pending")
    add_field("Booked At", format_receipt_datetime(booking.get("booked_at") or booking.get("created_at")))
    add_field("Tickets", booking.get("tickets") or 0)
    add_field("Booking Days", booking.get("booking_days") or 1)
    add_field("Subtotal", f"£{_to_money(booking.get('subtotal_amount')):.2f}")
    add_field("Student Discount", f"-£{_to_money(booking.get('student_discount_amount')):.2f}")
    add_field("Advance Discount", f"-£{_to_money(booking.get('advance_discount_amount')):.2f}")
    add_field("Discount Total", f"-£{_to_money(booking.get('discount_applied')):.2f}")
    add_field("Payment Method", (booking.get("payment_method") or "card").title())
    add_field("Total Paid", f"£{_to_money(booking.get('amount')):.2f}")
    if booking.get("is_student"):
        ops.append("0.73 0.11 0.11 rg")
        disclaimer_label = "Student ID Disclaimer:"
        pdf_add_text(ops, margin, y - 2, disclaimer_label, size=11, bold=True)
        y = pdf_add_paragraph(
            ops,
            margin + pdf_approx_text_width(disclaimer_label + " ", 11),
            y - 2,
            STUDENT_ID_DISCLAIMER,
            size=11,
            leading=14,
            max_chars=max(20, body_width_chars - len(disclaimer_label)),
            bold=False,
        )
        ops.append("0 0 0 rg")
        y -= 4
    y -= 8
    add_section("Attendee Details")
    add_field("Name", booking.get("full_name"))
    add_field("Email", booking.get("email"))
    add_field("Contact Phone", booking.get("contact_phone") or booking.get("phone") or "-")
    add_field("Role", (booking.get("role") or "user").title())

    y -= 8
    add_section("Event Details")
    add_field("Event", booking.get("event_name"))
    if booking.get("event_end_date") and booking.get("event_end_date") != booking.get("event_date"):
        add_field(
            "Date",
            f"{format_receipt_date(booking.get('event_date'))} - {format_receipt_date(booking.get('event_end_date'))}",
        )
    else:
        add_field("Date", format_receipt_date(booking.get("event_date")))
    add_field("Venue", booking.get("venue_name") or "-")
    add_field("Address", booking.get("address") or booking.get("location") or "-")
    add_field("Category", booking.get("category_name") or "-")
    add_field("Conditions", booking.get("conditions") or "-")

    y -= 8
    pdf_add_line(ops, margin, y, page_width - margin, y, width=0.7, gray=0.82)
    y -= 24
    ops.append("0.10 0.32 0.47 rg")
    y = pdf_add_paragraph(
        ops,
        margin,
        y,
        "Thank you for booking with Bristol Community Events.",
        size=11,
        leading=14,
        max_chars=body_width_chars,
        bold=True,
    )
    ops.append("0 0 0 rg")
    y = pdf_add_paragraph(
        ops,
        margin,
        y - 2,
        "Please keep this receipt for your records. For help, contact us at 42 Queen Square, Bristol BS1 4QR or +44 (0) 117 123 4567.",
        size=10,
        leading=13,
        max_chars=body_width_chars,
    )
    y -= 20
    content_stream = "\n".join(ops).encode("latin-1", "replace")
    objects = [
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 3 0 R /MediaBox [0 0 {page_width:.0f} {page_height:.0f}] "
        f"/Resources << /Font << /F1 1 0 R /F2 2 0 R >> >> /Contents 5 0 R >>".encode("ascii"),
        b"<< /Length " + str(len(content_stream)).encode("ascii") + b" >>\nstream\n" + content_stream + b"\nendstream",
        b"<< /Type /Catalog /Pages 3 0 R >>",
    ]

    pdf = io.BytesIO()
    pdf.write(b"%PDF-1.4\n")
    offsets = []
    for obj_number, body in enumerate(objects, start=1):
        offsets.append(pdf.tell())
        pdf.write(f"{obj_number} 0 obj\n".encode("ascii"))
        pdf.write(body)
        pdf.write(b"\nendobj\n")

    xref_start = pdf.tell()
    pdf.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.write(b"0000000000 65535 f \n")
    for offset in offsets:
        pdf.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 6 0 R >>\nstartxref\n{xref_start}\n%%EOF".encode(
            "ascii"
        )
    )
    return pdf.getvalue()


def pdf_add_rect(ops, x, y, w, h, *, fill=None, stroke=None, line_width=0.8):
    ops.append("q")
    if fill is not None:
        ops.append(f"{fill[0]:.3f} {fill[1]:.3f} {fill[2]:.3f} rg")
    if stroke is not None:
        ops.append(f"{stroke[0]:.3f} {stroke[1]:.3f} {stroke[2]:.3f} RG")
        ops.append(f"{line_width:.2f} w")
    cmd = "B" if fill is not None and stroke is not None else "f" if fill is not None else "S"
    ops.append(f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re {cmd}")
    ops.append("Q")


def pdf_add_polygon(ops, points, *, fill=None, stroke=None, line_width=0.8):
    if not points:
        return
    ops.append("q")
    if fill is not None:
        ops.append(f"{fill[0]:.3f} {fill[1]:.3f} {fill[2]:.3f} rg")
    if stroke is not None:
        ops.append(f"{stroke[0]:.3f} {stroke[1]:.3f} {stroke[2]:.3f} RG")
        ops.append(f"{line_width:.2f} w")
    first_x, first_y = points[0]
    ops.append(f"{first_x:.2f} {first_y:.2f} m")
    for x, y in points[1:]:
        ops.append(f"{x:.2f} {y:.2f} l")
    ops.append("h")
    cmd = "B" if fill is not None and stroke is not None else "f" if fill is not None else "S"
    ops.append(cmd)
    ops.append("Q")


def pdf_add_legend_item(ops, x, y, color, label, *, size=9):
    pdf_add_rect(ops, x, y + 1, 8, 8, fill=color, stroke=color, line_width=0.4)
    pdf_add_text(ops, x + 12, y, label, size=size, bold=False)


def pdf_draw_card(ops, x, y, w, h, *, title, value, subtitle="", accent=(0.10, 0.32, 0.47)):
    pdf_add_rect(ops, x, y, w, h, fill=(1, 1, 1), stroke=(0.86, 0.88, 0.92), line_width=0.8)
    pdf_add_rect(ops, x, y + h - 8, w, 8, fill=accent, stroke=accent, line_width=0.0)
    pdf_add_text(ops, x + 12, y + h - 22, title, size=8.5, bold=True)
    pdf_add_text(ops, x + 12, y + h - 44, value, size=15, bold=True)
    if subtitle:
        pdf_add_text(ops, x + 12, y + 12, subtitle, size=8.5, bold=False)


def pdf_draw_line_chart(ops, x, y, w, h, labels, values, *, title, accent=(0.10, 0.32, 0.47)):
    pdf_add_rect(ops, x, y, w, h, fill=(1, 1, 1), stroke=(0.86, 0.88, 0.92), line_width=0.8)
    pdf_add_text(ops, x + 12, y + h - 18, title, size=10.5, bold=True)

    chart_left = x + 42
    chart_bottom = y + 28
    chart_width = w - 56
    chart_height = h - 54

    if not labels or not values:
        pdf_add_text(ops, x + 12, y + h / 2, "No data", size=9, bold=False)
        return

    numeric_values = [float(v or 0) for v in values]
    max_value = max(numeric_values) if numeric_values else 0
    if max_value <= 0:
        max_value = 1

    grid_steps = 4
    for step in range(grid_steps + 1):
        frac = step / grid_steps
        grid_y = chart_bottom + (chart_height * frac)
        pdf_add_line(ops, chart_left, grid_y, chart_left + chart_width, grid_y, width=0.45, gray=0.90)
        tick_value = max_value * frac
        pdf_add_text(ops, x + 4, grid_y - 3, f"£{tick_value:.0f}", size=7.5, bold=False)

    if len(values) == 1:
        step_x = chart_width
    else:
        step_x = chart_width / max(len(values) - 1, 1)

    points = []
    for index, value in enumerate(numeric_values):
        px = chart_left + (index * step_x)
        py = chart_bottom + (chart_height * (value / max_value))
        points.append((px, py))

    if len(points) >= 2:
        ops.append("q")
        ops.append(f"{accent[0]:.3f} {accent[1]:.3f} {accent[2]:.3f} RG")
        ops.append("2.0 w")
        ops.append(f"{points[0][0]:.2f} {points[0][1]:.2f} m")
        for px, py in points[1:]:
            ops.append(f"{px:.2f} {py:.2f} l")
        ops.append("S")
        ops.append("Q")
    for px, py in points:
        pdf_add_rect(ops, px - 2.5, py - 2.5, 5, 5, fill=accent, stroke=accent, line_width=0.2)

    tick_step = max(1, len(labels) // 8)
    for index, label in enumerate(labels):
        if index % tick_step != 0 and index != len(labels) - 1:
            continue
        px = chart_left + (index * step_x)
        pdf_add_text(ops, px - 12, chart_bottom - 14, label, size=7, bold=False)


def pdf_draw_bar_chart(ops, x, y, w, h, labels, values, *, title, accent=(0.06, 0.46, 0.43)):
    pdf_add_rect(ops, x, y, w, h, fill=(1, 1, 1), stroke=(0.86, 0.88, 0.92), line_width=0.8)
    pdf_add_text(ops, x + 12, y + h - 18, title, size=10.5, bold=True)

    if not labels or not values:
        pdf_add_text(ops, x + 12, y + h / 2, "No data", size=9, bold=False)
        return

    label_x = x + 12
    bar_x = x + 120
    bar_w = w - 140
    top_y = y + h - 42
    row_h = max(18, min(28, (h - 56) / max(len(labels), 1)))
    max_value = max(float(v or 0) for v in values) if values else 1
    if max_value <= 0:
        max_value = 1

    for index, (label, value) in enumerate(zip(labels, values)):
        row_y = top_y - (index * row_h)
        pdf_add_text(ops, label_x, row_y + 4, label, size=7.5, bold=False)
        pdf_add_rect(ops, bar_x, row_y, bar_w, 10, fill=(0.94, 0.95, 0.97), stroke=(0.94, 0.95, 0.97), line_width=0.1)
        bar_fill = bar_w * (float(value or 0) / max_value)
        pdf_add_rect(ops, bar_x, row_y, bar_fill, 10, fill=accent, stroke=accent, line_width=0.1)
        pdf_add_text(ops, bar_x + bar_fill + 4, row_y + 1, f"£{float(value or 0):.0f}", size=7.5, bold=False)


def _pdf_circle_points(cx, cy, r, start_angle, end_angle, steps):
    points = []
    for step in range(steps + 1):
        angle = start_angle + ((end_angle - start_angle) * (step / steps))
        points.append((cx + math.cos(angle) * r, cy + math.sin(angle) * r))
    return points


def pdf_draw_pie_chart(ops, x, y, w, h, labels, values, *, title, colors):
    pdf_add_rect(ops, x, y, w, h, fill=(1, 1, 1), stroke=(0.86, 0.88, 0.92), line_width=0.8)
    pdf_add_text(ops, x + 12, y + h - 18, title, size=10.5, bold=True)

    if not labels or not values:
        pdf_add_text(ops, x + 12, y + h / 2, "No data", size=9, bold=False)
        return

    total = sum(float(v or 0) for v in values)
    if total <= 0:
        pdf_add_text(ops, x + 12, y + h / 2, "No data", size=9, bold=False)
        return

    cx = x + (w * 0.30)
    cy = y + (h * 0.46)
    radius = min(w * 0.22, h * 0.28)
    start_angle = math.radians(90)
    for index, value in enumerate(values):
        fraction = float(value or 0) / total
        sweep = max(fraction * math.tau, 0)
        end_angle = start_angle + sweep
        steps = max(10, int(40 * max(fraction, 0.02)))
        arc_points = _pdf_circle_points(cx, cy, radius, start_angle, end_angle, steps)
        slice_points = [(cx, cy)] + arc_points
        pdf_add_polygon(
            ops,
            slice_points,
            fill=colors[index % len(colors)],
            stroke=(1, 1, 1),
            line_width=0.4,
        )
        start_angle = end_angle

    legend_x = x + (w * 0.56)
    legend_y = y + h - 42
    for index, (label, value) in enumerate(zip(labels, values)):
        item_y = legend_y - (index * 16)
        if item_y < y + 14:
            break
        pdf_add_legend_item(
            ops,
            legend_x,
            item_y,
            colors[index % len(colors)],
            f"{label} ({float(value or 0):.0f})",
            size=8.5,
        )


def _build_pdf_document(pages, *, page_width=792.0, page_height=612.0):
    objects = [
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
    ]

    pages_object_number = 3
    page_object_numbers = []
    content_object_numbers = []
    for index in range(len(pages)):
        page_object_numbers.append(4 + index * 2)
        content_object_numbers.append(5 + index * 2)

    objects.append(None)  # pages object placeholder
    for page_ops, content_obj_num in zip(pages, content_object_numbers):
        content_stream = "\n".join(page_ops).encode("latin-1", "replace")
        objects.append(None)  # page object placeholder
        objects.append(
            b"<< /Length "
            + str(len(content_stream)).encode("ascii")
            + b" >>\nstream\n"
            + content_stream
            + b"\nendstream"
        )

    kids = " ".join(f"{num} 0 R" for num in page_object_numbers)
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("ascii")

    for index, page_obj_num in enumerate(page_object_numbers):
        content_obj_num = content_object_numbers[index]
        objects[3 + index * 2] = (
            f"<< /Type /Page /Parent {pages_object_number} 0 R /MediaBox [0 0 {page_width:.0f} {page_height:.0f}] "
            f"/Resources << /Font << /F1 1 0 R /F2 2 0 R >> >> /Contents {content_obj_num} 0 R >>"
        ).encode("ascii")

    catalog_object_number = 4 + len(pages) * 2
    objects.append(f"<< /Type /Catalog /Pages {pages_object_number} 0 R >>".encode("ascii"))

    pdf = io.BytesIO()
    pdf.write(b"%PDF-1.4\n")
    offsets = []
    for obj_number, body in enumerate(objects, start=1):
        offsets.append(pdf.tell())
        pdf.write(f"{obj_number} 0 obj\n".encode("ascii"))
        pdf.write(body)
        pdf.write(b"\nendobj\n")

    xref_start = pdf.tell()
    pdf.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.write(b"0000000000 65535 f \n")
    for offset in offsets:
        pdf.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_object_number} 0 R >>\nstartxref\n{xref_start}\n%%EOF".encode(
            "ascii"
        )
    )
    return pdf.getvalue()


def build_admin_reports_pdf(report):
    page_width = 792.0
    page_height = 612.0
    margin = 24.0
    report_period = report.get("report_period") or {}
    report_metrics = report.get("report_metrics") or {}
    report_comparison = report.get("report_comparison") or {}
    cancellation_summary = report.get("cancellation_summary") or {}
    waitlist_summary = report.get("waitlist_summary") or {}
    event_report = report.get("event_report")
    venue_report = report.get("venue_report")
    report_customers = report.get("report_customers") or []

    pages = []

    summary_ops = []
    summary_ops.append("0.10 0.32 0.47 rg")
    summary_ops.append(f"0 {page_height - 76:.2f} {page_width:.2f} 76.00 re f")
    summary_ops.append("1 1 1 rg")
    pdf_add_text(summary_ops, margin, page_height - 32, "Bristol Community Events", size=18, bold=True, color=(1, 1, 1))
    pdf_add_text(summary_ops, margin, page_height - 50, report_period.get("title") or "Admin Report", size=13, bold=True, color=(1, 1, 1))
    pdf_add_text(
        summary_ops,
        margin,
        page_height - 66,
        report_period.get("note") or "Selected analytics range",
        size=9.5,
        bold=False,
        color=(1, 1, 1),
    )

    kpi_cards = [
        ("Revenue", f"£{_to_money(report_metrics.get('revenue_total')):.2f}", "vs previous period", (0.10, 0.32, 0.47)),
        ("Tickets Sold", str(report_metrics.get("tickets_sold") or 0), "Completed tickets", (0.06, 0.46, 0.43)),
        ("Unique Customers", str(report_metrics.get("unique_customers") or 0), "Distinct buyers", (0.95, 0.61, 0.07)),
        ("Completion Rate", f"{report_metrics.get('booking_completion_rate') or 0}%", "Completed vs attempts", (0.12, 0.50, 0.74)),
        ("Repeat Bookers", str(report_metrics.get("repeat_bookers") or 0), "More than one booking", (0.56, 0.35, 0.94)),
        ("Cancellation Rate", f"{report_metrics.get('cancellation_rate') or 0}%", "Cancelled bookings", (0.70, 0.17, 0.17)),
    ]
    card_w = (page_width - (margin * 2) - 16) / 3
    card_h = 74.0
    card_positions = [
        (margin + (card_w + 8) * 0, 436.0),
        (margin + (card_w + 8) * 1, 436.0),
        (margin + (card_w + 8) * 2, 436.0),
        (margin + (card_w + 8) * 0, 350.0),
        (margin + (card_w + 8) * 1, 350.0),
        (margin + (card_w + 8) * 2, 350.0),
    ]
    for (title, value, subtitle, accent), (x, y) in zip(kpi_cards, card_positions):
        pdf_draw_card(summary_ops, x, y, card_w, card_h, title=title, value=value, subtitle=subtitle, accent=accent)

    info_x = margin
    info_w = (page_width - (margin * 2) - 16) / 3
    info_y = 236.0
    pdf_draw_card(
        summary_ops,
        info_x,
        info_y,
        info_w,
        92.0,
        title="Customer Report",
        value=str(report_metrics.get("unique_customers") or 0),
        subtitle=f"Avg spend £{_to_money(report_metrics.get('avg_spend_per_customer')):.2f} | Repeat bookers {report_metrics.get('repeat_bookers') or 0}",
        accent=(0.10, 0.32, 0.47),
    )
    pdf_draw_card(
        summary_ops,
        info_x + info_w + 8,
        info_y,
        info_w,
        92.0,
        title="Cancellation Report",
        value=str(cancellation_summary.get("cancelled_count") or 0),
        subtitle=f"Lost £{_to_money(cancellation_summary.get('lost_revenue')):.2f} | Charges £{_to_money(cancellation_summary.get('charge_collected')):.2f}",
        accent=(0.70, 0.17, 0.17),
    )
    pdf_draw_card(
        summary_ops,
        info_x + (info_w + 8) * 2,
        info_y,
        info_w,
        92.0,
        title="Waitlist Report",
        value=str(waitlist_summary.get("total_requests") or 0),
        subtitle=f"Tickets {waitlist_summary.get('total_tickets') or 0}",
        accent=(0.95, 0.61, 0.07),
    )

    if event_report:
        pdf_add_text(summary_ops, margin, 198, "Selected Event", size=11, bold=True)
        pdf_add_text(summary_ops, margin, 182, event_report.get("event_name") or "-", size=10, bold=False)
        pdf_add_text(summary_ops, margin, 168, f"Bookings: {event_report.get('bookings_count') or 0} | Revenue: £{_to_money(event_report.get('revenue_total')):.2f}", size=9, bold=False)
    if venue_report:
        vx = 390.0
        pdf_add_text(summary_ops, vx, 198, "Selected Venue", size=11, bold=True)
        pdf_add_text(summary_ops, vx, 182, venue_report.get("venue_name") or "-", size=10, bold=False)
        pdf_add_text(summary_ops, vx, 168, f"Events: {venue_report.get('total_events') or 0} | Revenue: £{_to_money(venue_report.get('revenue_total')):.2f}", size=9, bold=False)

    pages.append(summary_ops)

    charts_ops = []
    charts_ops.append("0.10 0.32 0.47 rg")
    charts_ops.append(f"0 {page_height - 48:.2f} {page_width:.2f} 48.00 re f")
    charts_ops.append("1 1 1 rg")
    pdf_add_text(charts_ops, margin, page_height - 22, "Charts & Visualisations", size=16, bold=True, color=(1, 1, 1))
    pdf_add_text(charts_ops, margin, page_height - 36, report_period.get("note") or "", size=9, bold=False, color=(1, 1, 1))

    chart_w = (page_width - (margin * 2) - 14) / 2
    chart_h = 212.0
    left_x = margin
    right_x = margin + chart_w + 14
    top_y = 316.0
    bottom_y = 84.0

    pdf_draw_line_chart(
        charts_ops,
        left_x,
        top_y,
        chart_w,
        chart_h,
        report.get("report_revenue_trend_labels") or [],
        report.get("report_revenue_trend_values") or [],
        title="Revenue over time",
        accent=(0.10, 0.32, 0.47),
    )
    pdf_draw_bar_chart(
        charts_ops,
        right_x,
        top_y,
        chart_w,
        chart_h,
        report.get("report_top_event_labels") or [],
        report.get("report_top_event_values") or [],
        title="Top 5 events by revenue",
        accent=(0.06, 0.46, 0.43),
    )
    pdf_draw_pie_chart(
        charts_ops,
        left_x,
        bottom_y,
        chart_w,
        chart_h,
        report.get("report_category_labels") or [],
        report.get("report_category_values") or [],
        title="Category breakdown",
        colors=[
            (0.10, 0.32, 0.47),
            (0.06, 0.46, 0.43),
            (0.95, 0.61, 0.07),
            (0.91, 0.30, 0.24),
            (0.55, 0.35, 0.94),
            (0.39, 0.45, 0.52),
        ],
    )
    pdf_draw_pie_chart(
        charts_ops,
        right_x,
        bottom_y,
        chart_w,
        chart_h,
        report.get("year_report", {}).get("event_mix_labels") or [],
        report.get("year_report", {}).get("event_mix_values") or [],
        title="Event mix",
        colors=[
            (0.10, 0.32, 0.47),
            (0.91, 0.30, 0.24),
            (0.95, 0.61, 0.07),
        ],
    )

    pages.append(charts_ops)
    return _build_pdf_document(pages, page_width=page_width, page_height=page_height)
