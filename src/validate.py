"""Check extracted invoices for missing data, broken arithmetic and duplicates.

Extraction copies what is printed; these rules decide whether the printed
numbers make sense. Each problem becomes one row in the Issues sheet.
"""
from __future__ import annotations

import re
from datetime import date

REQUIRED = ["vendor_name", "invoice_number", "invoice_date", "total"]
MONEY_TOLERANCE = 0.011     # amounts are printed to 2 decimals

RULES = {
    "missing_required_field": "A required field is blank or missing",
    "invalid_date": "A date is not a valid calendar date",
    "due_before_invoice": "The due date is before the invoice date",
    "line_math": "Quantity x unit price does not equal the line amount",
    "lines_sum_to_subtotal": "The line amounts do not add up to the subtotal",
    "tax_matches_rate": "The tax amount does not match the printed rate",
    "total_equals_subtotal_plus_tax": "The total does not equal subtotal plus tax",
    "duplicate_invoice": "Same vendor and invoice number as an earlier invoice",
}


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _parse_date(value):
    if _blank(value):
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return "invalid"


def _norm_key(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def _issue(file, data, rule, message, expected=None, found=None, severity="error"):
    return {
        "file": file,
        "invoice_number": data.get("invoice_number"),
        "vendor_name": data.get("vendor_name"),
        "rule": rule,
        "severity": severity,
        "message": message,
        "expected": expected,
        "found": found,
    }


def validate_invoice(file: str, data: dict) -> list[dict]:
    issues = []
    for field in REQUIRED:
        if _blank(data.get(field)):
            issues.append(_issue(file, data, "missing_required_field", f"{field} is blank or missing",
                                 expected="a value", found="blank"))

    inv_date, due_date = _parse_date(data.get("invoice_date")), _parse_date(data.get("due_date"))
    for field, parsed in (("invoice_date", inv_date), ("due_date", due_date)):
        if parsed == "invalid":
            issues.append(_issue(file, data, "invalid_date", f"{field} is not a valid date", found=data.get(field)))
    if isinstance(inv_date, date) and isinstance(due_date, date) and due_date < inv_date:
        issues.append(_issue(file, data, "due_before_invoice", "Due date is before the invoice date",
                             expected=f"on or after {inv_date}", found=str(due_date)))

    lines = data.get("line_items") or []
    for i, line in enumerate(lines, start=1):
        q, p, a = line.get("quantity"), line.get("unit_price"), line.get("amount")
        if None not in (q, p, a) and abs(q * p - a) > MONEY_TOLERANCE:
            issues.append(_issue(file, data, "line_math", f"Line {i} ({line.get('description')}): {q} x {p} = {round(q * p, 2)}",
                                 expected=round(q * p, 2), found=a))

    subtotal, tax, total = data.get("subtotal"), data.get("tax_amount"), data.get("total")
    amounts = [line.get("amount") for line in lines]
    if lines and subtotal is not None and None not in amounts and abs(sum(amounts) - subtotal) > MONEY_TOLERANCE:
        issues.append(_issue(file, data, "lines_sum_to_subtotal", "Line amounts do not add up to the subtotal",
                             expected=round(sum(amounts), 2), found=subtotal))

    rate = data.get("tax_rate_percent")
    # vendors that round tax to whole units (common for rupees) can be up to 0.5 off
    tax_tolerance = 0.5 + MONEY_TOLERANCE if tax is not None and float(tax).is_integer() else MONEY_TOLERANCE
    if None not in (rate, tax, subtotal) and abs(subtotal * rate / 100 - tax) > tax_tolerance:
        issues.append(_issue(file, data, "tax_matches_rate", f"Tax at {rate}% of the subtotal should be about {round(subtotal * rate / 100, 2)}",
                             expected=round(subtotal * rate / 100, 2), found=tax, severity="warning"))

    if subtotal is not None and total is not None and abs(subtotal + (tax or 0) - total) > MONEY_TOLERANCE:
        issues.append(_issue(file, data, "total_equals_subtotal_plus_tax", "Total does not equal subtotal plus tax",
                             expected=round(subtotal + (tax or 0), 2), found=total))
    return issues


def validate_batch(records: list[dict]) -> list[dict]:
    """records: [{"file": ..., "data": {...}}] in processing order."""
    issues, seen = [], {}
    for rec in records:
        file, data = rec["file"], rec["data"]
        issues.extend(validate_invoice(file, data))
        if not _blank(data.get("invoice_number")) and not _blank(data.get("vendor_name")):
            key = (_norm_key(data["vendor_name"]), _norm_key(data["invoice_number"]))
            if key in seen:
                issues.append(_issue(file, data, "duplicate_invoice",
                                     f"Same vendor and invoice number as {seen[key]}", expected="a new invoice number",
                                     found=data["invoice_number"]))
            else:
                seen[key] = file
    return issues
