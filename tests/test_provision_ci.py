"""provision.sh in CI shape: no az, no test users, environment URL supplied. Runs against stub tools."""

import os
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
  "copilot list") echo "OL Knowledge Agent {AGENT_ID} Published False" ;;
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


def test_protected_mode_still_requires_users_when_sharepoint_runs(tmp_path):
    result = run_provision(tmp_path, "--dry-run", extra_env={"KB_ACCESS_MODE": "protected"})
    assert result.returncode != 0 and "ALLOWED_USER" in result.stderr
