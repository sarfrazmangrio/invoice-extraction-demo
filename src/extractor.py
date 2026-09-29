"""Extract one invoice PDF into the fixed format with Claude.

The PDF is sent as a document block, so Claude reads both the text layer and the
page images; scanned (image-only) invoices work the same way. Structured outputs
force the reply to match INVOICE_SCHEMA.
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from schema import INVOICE_SCHEMA

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# USD per million tokens (input, output), from platform.claude.com/docs/en/about-claude/pricing.
# Add a row here before using another model, or the cost is reported as unknown.
PRICES_PER_MTOK = {
    "claude-haiku-4-5": (1.00, 5.00),
}

SYSTEM_PROMPT = """You extract data from invoices for an accounts-payable team.

Rules:
- Copy values exactly as printed. Do not calculate, correct or fill in anything; the numbers are checked later.
- If a field is blank or not printed, return null. Never invent an invoice number, date or amount.
- Dates: return YYYY-MM-DD. Dates written with slashes on these invoices are day first (DD/MM/YYYY).
- Amounts and quantities: plain numbers with no currency symbols or thousands separators. Read lakh-style grouping correctly (1,23,450 is 123450).
- currency: the ISO 4217 code (USD, PKR, ...), taken from the symbol, code or wording on the invoice.
- vendor_name is the company that issued the invoice; customer_name is the party being billed.
- line_items: one entry per printed line, in order, across all pages. Do not add subtotal, tax or total rows as line items.
- tax_rate_percent: the printed rate (18 for 18%); tax_amount: the printed tax amount. Both null if there is no tax line."""


class ExtractionError(RuntimeError):
    """The call succeeded (and was billed) but the reply can't be used."""

    def __init__(self, message: str, usage: dict | None = None, cost: float | None = None):
        super().__init__(message)
        self.usage = usage or {"input_tokens": 0, "output_tokens": 0}
        self.cost = cost


def price_for(model: str) -> tuple[float, float] | None:
    for prefix, prices in PRICES_PER_MTOK.items():
        if model.startswith(prefix):
            return prices
    return None


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    prices = price_for(model)
    if prices is None:
        return None
    return round((input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000, 6)


class ClaudeExtractor:
    """Calls the Claude API. Needs the ANTHROPIC_API_KEY environment variable."""

    def __init__(self, model: str = DEFAULT_MODEL, max_retries: int = 4, client=None):
        if client is None:
            from anthropic import Anthropic  # imported here so --mock runs without the SDK

            # The SDK retries rate limits, overloads and connection errors with backoff.
            client = Anthropic(max_retries=max_retries)
        self.client = client
        self.model = model

    def extract(self, pdf_path: Path) -> dict:
        pdf_b64 = base64.standard_b64encode(Path(pdf_path).read_bytes()).decode("ascii")
        started = time.time()
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}},
                    {"type": "text", "text": "Extract this invoice."},
                ],
            }],
            output_config={"format": {"type": "json_schema", "schema": INVOICE_SCHEMA}},
        )
        seconds = round(time.time() - started, 2)
        usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
        cost = cost_usd(self.model, usage["input_tokens"], usage["output_tokens"])
        if response.stop_reason != "end_turn":
            raise ExtractionError(f"stopped early (stop_reason={response.stop_reason})", usage, cost)
        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ExtractionError(f"reply was not valid JSON: {exc}", usage, cost) from exc
        return {"data": data, "model": response.model, "usage": usage, "cost_usd": cost, "seconds": seconds}


class MockExtractor:
    """Returns the ground truth instead of calling the API.

    Used to test the pipeline (validation, Excel, scoring) without an API key or cost.
    """

    def __init__(self, truth: dict):
        self.truth = truth
        self.model = "mock"

    def extract(self, pdf_path: Path) -> dict:
        rec = self.truth[Path(pdf_path).name]
        data = {k: rec[k] for k in INVOICE_SCHEMA["properties"]}
        return {"data": json.loads(json.dumps(data)), "model": "mock",
                "usage": {"input_tokens": 0, "output_tokens": 0}, "cost_usd": 0.0, "seconds": 0.0}
