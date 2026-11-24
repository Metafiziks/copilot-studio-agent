#!/usr/bin/env python3
"""Run the evaluation suite against the deployed agent.

Usage: .venv-eval/bin/python scripts/run_evals.py [--auth device|interactive] [--output eval_results.json]
                                                  [--cases tests/eval_cases.json] [--limit N]
                                                  [--skip-memory] [--memory-settle SECONDS]
                                                  [--judge auto|on|off] [--docs docs]

Run it as a user who holds a SharePoint-bearing license (set AGENT_TOKEN_CACHE to keep one sign-in per
user): the agent searches SharePoint as the signed-in user, and for an unlicensed user every question
comes back "not found".

Deterministic metrics, computed here from real agent replies:

  Keyword recall  >= 0.65   share of expected keywords present in the answer
  Citation recall >= 0.60   share of expected source files the reply cites
  p95 latency     <= 45 s   wall-clock time per question
  Memory recall   == 1.0    multi-turn cases: the agent is told a preference in one conversation and asked
                            about it in a NEW conversation, so only memory can supply the answer
  Failed calls              any failed call, or an answer with no text, fails the run

LLM-judged metrics (scripts/judge.py), each answer scored against the source documents in docs/:

  Faithfulness    >= 0.85   mean: every claim supported by the documents (the hallucination check)
  Min faithfulness >= 0.50  no single answer may be badly unfaithful, whatever the mean
  Relevance       >= 0.85   mean: the answer addresses the question
  Completeness    >= 0.60   mean: the answer includes what the documents say that the question asks for
  Judge self-test           before judging, the judge must score a known-good, a known-invented and a
                            known-off-topic answer correctly; if it cannot, the run fails

Copilot Studio's built-in Agent Evaluation rejects this agent type (README > Evaluations), which is why the
judging lives here. --judge auto (default) judges when a credential is available (`gh auth login` locally, the
GITHUB_TOKEN in Actions) and says so when it skips; --judge on fails the run if it cannot judge.

A failed call, a judge error or a missing citation is never counted as a pass. A memory case writes a
preference into the signed-in user's memory, and that persists.
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

JUDGE_THRESHOLDS = {"judge_faithfulness": 0.85, "judge_relevance": 0.85, "judge_completeness": 0.60,
                    "judge_min_faithfulness": 0.50}
THRESHOLDS = {"keyword_recall": 0.65, "citation_recall": 0.60, "p95_latency_ms": 45_000, "memory_recall": 1.0,
              **JUDGE_THRESHOLDS}


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


def score_memory_case(case: dict, replies: list) -> dict:
    """Score a multi-turn memory case: the LAST reply must contain the expected keywords.

    Any failed turn fails the case; an earlier turn that errored means nothing was ever stored.
    """
    last = replies[-1]
    errors = [r.error or "empty answer" for r in replies if r.error or not r.text.strip()]
    failed = bool(errors)
    return {
        "id": case["id"],
        "category": case.get("category"),
        "question": case["turns"][-1]["question"],
        "error": errors[0] if errors else None,
        "latency_ms": round(last.latency_ms),
        "memory_recall": 0.0 if failed else round(keyword_recall(last.text, case.get("expected_keywords", [])), 3),
        "answer": last.text,
    }


def summarize(results: list[dict], thresholds: dict = THRESHOLDS, memory_results: list[dict] | None = None,
              judge_selftest: dict | None = None) -> dict:
    memory_results = memory_results or []
    n = len(results)
    failures = [r["id"] for r in results + memory_results if r["error"]]
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
    if memory_results:
        metrics["memory_recall"] = round(sum(r["memory_recall"] for r in memory_results) / len(memory_results), 3)
        checks["memory_recall"] = metrics["memory_recall"] >= thresholds["memory_recall"]
    judge_errors = []
    judged = [r for r in results if "judge" in r]
    if judged:
        judge_errors = [r["id"] for r in judged if "error" in r["judge"]]
        scored = [r["judge"] for r in judged if "error" not in r["judge"]]
        for dim in ("faithfulness", "relevance", "completeness"):
            metrics[f"judge_{dim}"] = round(sum(j[dim] for j in scored) / len(scored), 3) if scored else 0.0
            checks[f"judge_{dim}"] = bool(scored) and metrics[f"judge_{dim}"] >= thresholds[f"judge_{dim}"]
        metrics["judge_min_faithfulness"] = min((j["faithfulness"] for j in scored), default=0.0)
        checks["judge_min_faithfulness"] = bool(scored) and metrics["judge_min_faithfulness"] >= thresholds["judge_min_faithfulness"]
        checks["no_judge_errors"] = not judge_errors
    if judge_selftest is not None:
        checks["judge_selftest"] = bool(judge_selftest.get("passed"))
    return {"cases": n, "memory_cases": len(memory_results), "metrics": metrics, "thresholds": thresholds, "checks": checks,
            "failed_calls": failures, "judge_errors": judge_errors, "passed": bool(n) and all(checks.values())}


def select_cases(cases: list[dict], limit: int | None = None) -> list[dict]:
    """Single-turn RAG cases only; multi-turn memory cases are selected separately."""
    chosen = [c for c in cases if not c.get("turns") and not c.get("memory")]
    return chosen[:limit] if limit else chosen


def select_memory_cases(cases: list[dict]) -> list[dict]:
    return [c for c in cases if c.get("memory") and len(c.get("turns", [])) >= 2]


# --- judging -------------------------------------------------------------------------------------

def judge_results(results: list[dict], cases: list[dict], corpus: dict[str, str], cfg: dict,
                  transport=None, sleep=None) -> None:
    """Attach a "judge" verdict to every answered result, in place. A judge failure is recorded, not hidden."""
    import judge

    kwargs = {k: v for k, v in (("transport", transport), ("sleep", sleep)) if v is not None}
    by_id = {c["id"]: c for c in cases}
    for i, result in enumerate(results, 1):
        if result["error"]:
            continue                                  # no answer to judge; the case already failed
        case = by_id[result["id"]]
        documents = judge.select_context(corpus, result.get("cited", []), case.get("expected_sources", []))
        try:
            verdict = judge.judge_answer(case["question"], result["answer"], documents, cfg, **kwargs)
            result["judge"] = verdict
            print(f"[judge {i}/{len(results)}] {result['id']}: faithful={verdict['faithfulness']:.2f} "
                  f"relevant={verdict['relevance']:.2f} complete={verdict['completeness']:.2f}", file=sys.stderr, flush=True)
        except judge.JudgeError as exc:
            result["judge"] = {"error": str(exc)}
            print(f"[judge {i}/{len(results)}] {result['id']}: JUDGE ERROR {exc}", file=sys.stderr, flush=True)


# --- runner --------------------------------------------------------------------------------------

async def run_memory(client, cases: list[dict], settle: float) -> list[dict]:
    """Each turn is a NEW conversation, so a correct last answer can only come from stored memory."""
    import agent_client

    results = []
    for case in cases:
        replies = []
        for i, turn in enumerate(case["turns"]):
            if i:
                await asyncio.sleep(settle)       # memory is written asynchronously after the turn
            replies.append(await agent_client.ask(client, turn["question"]))
        result = score_memory_case(case, replies)
        results.append(result)
        status = "ERROR " + result["error"] if result["error"] else f"memory={result['memory_recall']:.2f}"
        print(f"[memory] {case['id']}: {status}", file=sys.stderr, flush=True)
    return results


async def run(cases: list[dict], auth: str, memory_cases: list[dict] | None = None,
              settle: float = 30.0) -> tuple[list[dict], list[dict]]:
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
    memory_results = await run_memory(client, memory_cases, settle) if memory_cases else []
    return results, memory_results


def main() -> int:
    import judge

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--auth", choices=["device", "interactive"], default="device")
    parser.add_argument("--cases", type=Path, default=ROOT / "tests" / "eval_cases.json")
    parser.add_argument("--output", type=Path, default=ROOT / "eval_results.json")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N cases")
    parser.add_argument("--skip-memory", action="store_true", help="do not run the multi-turn memory cases")
    parser.add_argument("--memory-settle", type=float, default=30.0, help="seconds between memory turns")
    parser.add_argument("--judge", choices=["auto", "on", "off"], default="auto",
                        help="LLM-judge the answers (auto: only if a judge credential is available)")
    parser.add_argument("--docs", type=Path, default=ROOT / "docs", help="source documents the judge reads")
    args = parser.parse_args()

    all_cases = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = select_cases(all_cases, args.limit)
    memory_cases = [] if args.skip_memory else select_memory_cases(all_cases)
    if not cases:
        sys.exit("No runnable cases found")

    # Settle the judge BEFORE spending agent credits: a missing credential or a judge that cannot tell good
    # answers from bad ones should be known up front.
    cfg, corpus, selftest, judge_note = None, {}, None, None
    if args.judge != "off":
        cfg = judge.resolve_config()
        corpus = judge.load_corpus(args.docs) if cfg else {}
        if not cfg or not corpus:
            reason = ("no judge credential (run `gh auth login`, or set JUDGE_API_KEY; in Actions give the job "
                      "`permissions: models: read`)") if not cfg else f"no source documents under {args.docs}"
            if args.judge == "on":
                sys.exit(f"--judge on, but {reason}")
            judge_note = f"skipped: {reason}"
            print(f"NOTE: LLM judging {judge_note}", file=sys.stderr, flush=True)
            cfg = None
        else:
            try:
                selftest = judge.selftest(cfg)
            except judge.JudgeError as exc:
                selftest = {"passed": False, "error": str(exc), "model": cfg["model"]}
            print(f"judge self-test ({cfg['model']}): {'passed' if selftest['passed'] else 'FAILED'}"
                  + (f" {selftest.get('error') or selftest['checks']}" if not selftest["passed"] else ""),
                  file=sys.stderr, flush=True)

    results, memory_results = asyncio.run(run(cases, args.auth, memory_cases, args.memory_settle))
    if cfg and selftest and selftest["passed"]:
        judge_results(results, cases, corpus, cfg)
    summary = summarize(results, memory_results=memory_results, judge_selftest=selftest)
    summary["judge"] = {"model": cfg["model"], "selftest": selftest} if cfg else (judge_note or "off")
    args.output.write_text(json.dumps({"summary": summary, "results": results, "memory_results": memory_results},
                                      indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"\nwritten -> {args.output}", file=sys.stderr)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
