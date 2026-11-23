#!/usr/bin/env python3
"""Convert the plain-text corpus in docs/ to .docx files ready to upload to SharePoint.

Usage: build_corpus.py [--docs docs] [--out .build/corpus]

Subfolders are preserved (safety/, quality/, ...), and each .txt becomes a .docx with the same
stem, so `lockout_tagout_procedure.txt` is cited as `lockout_tagout_procedure.docx`.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from docx_writer import write_docx  # noqa: E402


def txt_to_paragraphs(text: str) -> list[str]:
    """One paragraph per non-empty line; trailing whitespace is dropped."""
    return [line.rstrip() for line in text.splitlines() if line.strip()]


def build(docs_dir: Path, out_dir: Path) -> list[Path]:
    written = []
    for src in sorted(docs_dir.rglob("*.txt")):
        dest = out_dir / src.relative_to(docs_dir).with_suffix(".docx")
        write_docx(dest, txt_to_paragraphs(src.read_text(encoding="utf-8")))
        written.append(dest)
    return written


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--docs", type=Path, default=root / "docs")
    parser.add_argument("--out", type=Path, default=root / ".build" / "corpus")
    args = parser.parse_args()
    files = build(args.docs, args.out)
    if not files:
        sys.exit(f"No .txt files found under {args.docs}")
    for f in files:
        print(f"wrote {f}")
    print(f"{len(files)} document(s) in {args.out}")


if __name__ == "__main__":
    main()
