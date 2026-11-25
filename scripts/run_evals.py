#!/usr/bin/env python3
"""Run the evaluation suite against the deployed agent.

Usage: .venv-eval/bin/python scripts/run_evals.py [--auth device|interactive] [--output eval_results.json]
                                                  [--cases tests/eval_cases.json] [--limit N]
                                                  [--skip-memory] [--memory-settle SECONDS]
                                                  [--hhem auto|on|off] [--judge auto|on|off] [--docs docs]

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

Faithfulness and quality, scored against the source documents in docs/ (Copilot Studio's built-in Agent
Evaluation rejects this agent type, README > Evaluations, so the scoring lives here):

  HHEM mean       >= 0.65   scripts/hhem.py: each claim in an answer scored against its source documents, averaged.
                            A trend metric (strict about paraphrase); reports the share of supported claims. Needs
                            PyTorch, no key
  Judge faithfulness >= 0.85, relevance >= 0.85, completeness >= 0.60, min faithfulness >= 0.50
                            scripts/judge.py: an LLM judge, active only when JUDGE_API_KEY is set
  Judge stated share >= 0.95, and no contradicted claim
                            the judge's claim mode: each statement labelled stated / added / contradicted
  Self-tests                before scoring, each scorer must rate a known-good, a known-invented and a
                            known-off-topic answer correctly; if it cannot, the run fails

--hhem and --judge take auto (default: score when the scorer is available and say so when skipping), on (fail
the run if it cannot score) or off. CI runs --hhem on.

A failed call, a scorer error or a missing citation is never counted as a pass. A memory case writes a
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

# Calibrated on CI runs against the agent: before the instructions were tightened the claim-level mean was 0.68,
# 0.65 and 0.61; after, 0.84 (a wording that cost citations) and 0.77 (the final wording). HHEM is strict: it flags
# any claim the source does not state outright, benign paraphrase included, so it is a TREND metric (does the
# agent stay close to the documents?) and there is deliberately no per-answer floor: the same correct answer
# scored 0.31, 0.16 and 0.07 across runs. Telling harmless paraphrase from fabrication is the LLM judge's job.
HHEM_THRESHOLDS = {"hhem_mean": 0.65}
# The judge's 1-5 whole-answer scores saturate: all 54 saved answers scored 5/5/5. Claim mode separates the runs:
# stated share 0.918, 0.958 and 0.955 before the instructions were tightened, 0.986 and 0.988 after (gpt-4.1).
# 0.95 fails the most elaborate run; tighter would fail correct runs on noise (one added claim in a 5-claim
# answer costs a 12-answer run about 0.015). Any claim the judge finds contradicting the documents fails the run.
JUDGE_THRESHOLDS = {"judge_faithfulness": 0.85, "judge_relevance": 0.85, "judge_completeness": 0.60,
                    "judge_min_faithfulness": 0.50, "judge_stated_share": 0.95}
THRESHOLDS = {"keyword_recall": 0.65, "citation_recall": 0.60, "p95_latency_ms": 45_000, "memory_recall": 1.0,
              **HHEM_THRESHOLDS, **JUDGE_THRESHOLDS}


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
              judge_selftest: dict | None = None, hhem_selftest: dict | None = None) -> dict:
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
    hhem_errors = []
    hhemed = [r for r in results if "hhem" in r]
    if hhemed:
        hhem_errors = [r["id"] for r in hhemed if "error" in r["hhem"]]
        scored = [r["hhem"]["score"] for r in hhemed if "error" not in r["hhem"]]
        shares = [r["hhem"]["supported_share"] for r in hhemed if "error" not in r["hhem"] and "supported_share" in r["hhem"]]
        metrics["hhem_mean"] = round(sum(scored) / len(scored), 3) if scored else 0.0
        metrics["hhem_min_answer"] = min(scored, default=0.0)                # reported, not gated: too noisy
        if shares:
            metrics["hhem_supported_claims"] = round(sum(shares) / len(shares), 3)
        checks["hhem_mean"] = bool(scored) and metrics["hhem_mean"] >= thresholds["hhem_mean"]
        checks["no_hhem_errors"] = not hhem_errors
    if hhem_selftest is not None:
        checks["hhem_selftest"] = bool(hhem_selftest.get("passed"))
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
        claimed = [j["claims"] for j in scored if "claims" in j]
        judge_errors += [r["id"] for r in judged if "error" in r["judge"].get("claims", {})]
        claimed = [c for c in claimed if "error" not in c]
        if claimed:
            metrics["judge_stated_share"] = round(sum(c["stated_share"] for c in claimed) / len(claimed), 3)
            metrics["judge_added_per_answer"] = round(sum(c["counts"]["added"] for c in claimed) / len(claimed), 2)
            metrics["judge_contradicted_claims"] = sum(c["counts"]["contradicted"] for c in claimed)
            checks["judge_stated_share"] = metrics["judge_stated_share"] >= thresholds["judge_stated_share"]
            checks["no_contradicted_claims"] = metrics["judge_contradicted_claims"] == 0
        checks["no_judge_errors"] = not judge_errors
    if judge_selftest is not None:
        checks["judge_selftest"] = bool(judge_selftest.get("passed"))
    return {"cases": n, "memory_cases": len(memory_results), "metrics": metrics, "thresholds": thresholds, "checks": checks,
            "failed_calls": failures, "hhem_errors": hhem_errors, "judge_errors": judge_errors, "passed": bool(n) and all(checks.values())}


def select_cases(cases: list[dict], limit: int | None = None) -> list[dict]:
    """Single-turn RAG cases only; multi-turn memory cases are selected separately."""
    chosen = [c for c in cases if not c.get("turns") and not c.get("memory")]
    return chosen[:limit] if limit else chosen


def select_memory_cases(cases: list[dict]) -> list[dict]:
    return [c for c in cases if c.get("memory") and len(c.get("turns", [])) >= 2]


# --- judging -------------------------------------------------------------------------------------

def hhem_results(results: list[dict], cases: list[dict], corpus: dict[str, str], model=None) -> None:
    """Attach a claim-level HHEM score to every answered result, in place. A failure is recorded, not hidden."""
    import hhem

    by_id = {c["id"]: c for c in cases}
    for i, result in enumerate(results, 1):
        if result["error"]:
            continue                                  # no answer to score; the case already failed
        documents = hhem.premise_documents(corpus, result.get("cited", []), by_id[result["id"]].get("expected_sources", []))
        try:
            claims = hhem.score_claims(hhem.build_premise(documents), result["answer"], model)
            result["hhem"] = {"score": claims["score"], "supported_share": claims["supported_share"],
                              "premise_docs": [name for name, _ in documents],
                              "weakest_claims": sorted(claims["claims"], key=lambda c: c["score"])[:3]}
            print(f"[hhem {i}/{len(results)}] {result['id']}: consistency={claims['score']:.2f} "
                  f"supported={claims['supported_share']:.0%} of {len(claims['claims'])} claims", file=sys.stderr, flush=True)
        except Exception as exc:  # noqa: BLE001 - one bad answer must not hide the others
            result["hhem"] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"[hhem {i}/{len(results)}] {result['id']}: HHEM ERROR {exc}", file=sys.stderr, flush=True)


def judge_results(results: list[dict], cases: list[dict], corpus: dict[str, str], cfg: dict,
                  transport=None, sleep=None, claims: bool = True) -> None:
    """Attach a "judge" verdict to every answered result, in place. A judge failure is recorded, not hidden.

    With claims (the default) the judge also labels each statement stated / added / contradicted.
    """
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
            continue
        if not claims:
            continue
        try:
            labelled = judge.judge_claims(case["question"], result["answer"], documents, cfg, **kwargs)
            result["judge"]["claims"] = {
                "stated_share": labelled["stated_share"], "counts": labelled["counts"],
                "not_stated": [c for c in labelled["claims"] if c["label"] != "stated"][:5]}
            print(f"[judge {i}/{len(results)}] {result['id']}: stated={labelled['stated_share']:.2f} "
                  f"added={labelled['counts']['added']} contradicted={labelled['counts']['contradicted']}",
                  file=sys.stderr, flush=True)
        except judge.JudgeError as exc:
            result["judge"]["claims"] = {"error": str(exc)}
            print(f"[judge {i}/{len(results)}] {result['id']}: CLAIM JUDGE ERROR {exc}", file=sys.stderr, flush=True)


def prepare_scorers(hhem_mode: str, judge_mode: str, docs_dir: Path, env=None, hhem_mod=None, judge_mod=None) -> dict:
    """Resolve and self-test each scorer BEFORE any agent credits are spent.

    auto: use a scorer when it is available and note why when it is not; on: refuse to run without it;
    off: skip. A scorer whose self-test FAILS is never skipped: its verdict would be meaningless, so the run
    fails. The modules are injectable for tests.
    """
    import hhem as hhem_default
    import judge as judge_default

    hhem_mod, judge_mod = hhem_mod or hhem_default, judge_mod or judge_default
    out = {"corpus": {}, "hhem_model": None, "hhem_selftest": None, "hhem_note": None if hhem_mode != "off" else "off",
           "judge_cfg": None, "judge_selftest": None, "judge_note": None if judge_mode != "off" else "off"}
    if hhem_mode == "off" and judge_mode == "off":
        return out
    out["corpus"] = judge_mod.load_corpus(docs_dir)
    no_docs = f"no source documents under {docs_dir}"

    def unavailable(mode: str, why: str, note_key: str) -> None:
        if mode == "on":
            sys.exit(f"--{note_key.split('_')[0]} on, but {why}")
        out[note_key] = f"skipped: {why}"
        print(f"NOTE: {note_key.split('_')[0].upper()} scoring {out[note_key]}", file=sys.stderr, flush=True)

    if hhem_mode != "off":
        if not out["corpus"]:
            unavailable(hhem_mode, no_docs, "hhem_note")
        else:
            try:
                out["hhem_model"] = hhem_mod.load_model()
                out["hhem_selftest"] = hhem_mod.selftest(out["hhem_model"])
                print(f"hhem self-test: {'passed' if out['hhem_selftest']['passed'] else 'FAILED ' + str(out['hhem_selftest']['scores'])}",
                      file=sys.stderr, flush=True)
            except hhem_mod.HHEMUnavailable as exc:
                unavailable(hhem_mode, str(exc), "hhem_note")
    if judge_mode != "off":
        cfg = judge_mod.resolve_config(env)
        if not cfg:
            unavailable(judge_mode, "no JUDGE_API_KEY (set it to enable the LLM judge)", "judge_note")
        elif not out["corpus"]:
            unavailable(judge_mode, no_docs, "judge_note")
        else:
            try:
                whole = judge_mod.selftest(cfg)
                by_claim = judge_mod.claims_selftest(cfg)
                out["judge_selftest"] = {**whole, "passed": whole["passed"] and by_claim["passed"],
                                         "claims_selftest": {k: by_claim[k] for k in ("passed", "checks", "stated_share")}}
                out["judge_cfg"] = cfg
                print(f"judge self-test ({cfg['model']}): {'passed' if out['judge_selftest']['passed'] else 'FAILED ' + str(out['judge_selftest']['scores'])}",
                      file=sys.stderr, flush=True)
            except judge_mod.JudgeError as exc:
                if judge_mode == "on":
                    out["judge_selftest"] = {"passed": False, "error": str(exc), "model": cfg["model"]}
                else:
                    unavailable(judge_mode, f"the judge endpoint is unavailable ({exc})", "judge_note")
    return out


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
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--auth", choices=["device", "interactive"], default="device")
    parser.add_argument("--cases", type=Path, default=ROOT / "tests" / "eval_cases.json")
    parser.add_argument("--output", type=Path, default=ROOT / "eval_results.json")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N cases")
    parser.add_argument("--skip-memory", action="store_true", help="do not run the multi-turn memory cases")
    parser.add_argument("--memory-settle", type=float, default=30.0, help="seconds between memory turns")
    parser.add_argument("--hhem", choices=["auto", "on", "off"], default="auto",
                        help="HHEM faithfulness score (auto: only if PyTorch is installed)")
    parser.add_argument("--judge", choices=["auto", "on", "off"], default="auto",
                        help="LLM judge (auto: only if JUDGE_API_KEY is set)")
    parser.add_argument("--docs", type=Path, default=ROOT / "docs", help="source documents the scorers read")
    args = parser.parse_args()

    all_cases = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = select_cases(all_cases, args.limit)
    memory_cases = [] if args.skip_memory else select_memory_cases(all_cases)
    if not cases:
        sys.exit("No runnable cases found")

    scorers = prepare_scorers(args.hhem, args.judge, args.docs)
    results, memory_results = asyncio.run(run(cases, args.auth, memory_cases, args.memory_settle))
    if scorers["hhem_selftest"] and scorers["hhem_selftest"]["passed"]:
        hhem_results(results, cases, scorers["corpus"], scorers["hhem_model"])
    if scorers["judge_cfg"] and scorers["judge_selftest"] and scorers["judge_selftest"]["passed"]:
        judge_results(results, cases, scorers["corpus"], scorers["judge_cfg"])
    summary = summarize(results, memory_results=memory_results, judge_selftest=scorers["judge_selftest"],
                        hhem_selftest=scorers["hhem_selftest"])
    summary["scorers"] = {"hhem": scorers["hhem_selftest"] or scorers["hhem_note"],
                          "judge": scorers["judge_selftest"] or scorers["judge_note"]}
    args.output.write_text(json.dumps({"summary": summary, "results": results, "memory_results": memory_results},
                                      indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"\nwritten -> {args.output}", file=sys.stderr)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
