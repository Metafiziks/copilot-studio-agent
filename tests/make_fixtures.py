#!/usr/bin/env python3
"""Write the two synthetic access-test fixtures as minimal .docx files.

restricted.docx  - gets a sensitivity label (applied by hand) that grants extract
                   rights to the allowed user only.
control.docx     - deliberately unlabeled, with a *different* fact, to check that
                   "no label" is not treated as "no problem".

Usage: make_fixtures.py <output-dir>
"""

import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

FIXTURES = {
    "restricted.docx": (
        "OL Restricted Test Document",
        "The restricted test access code is HELIOS-31.",
    ),
    "control.docx": (
        "OL Unlabeled Control Document",
        "The unlabeled control code is TUNDRA-58.",
    ),
    # Site-level restriction test: lives in a private site that only the allowed
    # user can open. Carries no label on purpose.
    "site-restricted.docx": (
        "OL Site-Restricted Test Document",
        "The site-restricted test code is KESTREL-19.",
    ),
}

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>'
)


def document_xml(title: str, body: str) -> str:
    def para(text: str) -> str:
        return f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>"

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{para(title)}{para(body)}</w:body></w:document>"
    )


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for name, (title, body) in FIXTURES.items():
        with zipfile.ZipFile(out / name, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", CONTENT_TYPES)
            z.writestr("_rels/.rels", RELS)
            z.writestr("word/document.xml", document_xml(title, body))
        print(f"wrote {out / name}")


if __name__ == "__main__":
    main()
