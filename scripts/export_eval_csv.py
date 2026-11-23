#!/usr/bin/env python3
"""Export tests/eval_cases.json as a CSV for Copilot Studio's built-in Agent Evaluation.

Usage: export_eval_csv.py [--cases tests/eval_cases.json] [--out .build/eval_cases.csv]

Import it under Evaluation > New evaluation > Import. Per Microsoft's documentation the file has
exactly two headers, `Question` and `Expected response`, at most 100 rows, and each question is at
most 1,000 characters. The test method (General quality, Keyword match, ...) is chosen in the UI
after import; the file has no method column.

Multi-turn cases (the memory-recall case) are skipped: this format is single-response only. The
documentation gives no separator for multiple keywords, so the expected keywords are written as a
comma-separated list; verify how the Keyword match method reads that after your first import.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

HEADERS = ["Question", "Expected response"]
MAX_ROWS = 100
MAX_QUESTION_CHARS = 1000


def to_rows(cases: list[dict]) -> list[list[str]]:
    rows = []
    for case in cases:
        if case.get("turns"):
            continue  # multi-turn; not representable in the single-response import
        question = case["question"].strip()
        if len(question) > MAX_QUESTION_CHARS:
            raise ValueError(f"case {case['id']}: question is {len(question)} chars (limit {MAX_QUESTION_CHARS})")
        rows.append([question, ", ".join(case.get("expected_keywords", []))])
    if len(rows) > MAX_ROWS:
        raise ValueError(f"{len(rows)} rows exceeds the {MAX_ROWS}-row import limit")
    return rows


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, default=root / "tests" / "eval_cases.json")
    parser.add_argument("--out", type=Path, default=root / ".build" / "eval_cases.csv")
    args = parser.parse_args()
    try:
        rows = to_rows(json.loads(args.cases.read_text(encoding="utf-8")))
    except ValueError as exc:
        sys.exit(str(exc))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADERS)
        writer.writerows(rows)
    print(f"wrote {len(rows)} case(s) to {args.out}")


if __name__ == "__main__":
    main()
