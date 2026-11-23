#!/usr/bin/env python3
"""Apply the repo's agent definition to a Copilot Studio agent workspace.

Usage:
  render_agent.py --workspace .build/workspace --definition agent \
      --source "OL Knowledge|Documents in the main site|https://<host>/sites/<nick>"

The agent is a harness-type ("CliCopilot") agent, so everything it is lives in two places inside a
workspace created by `pac copilot init` or `pac copilot clone`:

  settings.mcs.yml                  identity, model, instructions, memory      <- agent/settings.mcs.yml
  capabilities/knowledge/*.mcs.yml  one file per SharePoint knowledge source   <- rendered from --source

Every file under capabilities/knowledge/ is replaced, so the workspace always matches the sources
given. The knowledge-file layout was read back from agents configured in the portal and accepted by
`pac copilot push`; Microsoft publishes no schema page for it. File names are free-form
(`<schema>.<anything>.mcs.yml`); the portal appends a random suffix, this script does not.
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

TEMPLATE = """mcs.metadata:
  componentName: {name}
  description: {description}
kind: KnowledgeSourceConfiguration
source:
  kind: SharePointKnowledgeSource
  siteUrl: {site}
  targetKind: Site
"""

_PLAIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()/-]*$")
_SCHEMA = re.compile(r"^schemaName:\s*(\S+)\s*$", re.M)


def yaml_scalar(value: str) -> str:
    """Plain scalar when safe, otherwise single-quoted with '' escaping."""
    if _PLAIN.match(value) and not value.endswith(" "):
        return value
    return "'" + value.replace("'", "''") + "'"


def site_url(host: str, nick: str) -> str:
    return f"https://{host}/sites/{nick}"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower()) or "source"


def render_source(name: str, description: str, site: str) -> str:
    if not site.startswith("https://"):
        raise ValueError(f"SharePoint site must be an https URL: {site!r}")
    return TEMPLATE.format(name=yaml_scalar(name), description=yaml_scalar(description), site=site)


def schema_name(settings_text: str) -> str:
    match = _SCHEMA.search(settings_text)
    if not match:
        raise ValueError("settings.mcs.yml has no schemaName")
    return match.group(1)


def write_sources(workspace: Path, schema: str, sources: list[tuple[str, str, str]]) -> list[Path]:
    out_dir = workspace / "capabilities" / "knowledge"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.mcs.yml"):
        old.unlink()
    written, used = [], set()
    for name, description, site in sources:
        base = slug(name)
        while base in used:           # two sources with the same name still get distinct files
            base += "x"
        used.add(base)
        path = out_dir / f"{schema}.knowledge_{base}.mcs.yml"
        path.write_text(render_source(name, description, site), encoding="utf-8")
        written.append(path)
    return written


def apply_settings(workspace: Path, definition: Path) -> Path:
    """Copy the committed settings into the workspace, refusing to cross agents."""
    want = (definition / "settings.mcs.yml").read_text(encoding="utf-8")
    target = workspace / "settings.mcs.yml"
    have = target.read_text(encoding="utf-8-sig")
    if schema_name(want) != schema_name(have):
        raise ValueError(
            f"the workspace is bound to agent '{schema_name(have)}' but the repo defines '{schema_name(want)}'. "
            "Change AGENT_NAME/PUBLISHER_PREFIX in .env, or agent/settings.mcs.yml, so they agree."
        )
    shutil.copyfile(definition / "settings.mcs.yml", target)
    return target


def parse_source(arg: str) -> tuple[str, str, str]:
    parts = arg.split("|")
    if len(parts) != 3 or not all(p.strip() for p in parts):
        raise ValueError(f"--source must be 'name|description|url', got {arg!r}")
    return parts[0].strip(), parts[1].strip(), parts[2].strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--definition", type=Path, required=True, help="folder holding settings.mcs.yml")
    parser.add_argument("--source", action="append", required=True, help="name|description|site URL (repeatable)")
    args = parser.parse_args()
    try:
        sources = [parse_source(s) for s in args.source]
        print(f"wrote {apply_settings(args.workspace, args.definition)}")
        schema = schema_name((args.definition / "settings.mcs.yml").read_text(encoding="utf-8"))
        for path in write_sources(args.workspace, schema, sources):
            print(f"wrote {path}")
    except (ValueError, OSError) as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
