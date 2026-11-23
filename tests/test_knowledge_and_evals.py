import csv
import json
from pathlib import Path

import pytest

import export_eval_csv
import render_knowledge

ROOT = Path(__file__).resolve().parent.parent
CASES = json.loads((ROOT / "tests" / "eval_cases.json").read_text())


# --- render_knowledge ---

def test_render_source_contains_kind_and_site():
    text = render_knowledge.render_source("OL Library", "Main docs", "https://h.sharepoint.com/sites/x/Shared%20Documents")
    assert "kind: KnowledgeSourceConfiguration" in text
    assert "kind: SharePointSearchSource" in text
    assert "site: https://h.sharepoint.com/sites/x/Shared%20Documents" in text
    assert "componentName: OL Library" in text


def test_render_source_rejects_non_https():
    with pytest.raises(ValueError):
        render_knowledge.render_source("n", "d", "http://h/sites/x")


def test_yaml_scalar_quotes_unsafe_values():
    assert render_knowledge.yaml_scalar("Plain name") == "Plain name"
    assert render_knowledge.yaml_scalar("Name: with colon") == "'Name: with colon'"
    assert render_knowledge.yaml_scalar("It's #1") == "'It''s #1'"


def test_library_url_encodes_the_space():
    assert render_knowledge.library_url("t.sharepoint.com", "ol-knowledge") == \
        "https://t.sharepoint.com/sites/ol-knowledge/Shared%20Documents"


def test_write_sources_replaces_stale_files_and_numbers_from_zero(tmp_path):
    (tmp_path / "SharePointSearchSource.7.mcs.yml").write_text("stale")
    (tmp_path / "other.mcs.yml").write_text("keep")
    sources = [("A", "first", "https://h/sites/a/Shared%20Documents"),
               ("B", "second", "https://h/sites/b/Shared%20Documents")]
    written = render_knowledge.write_sources(tmp_path, sources)
    assert [p.name for p in written] == ["SharePointSearchSource.0.mcs.yml", "SharePointSearchSource.1.mcs.yml"]
    assert not (tmp_path / "SharePointSearchSource.7.mcs.yml").exists()
    assert (tmp_path / "other.mcs.yml").exists()


def test_parse_source_requires_three_parts():
    assert render_knowledge.parse_source("a|b|https://x") == ("a", "b", "https://x")
    with pytest.raises(ValueError):
        render_knowledge.parse_source("a|b")


def test_committed_knowledge_files_match_the_renderer_layout():
    for path in sorted((ROOT / "agent" / "knowledge").glob("SharePointSearchSource.*.mcs.yml")):
        text = path.read_text()
        assert "kind: SharePointSearchSource" in text and "site: https://" in text


# --- eval cases ---

def test_eval_case_ids_are_unique():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))


def test_rag_cases_have_the_expected_shape():
    rag = [c for c in CASES if not c.get("memory")]
    assert len(rag) == 12
    for c in rag:
        assert c["question"].strip()
        assert 4 <= len(c["expected_keywords"]) <= 6
        assert len({k.lower() for k in c["expected_keywords"]}) == len(c["expected_keywords"])
        assert len(c["expected_sources"]) == 1 and c["expected_sources"][0].endswith(".docx")


def test_memory_case_is_two_turns():
    memory = [c for c in CASES if c.get("memory")]
    assert len(memory) == 1 and len(memory[0]["turns"]) == 2


def test_export_skips_multi_turn_and_writes_documented_headers(tmp_path, monkeypatch):
    out = tmp_path / "evals.csv"
    monkeypatch.setattr("sys.argv", ["export_eval_csv.py", "--out", str(out)])
    export_eval_csv.main()
    with out.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["Question", "Expected response"]
    assert len(rows) == 1 + 12
    assert all(r[0] and r[1] for r in rows[1:])


def test_export_rejects_overlong_questions():
    with pytest.raises(ValueError):
        export_eval_csv.to_rows([{"id": "x", "question": "q" * 1001, "expected_keywords": ["k"]}])


def test_export_rejects_more_than_100_rows():
    cases = [{"id": str(i), "question": "q", "expected_keywords": ["k"]} for i in range(101)]
    with pytest.raises(ValueError):
        export_eval_csv.to_rows(cases)
