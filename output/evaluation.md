# Evaluation against the ground truth

Model: `claude-haiku-4-5-20251001` | invoices: 20 of 20 extracted

## Field accuracy

| Measure | Result |
|---|---|
| Header fields correct | 100.0% of 200 |
| Line-item fields correct | 100.0% of 468 |
| All fields correct | 100.0% |
| Invoices with every field correct | 20 of 20 |

| Field | Accuracy |
|---|---|
| vendor_name | 100.0% |
| invoice_number | 100.0% |
| invoice_date | 100.0% |
| due_date | 100.0% |
| currency | 100.0% |
| customer_name | 100.0% |
| subtotal | 100.0% |
| tax_rate_percent | 100.0% |
| tax_amount | 100.0% |
| total | 100.0% |
| line description | 100.0% |
| line quantity | 100.0% |
| line unit_price | 100.0% |
| line amount | 100.0% |

## Validation checks

- Planted problems found: **6 of 6**
- Missed: none
- Flags matching no planted problem: none
  (a flag here is either a false alarm or a check catching an extraction error listed below)

## Cost and speed

- Tokens: 61,372 input, 5,917 output
- Cost: $0.0910 (about $0.0045 per invoice)
- Time: 95.58 s of extraction calls

## Every mismatch

None.
