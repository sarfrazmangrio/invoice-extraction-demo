"""Generate the synthetic invoice test set and its ground truth.

Creates 20 one- or two-page PDF invoices from 4 fictional vendors, each with its
own layout, date format and number format. Six invoices carry a planted problem
that the validation step should catch, and two are "scanned" (image-only PDFs
with no text layer). The true value of every field goes to data/ground_truth.json,
so the extraction can be scored field by field.

All companies, people and addresses are fictional.

Run:  python src/generate_invoices.py
Needs: reportlab, pillow, pypdfium2 (see requirements-dev.txt)
"""
from __future__ import annotations

import io
import json
import random
from datetime import date
from pathlib import Path

from PIL import Image, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, letter
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "invoices"
TRUTH_PATH = ROOT / "data" / "ground_truth.json"

random.seed(2026)

# ---------------------------------------------------------------- number formats

def fmt_intl(x: float, decimals: int = 2) -> str:
    """1234567.5 -> '1,234,567.50'"""
    return f"{x:,.{decimals}f}"


def fmt_lakh(x: float) -> str:
    """South Asian grouping without decimals: 123450 -> '1,23,450'."""
    s = str(int(round(x)))
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups + [tail])


def transpose_digits(x: float) -> float:
    """Swap the 2nd and 3rd digits of the whole part (the first differing pair if equal)."""
    whole, cents = divmod(round(x * 100), 100)
    d = list(str(whole))
    for i in range(1, len(d) - 1):
        if d[i] != d[i + 1]:
            d[i], d[i + 1] = d[i + 1], d[i]
            break
    return float(int("".join(d))) + cents / 100


def fmt_qty(q: float, thousands: bool = False) -> str:
    if float(q).is_integer():
        return f"{int(q):,}" if thousands else str(int(q))
    return f"{q:g}"

# ---------------------------------------------------------------------- vendors

