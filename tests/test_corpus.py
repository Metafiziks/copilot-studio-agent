import json
import zipfile
from pathlib import Path

import build_corpus
import docx_writer
import make_fixtures

ROOT = Path(__file__).resolve().parent.parent


def docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        return z.read("word/document.xml").decode("utf-8")


def test_write_docx_is_a_valid_package_with_text(tmp_path):
    out = tmp_path / "nested" / "a.docx"
    docx_writer.write_docx(out, ["Title", "Body line"])
    with zipfile.ZipFile(out) as z:
        assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= set(z.namelist())
    xml = docx_text(out)
    assert "Title" in xml and "Body line" in xml
    assert xml.count("<w:p>") == 2


def test_write_docx_escapes_markup(tmp_path):
    out = tmp_path / "e.docx"
    docx_writer.write_docx(out, ["a < b & c > d"])
    xml = docx_text(out)
    assert "a &lt; b &amp; c &gt; d" in xml


def test_txt_to_paragraphs_drops_blank_lines_and_trailing_space():
    assert build_corpus.txt_to_paragraphs("One  \n\n  \nTwo\n") == ["One", "Two"]


def test_build_preserves_subfolders_and_renames_suffix(tmp_path):
    docs = tmp_path / "docs"
    (docs / "safety").mkdir(parents=True)
    (docs / "safety" / "loto.txt").write_text("Title\nStep 1\n")
    (docs / "README.md").write_text("ignored")
    written = build_corpus.build(docs, tmp_path / "out")
    assert [p.relative_to(tmp_path / "out").as_posix() for p in written] == ["safety/loto.docx"]


def test_real_corpus_builds_and_covers_every_expected_source(tmp_path):
    built = build_corpus.build(ROOT / "docs", tmp_path)
    names = {p.name for p in built}
    assert len(built) == 6
    cases = json.loads((ROOT / "tests" / "eval_cases.json").read_text())
    expected = {s for c in cases for s in c.get("expected_sources", [])}
    assert expected, "eval cases should cite at least one source"
    assert expected <= names, f"eval cases cite files the corpus does not produce: {expected - names}"


def test_real_corpus_content_survives_conversion(tmp_path):
    build_corpus.build(ROOT / "docs", tmp_path)
    xml = docx_text(tmp_path / "safety" / "lockout_tagout_procedure.docx")
    assert "Lockout Tagout Procedure" in xml
    assert "zero energy" in xml.lower()


def test_fixtures_carry_their_codes(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["make_fixtures.py", str(tmp_path)])
    make_fixtures.main()
    assert "HELIOS-31" in docx_text(tmp_path / "restricted.docx")
    assert "MAPLE-82" in docx_text(tmp_path / "control.docx")
    assert "KESTREL-19" in docx_text(tmp_path / "site-restricted.docx")
