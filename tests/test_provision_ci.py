"""provision.sh in CI shape: no az, no test users, environment URL supplied. Runs against stub tools."""

import os
import re
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def stub(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def run_provision(tmp_path: Path, *args: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # A pac stand-in that knows just enough: an active profile, and the agent already present.
    stub(bin_dir / "pac", f"""
case "$1 $2" in
  "auth list") echo "[1] * UNIVERSAL ci  app  Public  dev  https://x.crm.dynamics.com/" ;;
  "copilot list") echo "OL Memory Agent {AGENT_ID} Published False" ;;
  *) echo "pac $*" ;;
esac
""")
    (bin_dir / "python3").symlink_to(sys.executable)
    # No az on PATH on purpose, so any unconditional `az` requirement fails the test.
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), "SP_HOST": "x.sharepoint.com",
           "ENV_URL": "https://x.crm.dynamics.com/", **(extra_env or {})}
    return subprocess.run(["bash", str(ROOT / "scripts" / "provision.sh"), *args],
                          capture_output=True, text=True, env=env, cwd=tmp_path)


def test_skip_sharepoint_needs_neither_az_nor_the_test_users(tmp_path):
    result = run_provision(tmp_path, "--dry-run", "--skip-sharepoint",
                           extra_env={"KB_ACCESS_MODE": "protected"})
    # az is deliberately absent from PATH, so wrongly requiring it would fail this exit code.
    assert result.returncode == 0, result.stderr


def test_supplied_env_url_skips_environment_discovery(tmp_path):
    result = run_provision(tmp_path, "--dry-run", "--skip-sharepoint")
    assert result.returncode == 0, result.stderr
    assert "found: https://x.crm.dynamics.com/" in result.stderr
    assert "creating a Developer environment" not in result.stderr


def test_existing_agent_is_reused_and_pushed_and_published(tmp_path):
    result = run_provision(tmp_path, "--dry-run", "--skip-sharepoint")
    assert f"found: {AGENT_ID}" in result.stderr
    assert "pac copilot push" in result.stderr and f"pac copilot publish --bot {AGENT_ID}" in result.stderr


def run_provision_without_agent(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Like run_provision, but `pac copilot list` returns nothing: no agent, or one it cannot see."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub(bin_dir / "pac", """
case "$1 $2" in
  "auth list") echo "[1] * UNIVERSAL ci  app  Public  dev  https://x.crm.dynamics.com/" ;;
  "copilot list") echo "No Chatbots found in the environment." ;;
  *) echo "pac $*" ;;
esac
""")
    (bin_dir / "python3").symlink_to(sys.executable)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), "SP_HOST": "x.sharepoint.com",
           "ENV_URL": "https://x.crm.dynamics.com/"}
    return subprocess.run(["bash", str(ROOT / "scripts" / "provision.sh"), *args],
                          capture_output=True, text=True, env=env, cwd=tmp_path)


def test_no_create_refuses_to_create_when_the_agent_is_not_visible(tmp_path):
    # The CI failure: an identity that cannot see the agent made the script re-import the template.
    result = run_provision_without_agent(tmp_path, "--dry-run", "--skip-sharepoint", "--no-create")
    assert result.returncode != 0
    assert "--no-create" in result.stderr and "cannot see an agent" in result.stderr
    assert "creating a harness" not in result.stderr
    assert "pac copilot init" not in result.stderr


def test_without_no_create_a_missing_agent_is_created(tmp_path):
    result = run_provision_without_agent(tmp_path, "--dry-run", "--skip-sharepoint")
    assert result.returncode == 0, result.stderr
    assert "creating a harness (CliCopilot) agent" in result.stderr
    assert "pac copilot init --name OL Memory Agent --publisher-prefix olk --authoring-mode cli-copilot" in result.stderr


def test_protected_mode_still_requires_users_when_sharepoint_runs(tmp_path):
    result = run_provision(tmp_path, "--dry-run", extra_env={"KB_ACCESS_MODE": "protected"})
    assert result.returncode != 0 and "ALLOWED_USER" in result.stderr


def test_dry_run_applies_the_definition_with_site_level_knowledge(tmp_path):
    result = run_provision(tmp_path, "--dry-run", "--skip-sharepoint")
    assert result.returncode == 0, result.stderr
    assert "render_agent.py" in result.stderr and "--definition" in result.stderr
    assert re.search(r"\|https://[^|\s]+/sites/ol-knowledge(\s|$)", result.stderr)   # host may come from a local .env
    assert "Shared%20Documents" not in result.stderr      # site, not library


def test_protected_mode_adds_the_restricted_site_as_a_second_source(tmp_path):
    result = run_provision(tmp_path, "--dry-run", "--skip-sharepoint", extra_env={"KB_ACCESS_MODE": "protected"})
    assert "sites/ol-restricted" in result.stderr


def test_the_sharing_and_license_reminders_are_printed(tmp_path):
    result = run_provision(tmp_path, "--dry-run", "--skip-sharepoint")
    assert "Can use" in result.stderr and "SharePoint-bearing license" in result.stderr


def test_with_corpus_is_accepted_and_documented(tmp_path):
    result = run_provision(tmp_path, "--dry-run", "--skip-sharepoint", "--with-corpus")
    assert result.returncode == 0, result.stderr
    help_text = subprocess.run(["bash", str(ROOT / "scripts" / "provision.sh"), "--help"],
                               capture_output=True, text=True).stdout
    assert "--with-corpus" in help_text
