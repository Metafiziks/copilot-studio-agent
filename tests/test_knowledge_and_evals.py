import csv
import json
import re
from pathlib import Path

import pytest

import export_eval_csv
import render_agent

ROOT = Path(__file__).resolve().parent.parent
CASES = json.loads((ROOT / "tests" / "eval_cases.json").read_text())

SCHEMA = "olk_OLMemoryAgent"


# --- render_agent: knowledge sources ---

def test_render_source_is_a_harness_sharepoint_knowledge_source():
    text = render_agent.render_source("OL Knowledge", "Main docs", "https://h.sharepoint.com/sites/x")
    assert "kind: KnowledgeSourceConfiguration" in text
    assert "kind: SharePointKnowledgeSource" in text
    assert "siteUrl: https://h.sharepoint.com/sites/x" in text
    assert "targetKind: Site" in text
    assert "componentName: OL Knowledge" in text


def test_render_source_rejects_non_https():
    with pytest.raises(ValueError):
        render_agent.render_source("n", "d", "http://h/sites/x")


def test_yaml_scalar_quotes_unsafe_values():
    assert render_agent.yaml_scalar("Plain name") == "Plain name"
    assert render_agent.yaml_scalar("Name: with colon") == "'Name: with colon'"
    assert render_agent.yaml_scalar("It's #1") == "'It''s #1'"


def test_site_url_is_the_site_not_the_library():
    assert render_agent.site_url("t.sharepoint.com", "ol-knowledge") == "https://t.sharepoint.com/sites/ol-knowledge"


def test_write_sources_replaces_every_stale_knowledge_file(tmp_path):
    kdir = tmp_path / "capabilities" / "knowledge"
    kdir.mkdir(parents=True)
    # The portal names files with a random suffix; those must go too, or the agent keeps old sources.
    (kdir / f"{SCHEMA}.olknowledge__XKZ0KgYeeqJCLxBbrb96.mcs.yml").write_text("stale")
    sources = [("OL Knowledge", "first", "https://h/sites/a"), ("OL Restricted", "second", "https://h/sites/b")]
    written = render_agent.write_sources(tmp_path, SCHEMA, sources)
    assert sorted(p.name for p in kdir.iterdir()) == [
        f"{SCHEMA}.knowledge_olknowledge.mcs.yml", f"{SCHEMA}.knowledge_olrestricted.mcs.yml"]
    assert "siteUrl: https://h/sites/b" in written[1].read_text()


def test_same_named_sources_get_distinct_files(tmp_path):
    written = render_agent.write_sources(tmp_path, SCHEMA, [("Docs", "a", "https://h/sites/a"), ("Docs", "b", "https://h/sites/b")])
    assert len({p.name for p in written}) == 2


def test_parse_source_requires_three_parts():
    assert render_agent.parse_source("a|b|https://x") == ("a", "b", "https://x")
    with pytest.raises(ValueError):
        render_agent.parse_source("a|b")


# --- render_agent: the committed agent definition ---

def test_committed_settings_describe_a_memory_enabled_harness_agent():
    text = (ROOT / "agent" / "settings.mcs.yml").read_text()
    assert "authoringModel: CliCopilot" in text and "kind: CLICopilotRecognizer" in text
    assert "enableMemory: true" in text
    assert render_agent.schema_name(text) == SCHEMA


def test_apply_settings_copies_the_definition_when_the_agent_matches(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "settings.mcs.yml").write_bytes(b"\xef\xbb\xbfdisplayName: x\nschemaName: " + SCHEMA.encode() + b"\nenableMemory: false\n")
    render_agent.apply_settings(ws, ROOT / "agent")
    assert "enableMemory: true" in (ws / "settings.mcs.yml").read_text()


def test_apply_settings_refuses_to_overwrite_a_different_agent(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "settings.mcs.yml").write_text("schemaName: olk_SomethingElse\n")
    with pytest.raises(ValueError, match="olk_SomethingElse"):
        render_agent.apply_settings(ws, ROOT / "agent")
    assert "olk_SomethingElse" in (ws / "settings.mcs.yml").read_text()   # untouched


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
