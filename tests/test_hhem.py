"""HHEM scoring and the scorer set-up in run_evals, with fake models (PyTorch is not needed to test the logic)."""

import sys
from types import SimpleNamespace

import pytest

import hhem
import judge
import run_evals as ev


class FakeModel:
    """Scores by a rule the tests control; records what it was asked."""

    def __init__(self, rule=lambda premise, hypothesis: 0.9):
        self.rule, self.seen = rule, []

    def predict(self, pairs):
        self.seen.extend(pairs)
        return SimpleNamespace(tolist=lambda: [self.rule(p, h) for p, h in pairs])


def discriminating(premise, hypothesis):
    return 0.05 if "Bay 7" in hypothesis else (0.4 if "cafeteria" in hypothesis else 0.95)


# --- pure helpers ------------------------------------------------------------------------------------

def test_answers_are_scored_as_prose_not_markdown_or_citations():
    raw = "**Inspect** the belts [1] daily.\n- Check guards\n- See https://example.test/x and [doc](https://example.test/d)"
    assert hhem.clean_answer(raw) == "Inspect the belts daily. Check guards See and"


def test_premise_is_the_cited_and_expected_documents_else_the_whole_corpus():
    corpus = {"a": "AAA", "b": "BBB", "c": "CCC"}
    assert hhem.premise_documents(corpus, ["B.docx"], ["c.docx"]) == [("b", "BBB"), ("c", "CCC")]
    assert hhem.premise_documents(corpus, [], ["missing.docx"]) == list(corpus.items())
    assert hhem.build_premise([("a", "AAA"), ("b", "BBB")]) == "AAA\n\nBBB"


def test_scores_are_rounded_probabilities_and_the_answer_is_cleaned_first():
    model = FakeModel(lambda p, h: 0.123456)
    assert hhem.score([("premise", "**answer** [1]")], model) == [0.1235]
    assert model.seen == [("premise", "answer")]
    assert hhem.score([], model) == []


def test_tables_emoji_and_blockquotes_are_flattened_to_prose():
    raw = "Steps:\n\n| Requirement | What it says |\n|---|---|\n| **Where** | The quarantine area |\n\n> \u26a0\ufe0f **Important:** report it."
    assert hhem.clean_answer(raw) == "Steps: Requirement What it says Where The quarantine area Important: report it."


def test_claims_are_split_per_sentence_and_list_item_with_lead_ins_and_labels_dropped():
    raw = ("Based on the **Guide** [1], here is the answer:\n\n### Steps\n\n"
           "1. **Stop the machine** — Do not operate it. Notify maintenance.\n"
           "2. **Use absorbent pads** — Contain the spill.\n\nYes.")
    assert hhem.split_claims(raw) == ["Stop the machine — Do not operate it.", "Notify maintenance.",
                                      "Use absorbent pads — Contain the spill."]


def test_table_rows_become_claims_and_separator_rows_do_not():
    raw = "| Where | What |\n|---|---|\n| Place | Rejected materials go to the designated quarantine area |"
    assert hhem.split_claims(raw) == ["Place Rejected materials go to the designated quarantine area"]


def test_claim_scores_are_averaged_and_the_weak_ones_reported():
    model = FakeModel(lambda p, h: 0.1 if "tears" in h else 0.9)
    out = hhem.score_claims("premise", "Inspect the belt daily. Look for tears and fraying. Check the guards too.", model)
    assert out["score"] == 0.6333 and out["min_claim"] == 0.1 and out["supported_share"] == 0.667
    assert [c["score"] for c in out["claims"]] == [0.9, 0.1, 0.9]


def test_an_answer_with_no_extractable_claim_is_scored_whole():
    out = hhem.score_claims("premise", "Yes.", FakeModel(lambda p, h: 0.7))
    assert out["score"] == 0.7 and len(out["claims"]) == 1


def test_the_model_is_not_loaded_for_an_empty_batch():
    assert hhem.score([]) == []


def test_missing_pytorch_is_reported_with_the_install_command(monkeypatch):
    monkeypatch.setattr(hhem, "_MODEL", None)
    monkeypatch.setitem(sys.modules, "torch", None)            # makes `import torch` raise ImportError
    with pytest.raises(hhem.HHEMUnavailable, match="requirements-hhem.txt"):
        hhem.load_model()


