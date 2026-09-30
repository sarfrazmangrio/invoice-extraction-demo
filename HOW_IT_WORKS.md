# How it works

A walkthrough of the pipeline and the reasons behind each choice.

## 1. Reading the invoice (`extractor.py`)

Each PDF is sent to Claude as a **document block**. Claude reads every page both as text and as an image, so a scanned invoice with no text layer (INV-10, INV-19) needs no separate OCR step. The API accepts requests up to 32 MB and, for Haiku 4.5, up to 100 PDF pages per request.

The reply is constrained by **structured outputs**: the request carries a JSON schema (`schema.py`), and the API guarantees the reply matches it. Every invoice therefore comes back with the same keys and types. The schema allows `null` for fields that may be missing, and the prompt tells Claude to use it instead of guessing; INV-12, with a blank invoice number, tests this. Two limits shaped the schema: every property is listed as required (nullable instead of optional), and there are 12 nullable fields against the API's limit of 16.

The system prompt tells Claude to **copy values exactly as printed and never fix them**. This matters: if the model quietly corrected a wrong total, the error would disappear before anyone saw it. Reading and checking are separate jobs, and the checking happens in plain Python where it can be tested.

Other details:
- The prompt states two conventions: slash dates are day first, and lakh-style grouping (1,23,450) means 123450.
- The SDK retries rate limits, overloads and dropped connections with backoff (`max_retries=4`).
- Each result is saved to `output/extracted/`. A rerun reuses it, so a crash halfway through doesn't cost a second payment. Results saved with a different model are extracted again.
- One failed file is logged and skipped; the rest of the batch still runs.

## 2. Checking the numbers (`validate.py`)

Eight checks, each producing one row in the Issues sheet with the expected and found values:

| Check | Tolerance |
|---|---|
| Required fields present | blank text counts as missing |
| Valid calendar dates | |
| Due date not before invoice date | |
| Quantity x unit price = line amount | 0.01 |
| Line amounts add up to the subtotal | 0.01 |
| Tax matches the printed rate | 0.01, or half a unit when the tax is printed in whole units (common for rupees) |
| Total = subtotal + tax | 0.01 |
| No duplicate invoice number per vendor | vendor and number compared without spaces or punctuation |

The duplicate check flags the later copy (INV-19), because in accounts payable that is the one you'd stop paying.

## 3. The Excel workbook (`to_excel.py`)

Four sheets: **Summary** (counts, issues by rule, tokens and cost), **Invoices** (one row each, marked OK or Needs review), **Line items**, and **Issues**. Dates are real Excel dates and amounts use number formats, so the file can be filtered, summed or loaded into Power BI as is. Text read from an invoice is always stored as text, so a value starting with `=` can never run as a formula. If the workbook is open in Excel, the run saves a copy with a timestamp instead of failing.

## 4. Scoring (`evaluate.py`)

The test invoices were generated from known data (`generate_invoices.py`), so every field Claude returns can be compared with the true value:

- **Header accuracy**: 10 fields per invoice (vendor, number, dates, currency, customer, subtotal, tax rate, tax, total).
- **Line-item accuracy**: description, quantity, unit price and amount for every line. Lines are paired by description, so a missing or extra line counts as one wrong line instead of shifting every line after it.
- **Invoices fully correct**: every field on the invoice right.
- **Planted problems caught**, and any flags on invoices that had no planted problem.

Text is compared after lower-casing and removing thousands separators, periods, commas and brackets; numbers within 0.005; dates exactly. Every mismatch is listed at the end of `evaluation.md`, so a wrong number can be traced to the invoice and field.

With real client invoices there is no ground truth. The equivalent is to check 10 to 20 invoices by hand and compute the same accuracy.

## 5. Cost

Claude Haiku 4.5 costs $1 per million input tokens and $5 per million output tokens. A PDF page uses roughly 1,500 to 3,000 text tokens plus image tokens. In the test run, each one-page invoice used 2,750 to 3,072 input tokens (everything sent, prompt included) and 175 to 313 output tokens, which is $0.0038 to $0.0046 per invoice. The two-page, 30-line invoice used 5,129 input and 1,001 output tokens, about $0.010. `run_stats.json` records the tokens and cost of every call, including replies that were rejected (they are still billed), and how many results were reused from an earlier run. For large volumes the Batch API halves the price.

## 6. Adapting it for a client

1. Change the fields in `schema.py` to what their accounting system needs, for example a PO number or tax ID.
2. Add their rules to `validate.py`, for example matching the PO number against an open-orders list.
3. Match the Excel columns to their import template.
4. Run it on a sample of their invoices, check the results by hand, and report the accuracy before running the full batch.