VENDORS = {
    "A": {
        "name": "Orchid Office Supply Co.",
        "address": ["418 Harbor Avenue, Suite 12", "Portland, OR 97204, USA"],
        "currency": "USD", "tax_rate": 8.25, "page": letter,
        "prefix": "OOS-2026-", "start_no": 412,
        "customers": ["Brightside Dental Group", "Maple & Co. Interiors", "Summit Legal Partners"],
        "items": [
            ("A4 copy paper, 500 sheets", 6.49), ("Black toner cartridge 26A", 89.00),
            ("Stapler, heavy duty", 24.50), ("Ballpoint pens, box of 50", 12.99),
            ("Sticky notes 3x3, yellow, 12 pack", 9.75), ("Desk organizer, mesh", 18.20),
            ("Whiteboard markers, 8 pack", 11.40), ("File folders, letter, 100", 15.60),
            ("Paper shredder, cross-cut", 149.00), ("Ergonomic wireless mouse", 34.99),
            ("USB-C hub, 7-port", 42.00), ("Laminating pouches, 100", 21.30),
            ("Label maker tape, 2 pack", 16.80), ("Monitor stand, adjustable", 38.50),
            ("Envelopes #10, box of 500", 27.90), ("A3 printer paper, 250 sheets", 19.99),
            ("Correction tape, 10 pack", 8.60), ("Binder clips, assorted 60", 7.25),
            ("LED desk lamp", 45.00), ("Printing calculator", 64.95),
            ("Highlighters, 6 colors", 6.80), ("Clipboards, 5 pack", 14.25),
            ("Ring binders 2 in, 4 pack", 22.40), ("Index dividers, 8 tab", 5.10),
            ("Cable ties, 100 pack", 4.99), ("Wireless keyboard", 49.00),
            ("Desk fan, 12 in", 29.99), ("First aid kit, office", 36.75),
            ("Hand sanitizer, 1 L", 7.90), ("Tissue boxes, 12 pack", 18.60),
            ("Coffee filters, 500", 9.30), ("Trash bags 30 gal, 50", 13.45),
        ],
    },
    "B": {
        "name": "Kestrel Packaging Ltd.",
        "address": ["Plot 27, Sector 12-C, Korangi Industrial Area", "Karachi 74900, Pakistan"],
        "currency": "PKR", "tax_rate": 18.0, "page": A4,
        "prefix": "KPL/INV/", "start_no": 1187,
        "customers": ["Meridian Retail (Pvt.) Ltd.", "Crescent Foods", "Atlas Garments"],
        "items": [
            ("Corrugated box 12x10x8 in, 3-ply", 145.00), ("Corrugated box 18x14x12 in, 5-ply", 310.00),
            ("Bubble wrap roll 1 m x 50 m", 2450.00), ("Packing tape 2 in, clear, 6 rolls", 1080.00),
            ("Stretch film 18 in x 1500 ft", 1950.00), ("Kraft paper roll, 36 in", 3200.00),
            ("Air pillows, bag of 500", 1650.00), ("Printed mailer bags 12x16, 100", 2300.00),
            ("Pallet wrap, black", 2150.00), ("Edge protectors, 50", 1275.00),
            ("Shipping labels 4x6, 500", 1420.00),
        ],
    },
    "C": {
        "name": "Harbor Line Freight",
        "address": ["2200 Port Road, Building 4", "Long Beach, CA 90802, USA"],
        "currency": "USD", "tax_rate": None, "page": letter,
        "prefix": "HLF-", "start_no": 88213,
        "customers": ["Nova Home Electronics", "Greenleaf Pharmacy", "Pacific Tile Imports"],
        "items": [
            ("Container drayage, 40 ft, port to DC", 685.00), ("Customs clearance filing", 175.00),
            ("Warehouse handling (hours)", 48.00), ("Pallet storage (pallet-weeks)", 14.50),
            ("Fuel surcharge", 92.40), ("Chassis rental (days)", 38.00),
            ("Detention time (hours)", 85.00), ("Inspection exam fee", 260.00),
        ],
    },
    "D": {
        "name": "Saffron Print Studio",
        "address": ["14-B Main Boulevard, Gulberg III", "Lahore 54660, Pakistan"],
        "currency": "PKR", "tax_rate": 16.0, "page": A4,
        "prefix": "SPS-2026-", "start_no": 31,
        "customers": ["Atlas Garments", "Greenleaf Pharmacy", "Crescent Foods"],
        "items": [
            ("Business cards, 350 gsm, 500 pcs", 3500), ("A5 flyers, 130 gsm, 2,000 pcs", 14000),
            ("Roll-up banner 33x80 in", 6800), ("Letterheads, 100 gsm, 1,000 pcs", 7500),
            ("Tri-fold brochures, 1,000 pcs", 18500), ("Vinyl sticker labels, 1,000 pcs", 4200),
            ("A2 posters, glossy, 50 pcs", 5250), ("Envelope printing, 500 pcs", 4750),
        ],
    },
}

# ---------------------------------------------------------- invoice definitions
# (file no, vendor, invoice date, due date, number of lines, special case)
# Special cases are the planted problems the validation step should catch.
PLAN = [
    (1, "A", date(2026, 2, 17), date(2026, 3, 19), 5, None),
    (2, "B", date(2026, 2, 13), date(2026, 2, 28), 4, None),
    (3, "A", date(2026, 2, 24), date(2026, 3, 26), 30, "multipage"),
    (4, "A", date(2026, 3, 3), date(2026, 4, 2), 6, "subtotal_misprint"),
    (5, "C", date(2026, 3, 5), date(2026, 4, 4), 5, None),
    (6, "B", date(2026, 3, 28), date(2026, 3, 14), 3, "due_before_invoice"),
    (7, "D", date(2026, 3, 9), date(2026, 3, 24), 3, None),
    (8, "C", date(2026, 3, 12), date(2026, 4, 11), 4, None),
    (9, "B", date(2026, 3, 16), date(2026, 3, 31), 5, "line_math_error"),
    (10, "B", date(2026, 3, 18), date(2026, 4, 17), 4, "scanned"),
    (11, "D", date(2026, 3, 20), date(2026, 4, 4), 4, None),
    (12, "C", date(2026, 3, 23), date(2026, 4, 22), 3, "missing_invoice_number"),
    (13, "A", date(2026, 3, 25), date(2026, 4, 24), 7, None),
    (14, "C", date(2026, 3, 27), date(2026, 4, 26), 6, None),
    (15, "D", date(2026, 3, 30), date(2026, 4, 14), 5, None),
    (16, "D", date(2026, 4, 2), date(2026, 4, 17), 3, "total_misprint"),
    (17, "A", date(2026, 4, 6), date(2026, 5, 6), 4, None),
    (18, "C", date(2026, 4, 8), date(2026, 5, 8), 5, None),
    (19, "D", None, None, None, "scanned_duplicate_of_15"),
    (20, "B", date(2026, 4, 14), date(2026, 4, 29), 6, None),
]

