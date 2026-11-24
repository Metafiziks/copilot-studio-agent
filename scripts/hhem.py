#!/usr/bin/env python3
"""HHEM faithfulness scoring: is the agent's answer consistent with the source documents?

HHEM-2.1-open is Vectara's open hallucination-evaluation model (Apache-2.0, a fine-tuned flan-t5-base). Given a
premise (the source text) and a hypothesis (the answer) it returns the probability that the hypothesis is
factually consistent with the premise: 1.0 consistent, 0.0 hallucinated, 0.5 is the usual cut-off.

It runs locally on CPU with no API key, which is why it is the always-on faithfulness metric here. The
reviewed-and-pinned details:

  * the model revision is pinned (MODEL_REVISION); upgrading is a deliberate edit, not a surprise
  * loading it needs trust_remote_code=True. The 3.5 KB of code that executes (modeling_hhem_v2.py and
    configuration_hhem_v2.py at the pinned revision) was read before pinning: it builds a T5 token classifier
    and exposes predict(); it does no network access beyond fetching flan-t5-base's config and tokenizer
  * the premise is the cited and expected documents, the passages a correct answer rests on

Install (CPU PyTorch is far smaller than the default wheel):
  pip install -r requirements-hhem.txt --extra-index-url https://download.pytorch.org/whl/cpu

Usage:
  python scripts/hhem.py selftest                      # prove it scores consistent / invented / off-topic correctly
  python scripts/hhem.py rescore [answers.json]        # re-score saved answers whole vs claim by claim, no agent credits
  python scripts/hhem.py score "premise text" "answer text"
"""

import argparse
import json
import re
import sys

import judge

MODEL_ID = "vectara/hallucination_evaluation_model"
MODEL_REVISION = "de9a5e6ba9ba49531a022dba17e35ffeb2a6fad8"
CONSISTENT_AT = 0.5
SELFTEST_MARGIN = 0.3           # a faithful answer must beat an invented one by at least this much
INSTALL_HINT = ("HHEM needs PyTorch and transformers: pip install -r requirements-hhem.txt "
                "--extra-index-url https://download.pytorch.org/whl/cpu")


class HHEMUnavailable(RuntimeError):
    """PyTorch/transformers are not installed or the model could not be loaded."""


_MODEL = None


def load_model():
    """Load (once) the pinned HHEM model. Imports are lazy so the rest of the suite needs no PyTorch."""
    global _MODEL
    if _MODEL is None:
        try:
            import torch  # noqa: F401
            from transformers import AutoModelForSequenceClassification
        except ImportError as exc:
            raise HHEMUnavailable(f"{exc}. {INSTALL_HINT}") from exc
        try:
            model = AutoModelForSequenceClassification.from_pretrained(
                MODEL_ID, revision=MODEL_REVISION, trust_remote_code=True)
        except Exception as exc:  # noqa: BLE001 - download, remote-code and version problems all land here
            raise HHEMUnavailable(f"could not load {MODEL_ID}@{MODEL_REVISION[:8]}: {type(exc).__name__}: {exc}") from exc
        model.eval()
        _MODEL = model
    return _MODEL


