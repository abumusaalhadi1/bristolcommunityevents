"""Helpers for building booking receipt text and PDF output."""

from __future__ import annotations

import io
import textwrap
from decimal import Decimal, InvalidOperation


def booking_receipt_reference(booking_id: int) -> str:
    return f"BCE-{int(booking_id):06d}"


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


def pdf_add_text(ops, x, y, text, *, size=11, bold=False):
    font = "/F2" if bold else "/F1"
    ops.append(f"BT {font} {size} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm ({pdf_escape_text(text)}) Tj ET")


def pdf_add_line(ops, x1, y1, x2, y2, *, width=0.8, gray=0.82):
    ops.append(f"{gray:.3f} G")
    ops.append(f"{width:.2f} w")
    ops.append(f"{x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")


def pdf_add_paragraph(ops, x, y, text, *, size=11, leading=14, max_chars=84, bold=False):
    for line in pdf_wrap_text(text, max_chars):
        pdf_add_text(ops, x, y, line, size=size, bold=bold)
        y -= leading
    return y


def build_booking_receipt_pdf(booking):
    """Build the one-page PDF receipt shown and downloaded after booking."""
    booking_id = int(booking["booking_id"])
    receipt_reference = booking_receipt_reference(booking_id)
    page_width = 612.0
    page_height = 792.0
    margin = 54.0
    header_height = 108.0
    body_top = page_height - header_height - 28.0
    body_width_chars = max(50, int((page_width - (2 * margin)) / (11 * 0.52)))
    ops = []

    # Receipt banner
    ops.append("0.10 0.32 0.47 rg")
    ops.append(f"0 {page_height - header_height:.2f} {page_width:.2f} {header_height:.2f} re f")
    ops.append("1 1 1 rg")
    pdf_add_text(ops, margin, page_height - 38, "Bristol", size=20, bold=True)
    ops.append("0.953 0.612 0.071 rg")
    pdf_add_text(
        ops,
        margin + pdf_approx_text_width("Bristol ", 20),
        page_height - 38,
        "Community Events",
        size=20,
        bold=True,
    )
    ops.append("1 1 1 rg")
    pdf_add_text(ops, margin, page_height - 62, "Booking Receipt", size=12, bold=False)
    pdf_add_text(ops, margin, page_height - 78, "Official confirmation and payment record", size=9, bold=False)

    receipt_text = f"Receipt No. {receipt_reference}"
    booked_text = f"Booked {format_receipt_datetime(booking.get('booked_at') or booking.get('created_at'))}"
    receipt_x = max(margin, page_width - margin - pdf_approx_text_width(receipt_text, 11))
    booked_x = max(margin, page_width - margin - pdf_approx_text_width(booked_text, 10))
    pdf_add_text(ops, receipt_x, page_height - 38, receipt_text, size=11, bold=True)
    pdf_add_text(ops, booked_x, page_height - 60, booked_text, size=10, bold=False)
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
        )
        ops.append("0 0 0 rg")
        y -= 4
        pdf_add_line(ops, margin, y, page_width - margin, y, width=0.6, gray=0.88)
        y -= 16

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
    add_field("Receipt Reference", receipt_reference)
    add_field("Status", booking.get("status") or "Confirmed")
    add_field("Payment Status", booking.get("payment_status") or "Pending")
    add_field("Booked At", format_receipt_datetime(booking.get("booked_at") or booking.get("created_at")))
    add_field("Tickets", booking.get("tickets") or 0)
    add_field("Booking Days", booking.get("booking_days") or 1)
    add_field("Subtotal", f"£{_to_money(booking.get('subtotal_amount')):.2f}")
    add_field("Student Discount", f"-£{_to_money(booking.get('student_discount_amount')):.2f}")
    add_field("Advance Discount", f"-£{_to_money(booking.get('advance_discount_amount')):.2f}")
    add_field("Discount Total", f"-£{_to_money(booking.get('discount_applied')):.2f}")
    add_field("Cancellation Charge", f"£{_to_money(booking.get('cancellation_charge')):.2f}")
    add_field("Refund Amount", f"£{_to_money(booking.get('refund_amount')):.2f}")
    add_field("Payment Method", (booking.get("payment_method") or "card").title())
    add_field("Total Paid", f"£{_to_money(booking.get('amount')):.2f}")

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
    add_field("Location", booking.get("location") or booking.get("address") or "-")
    add_field("Category", booking.get("category_name") or "-")
    add_field("Conditions", booking.get("conditions") or "-")

    y -= 8
    pdf_add_line(ops, margin, y, page_width - margin, y, width=0.7, gray=0.82)
    y -= 18
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