EXPECTED_ISSUES = {
    "subtotal_misprint": ["lines_sum_to_subtotal"],
    "due_before_invoice": ["due_before_invoice"],
    "line_math_error": ["line_math"],
    "missing_invoice_number": ["missing_required_field"],
    "total_misprint": ["total_equals_subtotal_plus_tax"],
    "scanned_duplicate_of_15": ["duplicate_invoice"],
}

# --------------------------------------------------------------------- content

def make_lines(vendor: dict, key: str, n: int, multipage: bool = False) -> list[dict]:
    items = vendor["items"]
    picks = random.sample(items, k=min(n, len(items))) if n <= len(items) else items[:n]
    lines = []
    for desc, price in picks:
        if key == "A":
            qty = random.choice([1, 2, 3, 4, 5, 6, 10, 12]) if not multipage else random.choice([1, 2, 3, 5])
        elif key == "B":
            qty = random.choice([50, 100, 200, 250, 500, 1000, 1200]) if price < 500 else random.choice([2, 4, 5, 8, 10, 12])
        elif key == "C":
            qty = random.choice([1, 1, 2, 3, 4.5, 6, 6.5, 12]) if "(" in desc else 1
        else:
            qty = random.choice([1, 1, 2, 3])
        amount = round(qty * price, 2)
        lines.append({"description": desc, "quantity": qty, "unit_price": float(price), "amount": amount})
    return lines


def build_invoice(no: int, key: str, inv_date: date, due: date, n_lines: int, case: str | None, seq: dict) -> dict:
    v = VENDORS[key]
    number = f"{v['prefix']}{v['start_no'] + seq[key]:04d}" if key != "C" else f"{v['prefix']}{v['start_no'] + seq[key] * 7}"
    seq[key] += 1
    lines = make_lines(v, key, n_lines, multipage=(case == "multipage"))
    if case == "line_math_error":
        # two digits swapped in one printed amount (174,000 -> 147,000);
        # the printed subtotal adds up the printed amounts, so only the line check fails
        big = max(lines, key=lambda l: l["amount"])
        big["amount"] = transpose_digits(big["amount"])
    subtotal = round(sum(l["amount"] for l in lines), 2)
    if case == "subtotal_misprint":
        subtotal = round(subtotal + 20.00, 2)
    if v["tax_rate"] is None:
        tax = None
    elif v["currency"] == "PKR" and key == "D":
        tax = float(round(subtotal * v["tax_rate"] / 100))
    else:
        tax = round(subtotal * v["tax_rate"] / 100, 2)
    total = round(subtotal + (tax or 0), 2)
    if case == "total_misprint":
        total = round(total + 1000, 2)
    return {
        "file": f"INV-{no:02d}.pdf",
        "vendor_key": key,
        "vendor_name": v["name"],
        "invoice_number": None if case == "missing_invoice_number" else number,
        "invoice_date": inv_date.isoformat(),
        "due_date": due.isoformat(),
        "currency": v["currency"],
        "customer_name": random.choice(v["customers"]),
        "line_items": lines,
        "subtotal": subtotal,
        "tax_rate_percent": v["tax_rate"],
        "tax_amount": tax,
        "total": total,
        "case": case,
        "scanned": False,
        "expected_issues": EXPECTED_ISSUES.get(case, []),
    }

# --------------------------------------------------------------------- drawing

def money(inv: dict, x: float) -> str:
    key = inv["vendor_key"]
    if key in ("A", "C"):
        return f"${fmt_intl(x)}"
    if key == "B":
        return fmt_intl(x)
    return fmt_lakh(x)


