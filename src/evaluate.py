"""Score the extraction against the ground truth of the synthetic invoices.

Every field Claude returned is compared with the value printed on the invoice,
and the issues the validation step raised are compared with the problems that
were planted. This is only possible because the test invoices were generated
from known data; with real invoices you would spot-check a sample by hand.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from schema import HEADER_FIELDS, LINE_FIELDS

NUMERIC = {"subtotal", "tax_rate_percent", "tax_amount", "total", "quantity", "unit_price", "amount"}


def _norm_text(value):
    if value is None:
        return None
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", str(value).casefold())   # 1,000 -> 1000
    text = re.sub(r"[.,()]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def same(field: str, got, want) -> bool:
    if field in NUMERIC:
        if got is None or want is None:
            return got is None and want is None
        try:
            return abs(float(got) - float(want)) < 0.005
        except (TypeError, ValueError):
            return False
    if field == "currency":
        return (got or "").strip().upper() == (want or "").strip().upper()
    if field in ("invoice_date", "due_date"):
        return (got or None) == (want or None)
    return _norm_text(got) == _norm_text(want)


def align_lines(true_lines: list[dict], got_lines: list[dict]) -> list[tuple]:
    """Pair true and extracted lines by description, so one missing or extra line
    counts as one line wrong instead of shifting every line after it."""
    a = [_norm_text(line.get("description")) for line in true_lines]
    b = [_norm_text(line.get("description")) for line in got_lines]
    pairs = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        n = min(i2 - i1, j2 - j1) if tag in ("equal", "replace") else 0
        pairs += list(zip(range(i1, i1 + n), range(j1, j1 + n)))
        pairs += [(i, None) for i in range(i1 + n, i2)]
        pairs += [(None, j) for j in range(j1 + n, j2)]
    return pairs


def evaluate(records: list[dict], truth: dict, issues: list[dict], failures: list[dict]) -> dict:
    header = defaultdict(lambda: [0, 0])      # field -> [correct, total]
    lines = defaultdict(lambda: [0, 0])
    mismatches = []
    fully_correct = 0
    for rec in records:
        want = truth.get(rec["file"])
        if want is None:
            continue
        got = rec["data"]
        ok_invoice = True
        for f in HEADER_FIELDS:
            hit = same(f, got.get(f), want.get(f))
            header[f][0] += hit
            header[f][1] += 1
            if not hit:
                ok_invoice = False
                mismatches.append({"file": rec["file"], "field": f, "extracted": got.get(f), "true": want.get(f)})
        g_lines, w_lines = got.get("line_items") or [], want.get("line_items") or []
        if len(g_lines) != len(w_lines):
            ok_invoice = False
            mismatches.append({"file": rec["file"], "field": "line count", "extracted": len(g_lines), "true": len(w_lines)})
        for w_i, g_i in align_lines(w_lines, g_lines):
            g = g_lines[g_i] if g_i is not None else {}
            w = w_lines[w_i] if w_i is not None else {}
            label = f"line {w_i + 1}" if w_i is not None else f"extra line {g_i + 1}"
            for f in LINE_FIELDS:
                hit = bool(g) and bool(w) and same(f, g.get(f), w.get(f))
                lines[f][0] += hit
                lines[f][1] += 1
                if not hit:
                    ok_invoice = False
                    mismatches.append({"file": rec["file"], "field": f"{label} {f}", "extracted": g.get(f), "true": w.get(f)})
        fully_correct += ok_invoice

    flagged = defaultdict(set)
    for issue in issues:
        flagged[issue["file"]].add(issue["rule"])
    planted, found, false_flags = [], [], []
    for file, want in truth.items():
        expected = set(want.get("expected_issues", []))
        for rule in expected:
            planted.append((file, rule))
            if rule in flagged.get(file, set()):
                found.append((file, rule))
        for rule in flagged.get(file, set()) - expected:
            false_flags.append((file, rule))

    def pct(c, t):
        return round(100 * c / t, 1) if t else None

    header_correct = sum(v[0] for v in header.values())
    header_total = sum(v[1] for v in header.values())
    line_correct = sum(v[0] for v in lines.values())
    line_total = sum(v[1] for v in lines.values())
    return {
        "invoices_in_truth": len(truth),
        "invoices_extracted": len(records),
        "extraction_failures": [f["file"] for f in failures],
        "header_accuracy_pct": pct(header_correct, header_total),
        "header_fields_checked": header_total,
        "line_item_accuracy_pct": pct(line_correct, line_total),
        "line_item_fields_checked": line_total,
        "all_fields_accuracy_pct": pct(header_correct + line_correct, header_total + line_total),
        "invoices_fully_correct": fully_correct,
        "by_field": {f: pct(*header[f]) for f in HEADER_FIELDS} | {f"line {f}": pct(*lines[f]) for f in LINE_FIELDS},
        "planted_issues": len(planted),
        "planted_issues_found": len(found),
        "missed_issues": sorted(set(planted) - set(found)),
        "false_flags": sorted(false_flags),
        "mismatches": mismatches,
    }


def write_report(result: dict, stats: dict, path_md: Path, path_json: Path) -> None:
    path_json.write_text(json.dumps({"evaluation": result, "run": stats}, indent=2, default=str), encoding="utf-8")
    r = result
    lines = [
        "# Evaluation against the ground truth",
        "",
        f"Model: `{stats.get('model')}` | invoices: {r['invoices_extracted']} of {r['invoices_in_truth']} extracted"
        + (f" | failed: {', '.join(r['extraction_failures'])}" if r["extraction_failures"] else ""),
        "",
        "## Field accuracy",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Header fields correct | {r['header_accuracy_pct']}% of {r['header_fields_checked']} |",
        f"| Line-item fields correct | {r['line_item_accuracy_pct']}% of {r['line_item_fields_checked']} |",
        f"| All fields correct | {r['all_fields_accuracy_pct']}% |",
        f"| Invoices with every field correct | {r['invoices_fully_correct']} of {r['invoices_extracted']} |",
        "",
        "| Field | Accuracy |",
        "|---|---|",
    ]
    lines += [f"| {field} | {acc}% |" for field, acc in r["by_field"].items()]
    lines += [
        "",
        "## Validation checks",
        "",
        f"- Planted problems found: **{r['planted_issues_found']} of {r['planted_issues']}**",
        f"- Missed: {', '.join(f'{f} ({rule})' for f, rule in r['missed_issues']) or 'none'}",
        f"- Flags matching no planted problem: {', '.join(f'{f} ({rule})' for f, rule in r['false_flags']) or 'none'}",
        "  (a flag here is either a false alarm or a check catching an extraction error listed below)",
        "",
        "## Cost and speed",
        "",
        f"- Tokens: {stats.get('input_tokens'):,} input, {stats.get('output_tokens'):,} output",
        f"- Cost: {'$' + format(stats['cost_usd'], '.4f') if stats.get('cost_usd') is not None else 'unknown (add the model price in extractor.py)'}"
        + (f" (about ${stats['cost_usd'] / max(r['invoices_extracted'], 1):.4f} per invoice)" if stats.get("cost_usd") else ""),
        f"- Time: {stats.get('api_seconds')} s of extraction calls"
        + (f" ({stats['from_saved']} invoices reused from saved results)" if stats.get("from_saved") else ""),
        "",
        "## Every mismatch",
        "",
    ]
    if r["mismatches"]:
        lines += ["| File | Field | Extracted | True value |", "|---|---|---|---|"]
        lines += [f"| {m['file']} | {m['field']} | {m['extracted']} | {m['true']} |" for m in r["mismatches"]]
    else:
        lines.append("None.")
    path_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
