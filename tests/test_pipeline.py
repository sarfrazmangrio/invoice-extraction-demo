"""Tests that run without an API key: validation rules, the schema, the Claude
request and response handling (against a fake HTTP server), and the full
pipeline in mock mode."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evaluate import evaluate  # noqa: E402
from extractor import ClaudeExtractor, ExtractionError  # noqa: E402
from schema import INVOICE_SCHEMA  # noqa: E402
from validate import validate_batch, validate_invoice  # noqa: E402

TRUTH = json.loads((ROOT / "data" / "ground_truth.json").read_text(encoding="utf-8"))


def fields(rec: dict) -> dict:
    return {k: copy.deepcopy(rec[k]) for k in INVOICE_SCHEMA["properties"]}


# ------------------------------------------------------------------ schema

def test_ground_truth_matches_schema():
    for rec in TRUTH.values():
        jsonschema.validate(fields(rec), INVOICE_SCHEMA)


def test_schema_fits_structured_output_limits():
    # every property required (no optional parameters) and at most 16 union-typed parameters
    def walk(node):
        if node.get("type") == "object":
            assert set(node["required"]) == set(node["properties"])
            assert node["additionalProperties"] is False
            for child in node["properties"].values():
                yield child
                yield from walk(child)
        if node.get("type") == "array":
            yield from walk(node["items"])
    unions = [n for n in walk(INVOICE_SCHEMA) if isinstance(n.get("type"), list)]
    assert len(unions) <= 16


# -------------------------------------------------------------- validation

def rules(file):
    return sorted({i["rule"] for i in validate_invoice(file, fields(TRUTH[file]))})


def test_clean_invoices_raise_no_issues():
    clean = [f for f, r in TRUTH.items() if not r["expected_issues"]]
    assert len(clean) == 14
    for f in clean:
        assert rules(f) == [], f


@pytest.mark.parametrize("file,expected", [
    ("INV-04.pdf", ["lines_sum_to_subtotal"]),
    ("INV-06.pdf", ["due_before_invoice"]),
    ("INV-09.pdf", ["line_math"]),
    ("INV-12.pdf", ["missing_required_field"]),
    ("INV-16.pdf", ["total_equals_subtotal_plus_tax"]),
])
def test_each_planted_problem_is_caught(file, expected):
    assert rules(file) == expected


def test_duplicate_is_flagged_on_the_later_copy():
    records = [{"file": f, "data": fields(r)} for f, r in TRUTH.items()]
    dupes = [i for i in validate_batch(records) if i["rule"] == "duplicate_invoice"]
    assert [d["file"] for d in dupes] == ["INV-19.pdf"]


def test_blank_string_counts_as_missing_and_bad_dates_are_reported():
    data = fields(TRUTH["INV-01.pdf"])
    data["invoice_number"] = "  "
    data["due_date"] = "2026-02-31"
    found = {i["rule"] for i in validate_invoice("x.pdf", data)}
    assert {"missing_required_field", "invalid_date"} <= found


def test_tax_rate_check_tolerates_rounding_but_flags_wrong_tax():
    data = fields(TRUTH["INV-07.pdf"])          # PKR, tax rounded to whole rupees
    assert validate_invoice("x.pdf", data) == []
    data["tax_amount"] += 500
    data["total"] += 500
    assert [i["rule"] for i in validate_invoice("x.pdf", data)] == ["tax_matches_rate"]


# ------------------------------------------- Claude call (fake HTTP server)

def fake_client(reply_text: str, stop_reason: str = "end_turn", seen: list | None = None):
    from anthropic import Anthropic

    try:  # newer SDK versions ship their own HTTP library, httpx2
        import httpx2 as httpx
    except ImportError:
        import httpx

    def handler(request):
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-haiku-4-5-20251001",
            "content": [{"type": "text", "text": reply_text}], "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 3200, "output_tokens": 650},
        })
    return Anthropic(api_key="test-key", http_client=httpx.Client(transport=httpx.MockTransport(handler)), max_retries=0)


def test_extractor_sends_pdf_and_schema_and_parses_reply():
    seen = []
    want = fields(TRUTH["INV-02.pdf"])
    ex = ClaudeExtractor(client=fake_client(json.dumps(want), seen=seen))
    out = ex.extract(ROOT / "data" / "invoices" / "INV-02.pdf")
    body = seen[0]
    assert body["model"] == "claude-haiku-4-5-20251001"
    assert body["output_config"]["format"]["type"] == "json_schema"
    doc = body["messages"][0]["content"][0]
    assert doc["type"] == "document" and doc["source"]["media_type"] == "application/pdf"
    assert out["data"] == want
    assert out["usage"] == {"input_tokens": 3200, "output_tokens": 650}
    assert out["cost_usd"] == pytest.approx((3200 * 1 + 650 * 5) / 1_000_000)


def test_extractor_rejects_truncated_reply():
    ex = ClaudeExtractor(client=fake_client('{"vendor_name": "Orch', stop_reason="max_tokens"))
    with pytest.raises(ExtractionError):
        ex.extract(ROOT / "data" / "invoices" / "INV-01.pdf")


# ------------------------------------------------------- full pipeline (mock)

def test_mock_run_end_to_end(tmp_path):
    import run
    from openpyxl import load_workbook

    result = run.main(["--mock", "--out", str(tmp_path)])
    ev = result["evaluation"]
    assert ev["all_fields_accuracy_pct"] == 100.0
    assert ev["planted_issues_found"] == ev["planted_issues"] == 6
    assert ev["false_flags"] == []
    wb = load_workbook(tmp_path / "invoices_extracted.xlsx")
    assert wb.sheetnames == ["Summary", "Invoices", "Line items", "Issues"]
    assert wb["Invoices"].max_row == 21            # header + 20 invoices
    assert wb["Issues"].max_row == 7               # header + 6 issues
    statuses = [c.value for c in wb["Invoices"]["B"][1:]]
    assert statuses.count("Needs review") == 6


def test_missing_key_stops_before_any_call(tmp_path, monkeypatch):
    import run

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(SystemExit) as stop:
        run.main(["--out", str(tmp_path), "--limit", "2"])
    assert "ANTHROPIC_API_KEY is not set" in str(stop.value)


def test_saved_results_of_the_real_run_reproduce_its_scores(tmp_path, monkeypatch):
    """output/extracted holds Claude's replies from the run on 30 September 2026."""
    import shutil
    import run

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    shutil.copytree(ROOT / "output" / "extracted", tmp_path / "extracted")
    result = run.main(["--out", str(tmp_path)])        # no key needed: every result is reused
    assert result["stats"]["from_saved"] == 20
    ev = result["evaluation"]
    assert ev["header_fields_checked"] + ev["line_item_fields_checked"] == 668
    assert ev["all_fields_accuracy_pct"] == 100.0
    assert ev["planted_issues_found"] == 6 and ev["false_flags"] == []