def render_pdf(inv: dict, path: Path) -> None:
    v = VENDORS[inv["vendor_key"]]
    W, H = v["page"]
    c = canvas.Canvas(str(path), pagesize=v["page"])
    c.setTitle(f"Invoice {inv['invoice_number'] or ''}".strip())
    c.setAuthor(v["name"])
    lines = inv["line_items"]
    per_page = 22
    pages = [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[]]
    n_pages = len(pages)
    line_no = 0
    for p, chunk in enumerate(pages, start=1):
        y = draw_header(c, inv, v, W, H, first=(p == 1))
        y, line_no = draw_lines(c, inv, chunk, W, y, line_no)
        if p == n_pages:
            draw_totals(c, inv, v, W, y)
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.grey)
        c.drawCentredString(W / 2, 28, f"Page {p} of {n_pages}")
        c.showPage()
    c.save()


def fmt_date(inv: dict, iso: str) -> str:
    d = date.fromisoformat(iso)
    key = inv["vendor_key"]
    if key == "A":
        return d.strftime("%b %d, %Y")
    if key == "B":
        return d.strftime("%d/%m/%Y")
    if key == "C":
        return d.isoformat()
    return f"{d.day} {d.strftime('%B %Y')}"


def draw_header(c, inv, v, W, H, first: bool) -> float:
    key = inv["vendor_key"]
    number = inv["invoice_number"] or ""
    c.setFillColor(colors.black)
    if key == "A":
        c.setFont("Helvetica-Bold", 16)
        c.drawString(50, H - 60, v["name"])
        c.setFont("Helvetica", 9)
        for i, a in enumerate(v["address"]):
            c.drawString(50, H - 76 - 12 * i, a)
        c.setFont("Helvetica-Bold", 22)
        c.setFillColor(colors.HexColor("#6A1B9A"))
        c.drawRightString(W - 50, H - 60, "INVOICE")
        c.setFillColor(colors.black)
        c.setFont("Helvetica", 9.5)
        c.drawRightString(W - 50, H - 80, f"Invoice No.: {number}")
        c.drawRightString(W - 50, H - 93, f"Invoice Date: {fmt_date(inv, inv['invoice_date'])}")
        c.drawRightString(W - 50, H - 106, f"Due Date: {fmt_date(inv, inv['due_date'])}")
        if first:
            c.setFont("Helvetica-Bold", 10)
            c.drawString(50, H - 140, "Bill To")
            c.setFont("Helvetica", 10)
            c.drawString(50, H - 154, inv["customer_name"])
            return H - 190
        c.setFont("Helvetica-Oblique", 9)
        c.drawString(50, H - 140, f"Invoice {number} (continued)")
        return H - 165
    if key == "B":
        c.setFont("Helvetica-Bold", 17)
        c.drawCentredString(W / 2, H - 55, v["name"].upper())
        c.setFont("Helvetica", 9)
        c.drawCentredString(W / 2, H - 70, ", ".join(v["address"]))
        c.setStrokeColor(colors.HexColor("#1F4E79"))
        c.setLineWidth(1.2)
        c.line(40, H - 80, W - 40, H - 80)
        c.setFont("Helvetica-Bold", 13)
        c.drawCentredString(W / 2, H - 100, "TAX INVOICE")
        c.setFont("Helvetica", 9.5)
        c.drawString(45, H - 125, f"Invoice No: {number}")
        c.drawString(45, H - 138, f"Invoice Date: {fmt_date(inv, inv['invoice_date'])}")
        c.drawString(45, H - 151, f"Due Date: {fmt_date(inv, inv['due_date'])}")
        c.drawString(W / 2 + 20, H - 125, "Customer:")
        c.setFont("Helvetica-Bold", 9.5)
        c.drawString(W / 2 + 75, H - 125, inv["customer_name"])
        c.setFont("Helvetica", 9.5)
        c.drawString(W / 2 + 20, H - 138, "Currency: PKR")
        return H - 180
    if key == "C":
        c.setFont("Helvetica-Bold", 14)
        c.drawString(50, H - 55, v["name"].upper())
        c.setFont("Helvetica", 8.5)
        c.drawString(50, H - 69, " | ".join(v["address"]))
        c.setFont("Helvetica", 9.5)
        c.drawString(W - 210, H - 55, f"Invoice #  {number}")
        c.drawString(W - 210, H - 68, f"Date      {fmt_date(inv, inv['invoice_date'])}")
        c.drawString(W - 210, H - 81, f"Due       {fmt_date(inv, inv['due_date'])}")
        c.setFont("Helvetica-Bold", 9.5)
        c.drawString(50, H - 110, "Billed to:")
        c.setFont("Helvetica", 9.5)
        c.drawString(105, H - 110, inv["customer_name"])
        c.setStrokeColor(colors.black)
        c.setLineWidth(0.6)
        c.line(50, H - 122, W - 50, H - 122)
        return H - 150
    # D
    c.setFillColor(colors.HexColor("#E65100"))
    c.rect(0, H - 95, W, 95, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(40, H - 50, v["name"])
    c.setFont("Helvetica", 9)
    c.drawString(40, H - 66, ", ".join(v["address"]))
    c.setFont("Helvetica-Bold", 14)
    c.drawRightString(W - 40, H - 50, "INVOICE")
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, H - 125, "Bill To")
    c.drawString(W / 2 + 30, H - 125, "Invoice Details")
    c.setFont("Helvetica", 9.5)
    c.drawString(40, H - 140, inv["customer_name"])
    c.drawString(W / 2 + 30, H - 140, f"Invoice No.   {number}")
    c.drawString(W / 2 + 30, H - 153, f"Date          {fmt_date(inv, inv['invoice_date'])}")
    c.drawString(W / 2 + 30, H - 166, f"Payment Due   {fmt_date(inv, inv['due_date'])}")
    return H - 195


