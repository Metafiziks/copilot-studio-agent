#!/usr/bin/env python3
"""Render the agent's SharePoint knowledge-source files from site URLs.

Usage:
  render_knowledge.py --out agent/knowledge \
      --source "OL Knowledge Library|Documents in the main site|https://<host>/sites/<nick>/Shared%20Documents"

Existing SharePointSearchSource.*.mcs.yml files in --out are replaced, so the folder always matches
the sources given. The file kind (`SharePointSearchSource`) and layout were inferred from a cloned
agent and accepted by the service; Microsoft publishes no schema page for it.
"""

import argparse
import re
import sys
from pathlib import Path

TEMPLATE = """mcs.metadata:
  componentName: {name}
  description: {description}
kind: KnowledgeSourceConfiguration
source:
  kind: SharePointSearchSource
  triggerCondition: true
  site: {site}
"""

_PLAIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()/-]*$")


def yaml_scalar(value: str) -> str:
    """Plain scalar when safe, otherwise single-quoted with '' escaping."""
    if _PLAIN.match(value) and not value.endswith(" "):
        return value
    return "'" + value.replace("'", "''") + "'"


def library_url(host: str, nick: str) -> str:
    return f"https://{host}/sites/{nick}/Shared%20Documents"


def render_source(name: str, description: str, site: str) -> str:
    if not site.startswith("https://"):
        raise ValueError(f"SharePoint site must be an https URL: {site!r}")
    return TEMPLATE.format(name=yaml_scalar(name), description=yaml_scalar(description), site=site)


def write_sources(out_dir: Path, sources: list[tuple[str, str, str]]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("SharePointSearchSource.*.mcs.yml"):
        old.unlink()
    written = []
    for index, (name, description, site) in enumerate(sources):
        path = out_dir / f"SharePointSearchSource.{index}.mcs.yml"
        path.write_text(render_source(name, description, site), encoding="utf-8")
        written.append(path)
    return written


def parse_source(arg: str) -> tuple[str, str, str]:
    parts = arg.split("|")
    if len(parts) != 3 or not all(p.strip() for p in parts):
        raise ValueError(f"--source must be 'name|description|url', got {arg!r}")
    return parts[0].strip(), parts[1].strip(), parts[2].strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source", action="append", required=True, help="name|description|url (repeatable)")
    args = parser.parse_args()
    try:
        sources = [parse_source(s) for s in args.source]
        for path in write_sources(args.out, sources):
            print(f"wrote {path}")
    except ValueError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