def clean_answer(text: str) -> str:
    """The answer as plain prose: markdown, tables, emoji and citation markers add noise, not claims."""
    text = re.sub(r"\[\d+\]|\[[^\]]*\]\(https?://[^)]*\)|https?://\S+", "", text)
    text = re.sub(r"^\s*\|?[\s:|-]*-{3,}[\s:|-]*\|?\s*$", "", text, flags=re.MULTILINE)      # table separator rows
    text = text.replace("|", " ")                                                          # table cell borders
    text = re.sub(r"[\u2190-\u2BFF\U0001F000-\U0001FAFF\uFE0F\u200d]", "", text)            # arrows, symbols, emoji
    text = re.sub(r"[*_`#>]+", "", text)
    text = re.sub(r"^\s*[-\u2022]\s+", "", text, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", text).strip()


META_LEAD = re.compile(r"^(according to|based on|per the|here (is|are)|below (is|are)|the following)\b", re.I)


def split_claims(text: str) -> list[str]:
    """The answer as individual claims: one per sentence or list item, with lead-ins and labels dropped.

    HHEM was trained on short summaries, so a long multi-item answer scored in one pass is unreliable (one
    correct answer scored 0.31, 0.16 and 0.07 on three runs). Scoring each claim against the source and
    aggregating is the standard remedy.
    """
    text = re.sub(r"\[\d+\]|\[[^\]]*\]\(https?://[^)]*\)|https?://\S+", "", text)
    claims = []
    for line in text.splitlines():
        line = clean_answer(line)
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", line):
            sentence = sentence.strip()
            if len(sentence.split()) < 4:                          # headings, labels, "Yes."
                continue
            if META_LEAD.match(sentence) and sentence.endswith(":"):   # "Based on the guide, here is the answer:"
                continue
            claims.append(sentence)
    return claims


def score_claims(premise: str, answer: str, model=None) -> dict:
    """Score each claim in the answer against the premise. Falls back to the whole answer if none are found."""
    claims = split_claims(answer) or [clean_answer(answer)]
    values = score([(premise, claim) for claim in claims], model)
    return {"score": round(sum(values) / len(values), 4), "min_claim": min(values),
            "supported_share": round(sum(v >= CONSISTENT_AT for v in values) / len(values), 3),
            "claims": [{"text": c[:160], "score": v} for c, v in zip(claims, values)]}


def premise_documents(corpus: dict[str, str], cited: list[str], expected: list[str]) -> list[tuple[str, str]]:
    """The documents HHEM reads: the cited and expected ones, or the whole corpus if none match."""
    wanted = {judge.stem(n) for n in list(cited) + list(expected)}
    picked = [(n, corpus[n]) for n in corpus if n in wanted]
    return picked or list(corpus.items())


def build_premise(documents: list[tuple[str, str]]) -> str:
    return "\n\n".join(text for _, text in documents)


def score(pairs: list[tuple[str, str]], model=None) -> list[float]:
    """Consistency probability (0-1) for each (premise, hypothesis) pair."""
    if not pairs:
        return []
    model = model or load_model()
    values = model.predict([(premise, clean_answer(hypothesis)) for premise, hypothesis in pairs])
    return [round(float(v), 4) for v in (values.tolist() if hasattr(values, "tolist") else values)]


def selftest(model=None) -> dict:
    """Prove HHEM discriminates on a fixed mini-document: faithful high, invented low, off-topic not 'consistent'.

    If this fails the scores mean nothing (wrong model, broken install), so the evaluation fails instead.
    """
    premise = judge.SELFTEST_DOC[1]
    kinds = list(judge.SELFTEST_ANSWERS)
    scores = dict(zip(kinds, score([(premise, judge.SELFTEST_ANSWERS[k]) for k in kinds], model)))
    checks = {
        "faithful_answer_is_consistent": scores["faithful"] >= CONSISTENT_AT,
        "invented_answer_is_hallucinated": scores["unfaithful"] < CONSISTENT_AT,
        "faithful_beats_invented_by_margin": scores["faithful"] - scores["unfaithful"] >= SELFTEST_MARGIN,
    }
    return {"passed": all(checks.values()), "checks": checks, "scores": scores,
            "model": f"{MODEL_ID}@{MODEL_REVISION[:8]}"}


def rescore(path: str, docs_dir: str = "docs") -> int:
    """Re-score saved answers (tests/fixtures/agent_answers.json) whole vs claim by claim. Costs no agent credits."""
    import statistics
    from pathlib import Path

    corpus = judge.load_corpus(Path(docs_dir))
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    model = load_model()
    out = []
    for row in rows:
        premise = build_premise(premise_documents(corpus, row["cited"], row["expected_sources"]))
        whole = score([(premise, row["answer"])], model)[0]
        claim = score_claims(premise, row["answer"], model)
        out.append({"run": row["run"], "id": row["id"], "whole": whole, "claims": len(claim["claims"]),
                    "mean": claim["score"], "min_claim": claim["min_claim"], "supported": claim["supported_share"],
                    "worst_claim": min(claim["claims"], key=lambda c: c["score"])})
        print(f"{row['run']} {row['id'][:44]:44} whole={whole:.2f} claims={len(claim['claims']):2d} mean={claim['score']:.2f} "
              f"min={claim['min_claim']:.2f} supported={claim['supported_share']:.2f}", flush=True)
    for run in sorted({r["run"] for r in out}):
        sel = [r for r in out if r["run"] == run]
        print(f"{run}: whole-answer mean {statistics.mean(r['whole'] for r in sel):.3f} | claim-level mean "
              f"{statistics.mean(r['mean'] for r in sel):.3f} | supported share {statistics.mean(r['supported'] for r in sel):.3f} "
              f"| worst answer (claim mean) {min(r['mean'] for r in sel):.3f}")
    print("\nlowest-scoring claims:")
    for r in sorted(out, key=lambda r: r["worst_claim"]["score"])[:6]:
        print(f"  {r['worst_claim']['score']:.2f}  [{r['id'][:30]}] {r['worst_claim']['text']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    again = sub.add_parser("rescore", help="re-score saved answers whole vs claim by claim (no agent credits)")
    again.add_argument("path", nargs="?", default="tests/fixtures/agent_answers.json")
    sub.add_parser("selftest", help="check HHEM scores consistent, invented and off-topic answers correctly")
    one = sub.add_parser("score", help="score one answer against a premise")
    one.add_argument("premise")
    one.add_argument("answer")
    args = parser.parse_args()
    try:
        if args.command == "rescore":
            return rescore(args.path)
        if args.command == "selftest":
            result = selftest()
            print(json.dumps(result, indent=2))
            return 0 if result["passed"] else 1
        print(json.dumps({"consistency": score([(args.premise, args.answer)])[0]}))
        return 0
    except HHEMUnavailable as exc:
        print(f"HHEM unavailable: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
