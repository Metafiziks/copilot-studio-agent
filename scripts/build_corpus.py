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


def build(docs_dir: Path, out_dir: Path, flat: bool = False) -> list[Path]:
    """Convert docs/**/*.txt to .docx. With flat=True every file lands directly in out_dir (no sub-folders);
    two sources with the same file name then raise, because one would silently overwrite the other."""
    written, seen = [], set()
    for src in sorted(docs_dir.rglob("*.txt")):
        rel = Path(src.name) if flat else src.relative_to(docs_dir)
        dest = out_dir / rel.with_suffix(".docx")
        if dest in seen:
            raise ValueError(f"two documents would both be written to {dest.name}; rename one")
        seen.add(dest)
        write_docx(dest, txt_to_paragraphs(src.read_text(encoding="utf-8")))
        written.append(dest)
    return written


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--docs", type=Path, default=root / "docs")
    parser.add_argument("--out", type=Path, default=root / ".build" / "corpus")
    parser.add_argument("--flat", action="store_true", help="write every document directly in --out, no sub-folders")
    args = parser.parse_args()
    try:
        files = build(args.docs, args.out, flat=args.flat)
    except ValueError as exc:
        sys.exit(str(exc))
    if not files:
        sys.exit(f"No .txt files found under {args.docs}")
    for f in files:
        print(f"wrote {f}")
    print(f"{len(files)} document(s) in {args.out}")


if __name__ == "__main__":
    main()
