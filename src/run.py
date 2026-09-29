"""Run the whole pipeline: extract every PDF, validate, write Excel, and score.

    python src/run.py            # real run with Claude (needs ANTHROPIC_API_KEY)
    python src/run.py --mock     # no API call: uses the ground truth, for testing
    python src/run.py --limit 3  # only the first 3 invoices

Each extraction is saved to <out>/extracted/<file>.json. A rerun reuses those
files, so you are only billed once per invoice unless you pass --force.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evaluate import evaluate, write_report  # noqa: E402
from extractor import DEFAULT_MODEL, ClaudeExtractor, MockExtractor  # noqa: E402
from to_excel import write_workbook  # noqa: E402
from validate import validate_batch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TEST_SET = ROOT / "data" / "invoices"
TEST_TRUTH = ROOT / "data" / "ground_truth.json"


def _add_cost(stats: dict, cost) -> None:
    if stats["cost_usd"] is not None:
        stats["cost_usd"] = None if cost is None else round(stats["cost_usd"] + cost, 6)


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description="Invoice PDF to Excel extraction with validation checks")
    ap.add_argument("--input", type=Path, default=TEST_SET, help="folder of PDF invoices (default: the test set)")
    ap.add_argument("--out", type=Path, default=None, help="output folder (default: output, or output_mock with --mock)")
    ap.add_argument("--truth", type=Path, default=None,
                    help="known answers for scoring (default: data/ground_truth.json, used only with the test set)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--mock", action="store_true", help="skip the API and return the known answers")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="re-extract files that already have a saved result")
    args = ap.parse_args(argv)

    out = args.out or ROOT / ("output_mock" if args.mock else "output")
    cache = out / "extracted"
    cache.mkdir(parents=True, exist_ok=True)
    truth_path = args.truth or (TEST_TRUTH if args.input.resolve() == TEST_SET.resolve() else None)
    truth = json.loads(truth_path.read_text(encoding="utf-8")) if truth_path and truth_path.exists() else None

    if args.mock:
        if truth is None:
            sys.exit("--mock needs known answers: use it with the test set or pass --truth")
        extractor = MockExtractor(truth)
    else:
        extractor = ClaudeExtractor(model=args.model)

    files = sorted(args.input.glob("*.pdf"))[: args.limit]
    if not files:
        sys.exit(f"No PDF files in {args.input}")

    records, failures = [], []
    stats = {"model": extractor.model, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
             "api_seconds": 0.0, "from_saved": 0}
    started = time.time()
    for pdf in files:
        saved = cache / f"{pdf.stem}.json"
        try:
            result = json.loads(saved.read_text(encoding="utf-8")) if saved.exists() and not args.force else None
            if result is not None and not args.mock and not str(result.get("model", "")).startswith(args.model):
                result = None                          # saved with another model: extract again
            if result is not None:
                stats["from_saved"] += 1
                note = "saved result reused"
            else:
                result = extractor.extract(pdf)
                saved.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
                note = f"{result['usage']['input_tokens']:,} in / {result['usage']['output_tokens']:,} out tokens, {result['seconds']} s"
        except Exception as exc:  # one bad file must not stop the batch
            usage = getattr(exc, "usage", None)
            if usage:                                  # the call was billed even though the reply was unusable
                stats["input_tokens"] += usage["input_tokens"]
                stats["output_tokens"] += usage["output_tokens"]
                _add_cost(stats, getattr(exc, "cost", None))
            failures.append({"file": pdf.name, "error": f"{type(exc).__name__}: {exc}"})
            print(f"{pdf.name:<12} FAILED  {type(exc).__name__}: {exc}")
            continue
        records.append({"file": pdf.name, "data": result["data"]})
        stats["input_tokens"] += result["usage"]["input_tokens"]
        stats["output_tokens"] += result["usage"]["output_tokens"]
        stats["api_seconds"] = round(stats["api_seconds"] + result.get("seconds", 0), 2)
        _add_cost(stats, result.get("cost_usd"))
        if result.get("model"):
            stats["model"] = result["model"]
        print(f"{pdf.name:<12} ok      {note}")
    stats["wall_seconds"] = round(time.time() - started, 1)
    stats["files"] = len(files)
    stats["extracted"] = len(records)
    stats["failed"] = len(failures)

    issues = validate_batch(records)
    xlsx = write_workbook(out / "invoices_extracted.xlsx", records, issues, failures, stats)
    (out / "run_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")

    needs_review = len({i["file"] for i in issues})
    print(f"\n{len(records)} of {len(files)} extracted, {needs_review} need review, {len(issues)} issues")
    print(f"Excel: {xlsx}")
    if stats["cost_usd"] is not None and not args.mock:
        print(f"Cost: ${stats['cost_usd']:.4f} ({stats['input_tokens']:,} input + {stats['output_tokens']:,} output tokens)")

    result = None
    known = [r for r in records if truth and r["file"] in truth]
    if known:
        subset = {r["file"]: truth[r["file"]] for r in known}
        known_failures = [f for f in failures if f["file"] in truth]
        subset.update({f["file"]: truth[f["file"]] for f in known_failures})
        result = evaluate(known, subset, [i for i in issues if i["file"] in subset], known_failures)
        write_report(result, stats, out / "evaluation.md", out / "evaluation.json")
        print(f"Accuracy: {result['all_fields_accuracy_pct']}% of fields, "
              f"{result['invoices_fully_correct']} of {result['invoices_extracted']} invoices fully correct, "
              f"{result['planted_issues_found']} of {result['planted_issues']} planted problems caught")
        print(f"Report: {out / 'evaluation.md'}")
    return {"stats": stats, "issues": issues, "evaluation": result, "out": out}


if __name__ == "__main__":
    main()