def test_the_model_revision_is_pinned_to_a_commit():
    assert len(hhem.MODEL_REVISION) == 40 and set(hhem.MODEL_REVISION) <= set("0123456789abcdef")


# --- self-test ---------------------------------------------------------------------------------------

def test_selftest_passes_for_a_model_that_discriminates():
    result = hhem.selftest(FakeModel(discriminating))
    assert result["passed"] and result["scores"]["faithful"] > result["scores"]["unfaithful"]


def test_selftest_fails_for_a_model_that_calls_everything_consistent():
    result = hhem.selftest(FakeModel(lambda p, h: 0.9))
    assert not result["passed"] and not result["checks"]["invented_answer_is_hallucinated"]


def test_selftest_fails_for_a_model_that_calls_everything_hallucinated():
    assert not hhem.selftest(FakeModel(lambda p, h: 0.1))["checks"]["faithful_answer_is_consistent"]


def test_selftest_requires_a_real_margin_not_just_the_threshold():
    rule = lambda p, h: 0.45 if "Bay 7" in h else 0.55  # noqa: E731 - right side of 0.5 but too close
    assert not hhem.selftest(FakeModel(rule))["checks"]["faithful_beats_invented_by_margin"]


# --- scoring a run -----------------------------------------------------------------------------------

def result(id_, answer="Apply a lock.", error=None, cited=("loto.docx",)):
    return {"id": id_, "error": error, "answer": answer, "cited": list(cited)}


CASES = [{"id": "a", "expected_sources": ["loto.docx"]}, {"id": "b", "expected_sources": ["loto.docx"]}]
CORPUS = {"loto": "Apply a lock and a tag.", "other": "Unrelated."}


def test_hhem_results_attach_a_score_and_the_premise_documents_to_answered_cases_only():
    results = [result("a"), result("b", answer="", error="empty answer")]
    results[0]["answer"] = "Apply a lock and a tag. Verify zero energy first."
    ev.hhem_results(results, CASES, CORPUS, FakeModel(lambda p, h: 0.8))
    assert results[0]["hhem"]["score"] == 0.8 and results[0]["hhem"]["supported_share"] == 1.0
    assert results[0]["hhem"]["premise_docs"] == ["loto"] and len(results[0]["hhem"]["weakest_claims"]) == 2
    assert "hhem" not in results[1]


def test_an_hhem_failure_is_recorded_on_the_case():
    class Broken:
        def predict(self, pairs):
            raise RuntimeError("out of memory")
    results = [result("a")]
    ev.hhem_results(results, CASES, CORPUS, Broken())
    assert "out of memory" in results[0]["hhem"]["error"]


def scored(id_, value, share=None):
    r = result(id_)
    r.update(latency_ms=1, keyword_recall=1.0, citation_recall=1.0,
             hhem={"score": value, "supported_share": value if share is None else share})
    return r


def test_summary_gates_on_the_hhem_mean_and_reports_the_rest():
    good = ev.summarize([scored("a", 0.9), scored("b", 0.7)], hhem_selftest={"passed": True})
    assert good["metrics"]["hhem_mean"] == 0.8 and good["metrics"]["hhem_min_answer"] == 0.7 and good["passed"]
    assert good["metrics"]["hhem_supported_claims"] == 0.8
    below = ev.HHEM_THRESHOLDS["hhem_mean"] - 0.05
    low = ev.summarize([scored("a", below), scored("b", below)], hhem_selftest={"passed": True})
    assert not low["checks"]["hhem_mean"] and not low["passed"]


def test_one_poor_answer_does_not_fail_the_run_because_single_answers_are_too_noisy_to_gate_on():
    results = [scored(str(i), 0.9) for i in range(9)] + [scored("x", 0.02)]
    summary = ev.summarize(results, hhem_selftest={"passed": True})
    assert summary["metrics"]["hhem_min_answer"] == 0.02 and summary["passed"]
    assert "hhem_min" not in summary["checks"]


