"""provision_observability.sh against a stub `az` that records every call."""

import json
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONNECTION = "InstrumentationKey=k;IngestionEndpoint=https://x.in.applicationinsights.azure.com/"


def az_stub(tmp_path: Path, *, group_exists: bool, workspace_exists: bool, appi_exists: bool, extension: bool) -> Path:
    """An az stand-in: answers show/exists queries per the flags and logs each invocation."""
    log = tmp_path / "az-calls.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "az"
    script.write_text(f"""#!/usr/bin/env bash
echo "$*" >> "{log}"
case "$1 $2 $3" in
  "account show --query") echo "Test Subscription" ;;
  "extension show --name") {"exit 0" if extension else "exit 1"} ;;
  "group exists --name") echo {"true" if group_exists else "false"} ;;
  "monitor log-analytics workspace")
    if [ "$4" = show ]; then
      # Stateful like the real service: it exists if it did at the start or was created earlier in this run.
      if {"true" if workspace_exists else "false"} || grep -q "log-analytics workspace create" "{log}"; then
        echo "/subscriptions/s/resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/ws"; exit 0
      fi
      exit 1
    fi
    exit 0 ;;
  "monitor app-insights component")
    if [ "$4" = show ]; then
      if echo "$*" | grep -q connectionString; then echo "{CONNECTION}"; exit 0; fi
      if {"true" if appi_exists else "false"} || grep -q "app-insights component create" "{log}"; then exit 0; fi
      exit 1
    fi
    exit 0 ;;
  *) exit 0 ;;
esac
""")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    (bin_dir / "python3").symlink_to(sys.executable)
    return bin_dir


def run(tmp_path: Path, *args: str, **flags) -> tuple[subprocess.CompletedProcess, list[str]]:
    bin_dir = az_stub(tmp_path, **flags)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    result = subprocess.run(["bash", str(ROOT / "scripts" / "provision_observability.sh"), *args],
                            capture_output=True, text=True, env=env, cwd=tmp_path)
    calls = (tmp_path / "az-calls.log").read_text().splitlines() if (tmp_path / "az-calls.log").exists() else []
    return result, calls


def test_dry_run_plans_everything_with_the_cost_cap_and_changes_nothing(tmp_path):
    result, calls = run(tmp_path, "--dry-run", group_exists=False, workspace_exists=False, appi_exists=False, extension=False)
    assert result.returncode == 0, result.stderr
    err = result.stderr
    assert "--quota 1" in err and "--retention-time 30" in err        # daily cap and retention are always set
    assert "az group create" in err and "az monitor log-analytics workspace create" in err
    assert "application-insights" in err
    # Only read-only calls reached az during a dry run.
    assert not any(c.startswith(("group create", "extension add", "monitor log-analytics workspace create")) for c in calls)


def test_everything_existing_is_reused_and_the_connection_string_is_printed(tmp_path):
    result, calls = run(tmp_path, group_exists=True, workspace_exists=True, appi_exists=True, extension=True)
    assert result.returncode == 0, result.stderr
    assert result.stderr.count("found") == 3
    assert CONNECTION in result.stderr
    assert not any(" create" in c or c.startswith("extension add") for c in calls)


def test_missing_resources_are_created_in_dependency_order(tmp_path):
    result, calls = run(tmp_path, group_exists=False, workspace_exists=False, appi_exists=False, extension=True)
    assert result.returncode == 0, result.stderr
    creates = [c for c in calls if " create" in c]
    assert creates[0].startswith("group create")
    assert creates[1].startswith("monitor log-analytics workspace create") and "--quota 1" in creates[1]
    assert creates[2].startswith("monitor app-insights component create") and "--workspace" in creates[2]


def test_custom_cap_and_names_come_from_the_environment(tmp_path):
    bin_dir = az_stub(tmp_path, group_exists=False, workspace_exists=False, appi_exists=False, extension=True)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), "OBS_DAILY_CAP_GB": "0.25",
           "OBS_RESOURCE_GROUP": "rg-x", "OBS_LOCATION": "westus2"}
    subprocess.run(["bash", str(ROOT / "scripts" / "provision_observability.sh")], capture_output=True, text=True,
                   env=env, cwd=tmp_path)
    calls = (tmp_path / "az-calls.log").read_text()
    assert "--quota 0.25" in calls and "group create --name rg-x --location westus2" in calls
