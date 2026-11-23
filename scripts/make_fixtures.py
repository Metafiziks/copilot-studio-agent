#!/usr/bin/env python3
"""Write the synthetic access-test fixtures as minimal .docx files.

restricted.docx       - gets a sensitivity label applied by hand (inside SharePoint, as the user
                        the label is published to) that grants extract rights to the allowed user.
control.docx          - deliberately unlabeled, with a different fact, to check that
                        "no label" is not treated as "no problem".
site-restricted.docx  - goes in a separate private site only the allowed user can open; no label.

Usage: make_fixtures.py <output-dir>
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from docx_writer import write_docx  # noqa: E402

FIXTURES = {
    "restricted.docx": (
        "OL Restricted Test Document",
        "The restricted test access code is HELIOS-31.",
    ),
    "control.docx": (
        "OL Unlabeled Control Document",
        "The unlabeled control code is MAPLE-82.",
    ),
    "site-restricted.docx": (
        "OL Site-Restricted Test Document",
        "The site-restricted test code is KESTREL-19.",
    ),
}


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    out = Path(sys.argv[1])
    for name, (title, body) in FIXTURES.items():
        write_docx(out / name, [title, body])
        print(f"wrote {out / name}")


if __name__ == "__main__":
    main()