def table_spec(inv, W):
    key = inv["vendor_key"]
    if key == "A":
        return [("Description", 50, "l"), ("Qty", 330, "r"), ("Unit Price", 430, "r"), ("Amount", W - 50, "r")], colors.HexColor("#6A1B9A")
    if key == "B":
        return [("#", 45, "l"), ("Item", 65, "l"), ("Qty", 330, "r"), ("Rate", 425, "r"), ("Amount", W - 45, "r")], colors.HexColor("#1F4E79")
    if key == "C":
        return [("Service", 50, "l"), ("Hours/Units", 360, "r"), ("Rate", 450, "r"), ("Line Total", W - 50, "r")], None
    return [("Description", 40, "l"), ("Qty", 330, "r"), ("Unit Rate (Rs.)", 440, "r"), ("Total (Rs.)", W - 40, "r")], colors.HexColor("#E65100")


def draw_lines(c, inv, chunk, W, y, line_no):
    key = inv["vendor_key"]
    cols, fill = table_spec(inv, W)
    left, right = cols[0][1] - 5, cols[-1][1] + 5
    if fill is not None:
        c.setFillColor(fill)
        c.rect(left, y - 5, right - left, 18, stroke=0, fill=1)
        c.setFillColor(colors.white)
    else:
        c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 9.5)
    for label, x, a in cols:
        (c.drawString if a == "l" else c.drawRightString)(x, y, label)
    c.setFillColor(colors.black)
    y -= 22
    c.setFont("Helvetica", 9.5)
    for ln in chunk:
        line_no += 1
        if key == "A":
            vals = [ln["description"], fmt_qty(ln["quantity"]), money(inv, ln["unit_price"]), money(inv, ln["amount"])]
        elif key == "B":
            vals = [str(line_no), ln["description"], fmt_qty(ln["quantity"], thousands=True), fmt_intl(ln["unit_price"]), fmt_intl(ln["amount"])]
        elif key == "C":
            vals = [ln["description"], fmt_qty(ln["quantity"]), money(inv, ln["unit_price"]), money(inv, ln["amount"])]
        else:
            vals = [ln["description"], fmt_qty(ln["quantity"]), fmt_lakh(ln["unit_price"]), fmt_lakh(ln["amount"])]
        for (label, x, a), val in zip(cols, vals):
            (c.drawString if a == "l" else c.drawRightString)(x, y, val)
        c.setStrokeColor(colors.HexColor("#DDDDDD"))
        c.setLineWidth(0.4)
        c.line(left, y - 5, right, y - 5)
        y -= 17
    return y, line_no


