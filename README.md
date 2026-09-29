# Invoice PDF to Excel, with validation checks

Turns a folder of invoice PDFs, including scanned ones, into one Excel workbook and flags the invoices that need a person to look at them: missing fields, arithmetic that doesn't add up, impossible dates and duplicates.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Claude](https://img.shields.io/badge/LLM-Claude%20Haiku%204.5-orange)
![Excel](https://img.shields.io/badge/Output-Excel-green)

## Results on the test set

This section is filled in after the first real run: field accuracy, planted problems caught, cost per invoice and run time. The full scoring will be in `output/evaluation.md`.

## What it does

1. **Reads each invoice with Claude.** The PDF goes to Claude Haiku 4.5 as a document, so text-based and scanned invoices are handled the same way. Structured outputs force every reply into the same fixed format: vendor, invoice number, dates, currency, customer, every line item, subtotal, tax and total. The prompt tells Claude to leave a field empty when it isn't printed rather than guess; INV-12, with a blank invoice number, tests this.
2. **Checks the numbers.** Eight rules run on every invoice:
   - required fields are present (vendor, invoice number, invoice date, total)
   - dates are real calendar dates
   - the due date is not before the invoice date
   - quantity x unit price equals each line amount
   - the line amounts add up to the subtotal
   - the tax matches the printed rate
   - the total equals subtotal plus tax
   - no vendor sends the same invoice number twice
3. **Writes one Excel workbook** with four sheets: Summary, Invoices (each marked OK or Needs review), Line items, and Issues (what failed, the expected value and the value found).
4. **Scores itself.** Because the test invoices were generated from known data, every extracted field is compared with the true value, and every flag with the problems that were planted.

## The test set

20 synthetic invoices from 4 fictional vendors, each with its own layout, date format and number format:

| Vendor | Currency | Dates printed as | Numbers printed as |
|---|---|---|---|
| Orchid Office Supply Co. | USD | Mar 03, 2026 | $1,553.40 |
| Kestrel Packaging Ltd. | PKR | 28/03/2026 | 157,500.00 |
| Harbor Line Freight | USD | 2026-03-23 | $1,506.90 (no tax line) |
| Saffron Print Studio | PKR | 9 March 2026 | 1,18,150 (lakh grouping) |

It includes a two-page invoice with 30 lines and two scanned invoices with no text layer. Six invoices carry a planted problem:

| File | Planted problem | Check that should catch it |
|---|---|---|
| INV-04 | Subtotal misprinted ($20 too high) | Line amounts add up to the subtotal |
| INV-06 | Due date two weeks before the invoice date | Due date not before invoice date |
| INV-09 | Two digits swapped in one line amount (174,000 printed as 147,000) | Quantity x unit price |
| INV-12 | Invoice number left blank | Required fields |
| INV-16 | Total 1,000 higher than subtotal plus tax | Total equals subtotal plus tax |
| INV-19 | Scanned copy of INV-15 (same vendor and number) | Duplicate invoice |

All companies, people and addresses are made up.

## Run it

You need Python 3.10 or newer and an Anthropic API key. These commands are for Windows PowerShell; on macOS or Linux use `python3` and `export ANTHROPIC_API_KEY=...`.

```powershell
git clone https://github.com/sarfrazmangrio/invoice-extraction-demo.git
cd invoice-extraction-demo
python -m pip install -r requirements.txt

# 1. Check the setup without calling the API (uses the known answers)
python src\run.py --mock

# 2. Real run. The key is set for this window only and never saved in the project.
$env:ANTHROPIC_API_KEY = "paste-your-key-here"
python src\run.py
```

The real run writes to `output/`:

- `invoices_extracted.xlsx`: the workbook described above
- `evaluation.md`: accuracy, planted problems caught, cost and every mismatch
- `run_stats.json`: model, tokens, cost and time
- `extracted/`: Claude's raw result for each invoice. A rerun reuses these, so each invoice is billed once; pass `--force` to extract again.

Other options: `--limit 3` processes only the first 3 invoices, and `--input <folder>` points it at your own PDFs. Scoring runs only on the test set, or when you pass `--truth` with a file of known answers.

## How it works

```
data/invoices/*.pdf
      |
      v
extractor.py   Claude Haiku 4.5, PDF as a document block, fixed JSON schema (schema.py)
      |
      v
validate.py    8 checks, one Issues row per problem
      |
      v
to_excel.py    Summary | Invoices | Line items | Issues
      |
      v
evaluate.py    field-by-field accuracy and planted problems caught (test set only)
```

[HOW_IT_WORKS.md](HOW_IT_WORKS.md) explains each design choice: why the model copies values instead of fixing them, how the checks tolerate rounding, what the accuracy numbers mean, and what the run costs.

## Repo layout

```text
src/
  run.py                  # runs the whole pipeline
  extractor.py            # Claude call, system prompt, cost per call
  schema.py               # the fixed output format
  validate.py             # the 8 checks
  to_excel.py             # the Excel workbook
  evaluate.py             # scoring against the known answers
  generate_invoices.py    # rebuilds the test set (needs requirements-dev.txt)
data/
  invoices/               # the 20 test PDFs
  ground_truth.json       # the true value of every field
tests/test_pipeline.py    # 20 tests; no API key needed
output/                   # results of the real run
```

Run the tests with `pip install -r requirements-dev.txt` then `python -m pytest`.

## Limitations

- The invoices are synthetic. Real invoices vary more: stamps, handwriting, poor scans, several currencies on one page.
- Dates written with slashes are read day first (DD/MM/YYYY). That is set in the prompt; real use would set it per vendor.
- For client work, the fields, the checks and the Excel columns would be matched to the client's accounting system, and accuracy measured on a sample of their own invoices.

## License

MIT. See [LICENSE](LICENSE).
