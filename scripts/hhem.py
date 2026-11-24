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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("selftest", help="check HHEM scores consistent, invented and off-topic answers correctly")
    one = sub.add_parser("score", help="score one answer against a premise")
    one.add_argument("premise")
    one.add_argument("answer")
    args = parser.parse_args()
    try:
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