def test_an_hhem_error_or_failed_selftest_fails_the_run():
    erred = scored("b", 0.9)
    erred["hhem"] = {"error": "boom"}
    assert not ev.summarize([scored("a", 0.9), erred], hhem_selftest={"passed": True})["passed"]
    assert not ev.summarize([scored("a", 0.9)], hhem_selftest={"passed": False})["passed"]


def test_summary_without_hhem_has_no_hhem_metrics():
    plain = scored("a", 0.9)
    del plain["hhem"]
    assert not any(k.startswith("hhem") for k in ev.summarize([plain])["metrics"])


# --- scorer set-up -----------------------------------------------------------------------------------

def fake_hhem(passed=True, unavailable=False):
    def load_model():
        if unavailable:
            raise hhem.HHEMUnavailable("no torch")
        return "model"
    return SimpleNamespace(load_model=load_model, HHEMUnavailable=hhem.HHEMUnavailable,
                           selftest=lambda m: {"passed": passed, "scores": {}})


def fake_judge(cfg=None, selftest=None, error=None, claims_selftest=None):
    def run_selftest(c):
        if error:
            raise judge.JudgeError(error)
        return selftest or {"passed": True, "scores": {}}
    return SimpleNamespace(load_corpus=lambda d: {"loto": "text"}, resolve_config=lambda env=None: cfg,
                           selftest=run_selftest, JudgeError=judge.JudgeError,
                           claims_selftest=lambda c: claims_selftest or {"passed": True, "checks": {}, "stated_share": {}})


def prepare(hhem_mode, judge_mode, hhem_mod=None, judge_mod=None):
    return ev.prepare_scorers(hhem_mode, judge_mode, "docs", hhem_mod=hhem_mod or fake_hhem(), judge_mod=judge_mod or fake_judge())


def test_everything_off_touches_nothing():
    out = prepare("off", "off", hhem_mod=SimpleNamespace(), judge_mod=SimpleNamespace())
    assert out["hhem_selftest"] is None and out["judge_selftest"] is None


def test_auto_uses_hhem_when_it_loads_and_skips_the_judge_without_a_key_saying_why():
    out = prepare("auto", "auto")
    assert out["hhem_selftest"]["passed"] and out["hhem_model"] == "model"
    assert out["judge_cfg"] is None and "JUDGE_API_KEY" in out["judge_note"]


def test_auto_skips_hhem_when_pytorch_is_missing_and_says_so():
    out = prepare("auto", "off", hhem_mod=fake_hhem(unavailable=True))
    assert out["hhem_selftest"] is None and "no torch" in out["hhem_note"]


def test_on_refuses_to_run_without_the_scorer():
    with pytest.raises(SystemExit, match="--hhem on"):
        prepare("on", "off", hhem_mod=fake_hhem(unavailable=True))
    with pytest.raises(SystemExit, match="--judge on"):
        prepare("off", "on")


def test_a_failing_selftest_is_never_skipped_even_in_auto():
    out = prepare("auto", "off", hhem_mod=fake_hhem(passed=False))
    assert out["hhem_selftest"]["passed"] is False


def test_the_judge_runs_when_configured_and_its_selftest_is_kept():
    out = prepare("off", "auto", judge_mod=fake_judge(cfg={"model": "m"}))
    assert out["judge_cfg"] == {"model": "m"} and out["judge_selftest"]["passed"]


def test_an_unreachable_judge_is_skipped_in_auto_but_fails_in_on():
    out = prepare("off", "auto", judge_mod=fake_judge(cfg={"model": "m"}, error="HTTP 500"))
    assert out["judge_cfg"] is None and "HTTP 500" in out["judge_note"]
    out = prepare("off", "on", judge_mod=fake_judge(cfg={"model": "m"}, error="HTTP 500"))
    assert out["judge_selftest"]["passed"] is False and "HTTP 500" in out["judge_selftest"]["error"]


def test_the_run_fails_if_the_judge_cannot_tell_added_detail_from_stated_fact():
    bad = {"passed": False, "checks": {"added_detail_is_caught": False}, "stated_share": {}}
    out = prepare("off", "auto", judge_mod=fake_judge(cfg={"model": "m"}, claims_selftest=bad))
    assert out["judge_selftest"]["passed"] is False
    assert out["judge_selftest"]["claims_selftest"]["checks"] == {"added_detail_is_caught": False}
