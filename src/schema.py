"""The fixed output format for one invoice.

Claude's structured outputs guarantee the response matches this JSON schema, so
every invoice comes back with the same fields and types. Fields that are not
printed on the invoice come back as null rather than being guessed.
"""

HEADER_FIELDS = [
    "vendor_name", "invoice_number", "invoice_date", "due_date", "currency",
    "customer_name", "subtotal", "tax_rate_percent", "tax_amount", "total",
]
LINE_FIELDS = ["description", "quantity", "unit_price", "amount"]

NULLABLE_STRING = {"type": ["string", "null"]}
NULLABLE_NUMBER = {"type": ["number", "null"]}

INVOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "vendor_name": {"type": "string", "description": "Company that issued the invoice, as printed."},
        "invoice_number": {**NULLABLE_STRING, "description": "Invoice number exactly as printed; null if it is blank or missing."},
        "invoice_date": {**NULLABLE_STRING, "description": "Invoice date as YYYY-MM-DD."},
        "due_date": {**NULLABLE_STRING, "description": "Payment due date as YYYY-MM-DD; null if not printed."},
        "currency": {**NULLABLE_STRING, "description": "ISO 4217 code, e.g. USD or PKR."},
        "customer_name": {**NULLABLE_STRING, "description": "The billed party (Bill To / Customer / Billed to)."},
        "line_items": {
            "type": "array",
            "description": "One entry per printed line, in order, across all pages. Excludes subtotal, tax and total rows.",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "quantity": NULLABLE_NUMBER,
                    "unit_price": NULLABLE_NUMBER,
                    "amount": NULLABLE_NUMBER,
                },
                "required": ["description", "quantity", "unit_price", "amount"],
                "additionalProperties": False,
            },
        },
        "subtotal": {**NULLABLE_NUMBER, "description": "Printed subtotal."},
        "tax_rate_percent": {**NULLABLE_NUMBER, "description": "Printed tax rate, e.g. 18 for 18%; null if none."},
        "tax_amount": {**NULLABLE_NUMBER, "description": "Printed tax amount; null if there is no tax line."},
        "total": {**NULLABLE_NUMBER, "description": "Printed total amount due."},
    },
    "required": HEADER_FIELDS[:6] + ["line_items"] + HEADER_FIELDS[6:],
    "additionalProperties": False,
}