def draw_totals(c, inv, v, W, y):
    key = inv["vendor_key"]
    y -= 12
    label_x = W - 250
    rows = []
    if key == "A":
        rows = [("Subtotal", money(inv, inv["subtotal"])),
                (f"Sales Tax ({v['tax_rate']:g}%)", money(inv, inv["tax_amount"])),
                ("Total Due", money(inv, inv["total"]))]
    elif key == "B":
        rows = [("Sub Total", fmt_intl(inv["subtotal"])),
                (f"GST @ {v['tax_rate']:g}%", fmt_intl(inv["tax_amount"])),
                ("Grand Total (PKR)", fmt_intl(inv["total"]))]
    elif key == "C":
        rows = [("Subtotal", money(inv, inv["subtotal"])),
                ("Total Due (USD)", money(inv, inv["total"]))]
    else:
        rows = [("Subtotal", fmt_lakh(inv["subtotal"])),
                (f"Sales Tax ({v['tax_rate']:g}%)", fmt_lakh(inv["tax_amount"])),
                ("Amount Payable (Rs.)", fmt_lakh(inv["total"]))]
    for i, (label, val) in enumerate(rows):
        last = i == len(rows) - 1
        c.setFont("Helvetica-Bold" if last else "Helvetica", 10.5 if last else 9.5)
        c.drawString(label_x, y, label)
        c.drawRightString(W - 50 if key in ("A", "C") else W - 42, y, val)
        y -= 16
    y -= 20
    c.setFont("Helvetica-Oblique", 8.5)
    c.setFillColor(colors.HexColor("#444444"))
    notes = {
        "A": "Thank you for your business. Payment terms: Net 30.",
        "B": "Please pay by bank transfer within 15 days. Goods once sold are not returnable.",
        "C": "Payable within 30 days. Late payments incur a 1.5% monthly service charge.",
        "D": "Amounts in Pakistani Rupees. Payment due within 15 days of the invoice date.",
    }
    c.drawString(50 if key in ("A", "C") else 40, y, notes[key])
    c.setFillColor(colors.black)


def make_scanned(src_pdf: Path, dst_pdf: Path, seed: int) -> None:
    """Rasterize page 1, add scan artefacts, and save an image-only PDF (no text layer)."""
    import pypdfium2 as pdfium

    rnd = random.Random(seed)
    pdf = pdfium.PdfDocument(str(src_pdf))
    page = pdf[0]
    img = page.render(scale=110 / 72).to_pil().convert("L")
    pdf.close()
    img = img.rotate(rnd.uniform(0.6, 1.1), resample=Image.BICUBIC, expand=True, fillcolor=255)
    noise = Image.effect_noise(img.size, 18).convert("L")
    img = Image.blend(img, noise, 0.06).filter(ImageFilter.GaussianBlur(0.5))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=55)
    Image.open(buf).convert("RGB").save(dst_pdf, "PDF", resolution=110)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    seq = {k: 0 for k in VENDORS}
    truth = {}
    for no, key, inv_date, due, n_lines, case in PLAN:
        if case == "scanned_duplicate_of_15":
            src = dict(truth["INV-15.pdf"])
            inv = {**src, "file": f"INV-{no:02d}.pdf", "case": case, "scanned": True,
                   "expected_issues": EXPECTED_ISSUES[case]}
            make_scanned(OUT_DIR / "INV-15.pdf", OUT_DIR / inv["file"], seed=no)
        else:
            inv = build_invoice(no, key, inv_date, due, n_lines, case, seq)
            if case == "scanned":
                tmp = OUT_DIR / f"_tmp_{no}.pdf"
                render_pdf(inv, tmp)
                make_scanned(tmp, OUT_DIR / inv["file"], seed=no)
                tmp.unlink()
                inv["scanned"] = True
            else:
                render_pdf(inv, OUT_DIR / inv["file"])
        truth[inv["file"]] = inv
    TRUTH_PATH.write_text(json.dumps(truth, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(truth)} invoices to {OUT_DIR} and ground truth to {TRUTH_PATH}")


if __name__ == "__main__":
    main()
