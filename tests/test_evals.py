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


# --- memory cases ---

MEMORY_CASE = {"id": "mem", "category": "memory", "memory": True,
               "turns": [{"question": "remember: second shift, Line 2"}, {"question": "what shift do I prefer?"}],
               "expected_keywords": ["second shift", "Line 2"], "expected_sources": []}


def test_select_memory_cases_needs_a_memory_flag_and_two_turns():
    cases = [{"id": "a"}, MEMORY_CASE, {"id": "m1", "memory": True, "turns": [{"question": "x"}]}]
    assert [c["id"] for c in ev.select_memory_cases(cases)] == ["mem"]


def test_memory_case_scores_the_last_reply_only():
    out = ev.score_memory_case(MEMORY_CASE, [reply("Got it, saved."), reply("Your shift is second shift on Line 2.")])
    assert out["memory_recall"] == 1.0 and out["error"] is None
    assert out["question"] == "what shift do I prefer?"


def test_memory_case_with_a_forgotten_preference_scores_zero_not_an_error():
    out = ev.score_memory_case(MEMORY_CASE, [reply("Saved."), reply("I don't know your preferences.")])
    assert out["memory_recall"] == 0.0 and out["error"] is None


def test_memory_case_fails_when_either_turn_errors():
    out = ev.score_memory_case(MEMORY_CASE, [reply(error="TimeoutError: slow"), reply("second shift, Line 2")])
    assert out["error"] == "TimeoutError: slow" and out["memory_recall"] == 0.0
    out = ev.score_memory_case(MEMORY_CASE, [reply("Saved."), reply("  ")])
    assert out["error"] == "empty answer"


def test_summarize_requires_full_memory_recall_when_memory_cases_ran():
    ok = {"id": "mem", "error": None, "latency_ms": 1, "memory_recall": 1.0}
    assert ev.summarize([good("a")], memory_results=[ok])["passed"] is True
    half = dict(ok, memory_recall=0.5)
    s = ev.summarize([good("a")], memory_results=[half])
    assert s["checks"]["memory_recall"] is False and s["passed"] is False
    bad = dict(ok, error="boom", memory_recall=0.0)
    assert ev.summarize([good("a")], memory_results=[bad])["failed_calls"] == ["mem"]


def test_summarize_without_memory_cases_has_no_memory_check():
    s = ev.summarize([good("a")])
    assert "memory_recall" not in s["checks"] and s["memory_cases"] == 0
