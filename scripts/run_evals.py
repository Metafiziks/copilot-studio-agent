#!/usr/bin/env python3
"""Run the evaluation suite against the deployed agent.

Usage: .venv-eval/bin/python scripts/run_evals.py [--auth device|interactive] [--output eval_results.json]
                                                  [--cases tests/eval_cases.json] [--limit N]

Deterministic metrics, computed here from real agent replies:

  Keyword recall  >= 0.65   share of expected keywords present in the answer
  Citation recall >= 0.60   share of expected source files the reply cites
  p95 latency     <= 45 s   wall-clock time per question
  Failed calls              any failed call, or an answer with no text, fails the run

The LLM-judged metrics (relevance, groundedness) come from Copilot Studio's built-in Agent Evaluation;
see README. Multi-turn memory cases are skipped until memory is built. A failed call or a missing
citation is never counted as a pass.
"""

import argparse
import asyncio
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

THRESHOLDS = {"keyword_recall": 0.65, "citation_recall": 0.60, "p95_latency_ms": 45_000}


# --- pure metric functions (unit-tested) -------------------------------------------------------

def normalize(text: str) -> str:
    """Lower-case and collapse whitespace and hyphens so 'Lock-out  tag' matches 'lock out tag'."""
    return re.sub(r"[\s\-_]+", " ", text.lower()).strip()


def keyword_recall(answer: str, keywords: list[str]) -> float:
    if not keywords:
        return 1.0
    haystack = normalize(answer)
    return sum(1 for k in keywords if normalize(k) in haystack) / len(keywords)


def cited_files(payload: str, expected: list[str]) -> list[str]:
    """Which of the expected source filenames appear in the reply payload (case-insensitive)."""
    lowered = payload.lower()
    return [name for name in expected if name.lower() in lowered]


def citation_recall(found: list[str], expected: list[str]) -> float:
    if not expected:
        return 1.0
    return len({f.lower() for f in found} & {e.lower() for e in expected}) / len(expected)


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (no interpolation)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def score_case(case: dict, reply) -> dict:
    """Score one reply. A reply with an error or no text is a failure, never a pass."""
    expected = case.get("expected_sources", [])
    payload = json.dumps(reply.activities, ensure_ascii=False)
    found = cited_files(payload, expected)
    failed = bool(reply.error) or not reply.text.strip()
    return {
        "id": case["id"],
        "category": case.get("category"),
        "question": case["question"],
        "error": reply.error or ("empty answer" if not reply.text.strip() else None),
        "latency_ms": round(reply.latency_ms),
        "keyword_recall": 0.0 if failed else round(keyword_recall(reply.text, case.get("expected_keywords", [])), 3),
        "citation_recall": 0.0 if failed else round(citation_recall(found, expected), 3),
        "cited": found,
        "answer": reply.text,
    }


def summarize(results: list[dict], thresholds: dict = THRESHOLDS) -> dict:
    n = len(results)
    failures = [r["id"] for r in results if r["error"]]
    mean = lambda key: round(sum(r[key] for r in results) / n, 3) if n else 0.0  # noqa: E731
    p95 = percentile([r["latency_ms"] for r in results], 95)
    metrics = {"keyword_recall": mean("keyword_recall"), "citation_recall": mean("citation_recall"),
               "p95_latency_ms": p95}
    checks = {
        "keyword_recall": metrics["keyword_recall"] >= thresholds["keyword_recall"],
        "citation_recall": metrics["citation_recall"] >= thresholds["citation_recall"],
        "p95_latency_ms": p95 <= thresholds["p95_latency_ms"],
        "no_failed_calls": not failures,
    }
    return {"cases": n, "metrics": metrics, "thresholds": thresholds, "checks": checks,
            "failed_calls": failures, "passed": bool(n) and all(checks.values())}


def select_cases(cases: list[dict], limit: int | None = None) -> list[dict]:
    """Single-turn RAG cases only; multi-turn memory cases are skipped until memory is built."""
    chosen = [c for c in cases if not c.get("turns") and not c.get("memory")]
    return chosen[:limit] if limit else chosen


# --- runner --------------------------------------------------------------------------------------

async def run(cases: list[dict], auth: str) -> list[dict]:
    import agent_client

    agent_client.load_dotenv()
    client = agent_client.make_client(auth)
    results = []
    for i, case in enumerate(cases, 1):
        reply = await agent_client.ask(client, case["question"])
        result = score_case(case, reply)
        results.append(result)
        status = "ERROR " + result["error"] if result["error"] else \
            f"kw={result['keyword_recall']:.2f} cite={result['citation_recall']:.2f} {result['latency_ms']} ms"
        print(f"[{i}/{len(cases)}] {case['id']}: {status}", file=sys.stderr, flush=True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--auth", choices=["device", "interactive"], default="device")
    parser.add_argument("--cases", type=Path, default=ROOT / "tests" / "eval_cases.json")
    parser.add_argument("--output", type=Path, default=ROOT / "eval_results.json")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N cases")
    args = parser.parse_args()

    cases = select_cases(json.loads(args.cases.read_text(encoding="utf-8")), args.limit)
    if not cases:
        sys.exit("No runnable cases found")
    results = asyncio.run(run(cases, args.auth))
    summary = summarize(results)
    args.output.write_text(json.dumps({"summary": summary, "results": results}, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"\nwritten -> {args.output}", file=sys.stderr)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
