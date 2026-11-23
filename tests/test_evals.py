from types import SimpleNamespace

import run_evals as ev


def reply(text="", activities=None, latency=1000.0, error=None):
    return SimpleNamespace(text=text, activities=activities or [], latency_ms=latency, error=error)


CASE = {"id": "c1", "category": "safety", "question": "q?",
        "expected_keywords": ["zero energy", "lock"], "expected_sources": ["loto.docx"]}


def test_normalize_collapses_case_space_and_hyphens():
    assert ev.normalize("Lock-Out   Tag_out") == "lock out tag out"


def test_keyword_recall_counts_present_keywords_case_insensitively():
    assert ev.keyword_recall("Verify ZERO ENERGY first", ["zero energy", "lock"]) == 0.5
    assert ev.keyword_recall("anything", []) == 1.0


def test_keyword_recall_matches_across_hyphens_and_spacing():
    assert ev.keyword_recall("apply a lock-out device", ["lock out"]) == 1.0


def test_cited_files_and_citation_recall():
    payload = '{"entities": [{"name": "LOTO.docx"}]}'
    found = ev.cited_files(payload, ["loto.docx", "other.docx"])
    assert found == ["loto.docx"]
    assert ev.citation_recall(found, ["loto.docx", "other.docx"]) == 0.5
    assert ev.citation_recall([], []) == 1.0


def test_percentile_is_nearest_rank():
    values = list(range(1, 101))
    assert ev.percentile(values, 95) == 95
    assert ev.percentile([5], 95) == 5
    assert ev.percentile([], 95) == 0.0


def test_score_case_scores_a_good_reply():
    r = reply("Check zero energy then lock out.", [{"text": "see loto.docx"}], 1234)
    out = ev.score_case(CASE, r)
    assert out["keyword_recall"] == 1.0 and out["citation_recall"] == 1.0
    assert out["error"] is None and out["latency_ms"] == 1234 and out["cited"] == ["loto.docx"]


def test_score_case_treats_errors_and_empty_answers_as_failures():
    err = ev.score_case(CASE, reply(error="TimeoutError: slow"))
    assert err["error"] == "TimeoutError: slow" and err["keyword_recall"] == 0.0 and err["citation_recall"] == 0.0
    empty = ev.score_case(CASE, reply(text="   "))
    assert empty["error"] == "empty answer" and empty["keyword_recall"] == 0.0


def good(id_="a", latency=1000):
    return {"id": id_, "error": None, "latency_ms": latency, "keyword_recall": 1.0, "citation_recall": 1.0}


def test_summarize_passes_when_all_thresholds_hold():
    s = ev.summarize([good("a"), good("b")])
    assert s["passed"] is True and s["metrics"]["keyword_recall"] == 1.0 and s["failed_calls"] == []


def test_summarize_fails_on_low_recall_slow_p95_or_any_failed_call():
    low = dict(good("a"), keyword_recall=0.2)
    assert ev.summarize([low])["passed"] is False
    slow = ev.summarize([good("a", 60_000)])
    assert slow["checks"]["p95_latency_ms"] is False and slow["passed"] is False
    failed = dict(good("b"), error="boom", keyword_recall=0.0, citation_recall=0.0)
    s = ev.summarize([good("a"), failed])
    assert s["failed_calls"] == ["b"] and s["checks"]["no_failed_calls"] is False and s["passed"] is False


def test_summarize_with_no_results_does_not_pass():
    assert ev.summarize([])["passed"] is False


def test_select_cases_skips_multi_turn_and_applies_limit():
    cases = [{"id": "a"}, {"id": "m", "memory": True, "turns": [{}]}, {"id": "b"}, {"id": "c"}]
    assert [c["id"] for c in ev.select_cases(cases)] == ["a", "b", "c"]
    assert [c["id"] for c in ev.select_cases(cases, limit=2)] == ["a", "b"]