def test_evaluation_reports_a_wrong_field():
    records = [{"file": f, "data": fields(r)} for f, r in TRUTH.items()]
    records[0]["data"]["total"] = 1.0
    records[1]["data"]["line_items"][0]["quantity"] = 999
    ev = evaluate(records, TRUTH, validate_batch(records), [])
    wrong = {(m["file"], m["field"]) for m in ev["mismatches"]}
    assert ("INV-01.pdf", "total") in wrong
    assert ("INV-02.pdf", "line 1 quantity") in wrong
    assert ev["invoices_fully_correct"] == 18


def test_rejected_reply_still_reports_its_billed_usage():
    ex = ClaudeExtractor(client=fake_client('{"vendor_name": "Orch', stop_reason="max_tokens"))
    with pytest.raises(ExtractionError) as info:
        ex.extract(ROOT / "data" / "invoices" / "INV-01.pdf")
    assert info.value.usage == {"input_tokens": 3200, "output_tokens": 650}
    assert info.value.cost == pytest.approx((3200 * 1 + 650 * 5) / 1_000_000)


def test_one_missing_line_counts_as_one_wrong_line():
    records = [{"file": f, "data": fields(r)} for f, r in TRUTH.items()]
    inv03 = next(r for r in records if r["file"] == "INV-03.pdf")
    del inv03["data"]["line_items"][22]             # the first line on page 2
    ev = evaluate(records, TRUTH, validate_batch(records), [])
    line_errors = [m for m in ev["mismatches"] if m["file"] == "INV-03.pdf" and m["field"].startswith("line 23 ")]
    others = [m for m in ev["mismatches"] if m["file"] == "INV-03.pdf" and m["field"] not in ("line count",) and m not in line_errors]
    assert len(line_errors) == 4 and others == []    # only that one line's 4 fields are wrong


def test_tax_tolerance_depends_on_rounding():
    usd = fields(TRUTH["INV-01.pdf"])
    usd["tax_amount"] = round(usd["tax_amount"] + 0.5, 2)
    usd["total"] = round(usd["total"] + 0.5, 2)
    assert "tax_matches_rate" in {i["rule"] for i in validate_invoice("x.pdf", usd)}
    pkr = fields(TRUTH["INV-11.pdf"])                # tax printed in whole rupees
    pkr["subtotal"] += 2                             # 16% of 2 = 0.32, within half a rupee
    pkr["line_items"][0]["amount"] += 2
    pkr["line_items"][0]["unit_price"] = pkr["line_items"][0]["amount"] / pkr["line_items"][0]["quantity"]
    pkr["total"] += 2
    assert "tax_matches_rate" not in {i["rule"] for i in validate_invoice("x.pdf", pkr)}


def test_scoring_only_uses_known_answers(tmp_path):
    import shutil
    import run

    folder = tmp_path / "pdfs"
    folder.mkdir()
    for name in ("INV-01.pdf", "INV-02.pdf"):
        shutil.copy(ROOT / "data" / "invoices" / name, folder / name)
    with pytest.raises(SystemExit):                  # another folder: no known answers unless --truth is given
        run.main(["--mock", "--input", str(folder), "--out", str(tmp_path / "a")])
    result = run.main(["--mock", "--input", str(folder), "--truth", str(ROOT / "data" / "ground_truth.json"),
                       "--out", str(tmp_path / "b")])
    ev = result["evaluation"]
    assert ev["invoices_in_truth"] == ev["invoices_extracted"] == 2


def test_text_that_looks_like_a_formula_is_saved_as_text(tmp_path):
    from openpyxl import load_workbook
    from to_excel import write_workbook

    data = fields(TRUTH["INV-01.pdf"])
    data["line_items"][0]["description"] = "=HYPERLINK(\"http://example.com\")"
    data["customer_name"] = "Brightside\x07 Dental"
    path = write_workbook(tmp_path / "x.xlsx", [{"file": "INV-01.pdf", "data": data}], [], [], {"model": "mock"})
    wb = load_workbook(path)
    cell = wb["Line items"]["D2"]
    assert cell.data_type == "s" and cell.value.startswith("=HYPERLINK")
    assert wb["Invoices"]["H2"].value == "Brightside Dental"
