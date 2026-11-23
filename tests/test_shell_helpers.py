"""Tests for scripts/_common.sh helpers, run in a real bash with fake commands."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMMON = ROOT / "scripts" / "_common.sh"


def run_bash(script: str, tmp_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail; . "{COMMON}"; {script}'],
        capture_output=True, text=True, cwd=tmp_path,
    )


def flaky_command(tmp_path: Path, fail_times: int, message: str) -> str:
    """A fake command that prints `message` and fails `fail_times` times, then succeeds."""
    counter = tmp_path / "count"
    counter.write_text("0")
    cmd = tmp_path / "flaky.sh"
    cmd.write_text(
        "#!/usr/bin/env bash\n"
        f'n=$(($(cat "{counter}") + 1)); echo $n > "{counter}"\n'
        f'if [ "$n" -le {fail_times} ]; then echo "{message}"; exit 1; fi\n'
        'echo "done on attempt $n"\n'
    )
    cmd.chmod(0o755)
    return str(cmd)


def test_retry_on_succeeds_after_matching_failures(tmp_path):
    cmd = flaky_command(tmp_path, 2, "Error: Async operations are currently disabled for this organization.")
    result = run_bash(f"retry_on 'Async operations are currently disabled' 5 0 {cmd}", tmp_path)
    assert result.returncode == 0, result.stderr
    assert "done on attempt 3" in result.stderr
    assert result.stderr.count("not ready yet") == 2


def test_retry_on_quiet_hides_success_output_but_not_failures(tmp_path):
    cmd = flaky_command(tmp_path, 1, "Error: Async operations are currently disabled")
    result = run_bash(f"RETRY_QUIET=true retry_on 'Async operations are currently disabled' 3 0 {cmd}", tmp_path)
    assert result.returncode == 0
    assert "done on attempt" not in result.stderr          # success output suppressed
    assert "Async operations are currently disabled" in result.stderr  # earlier failure still shown


def test_retry_on_stops_immediately_on_a_different_error(tmp_path):
    cmd = flaky_command(tmp_path, 5, "Error: something else is wrong")
    result = run_bash(f"retry_on 'Async operations are currently disabled' 5 0 {cmd}", tmp_path)
    assert result.returncode != 0
    assert (tmp_path / "count").read_text().strip() == "1"
    assert "not ready yet" not in result.stderr


def test_retry_on_gives_up_after_the_attempt_limit(tmp_path):
    cmd = flaky_command(tmp_path, 99, "Error: Async operations are currently disabled")
    result = run_bash(f"retry_on 'Async operations are currently disabled' 3 0 {cmd}", tmp_path)
    assert result.returncode != 0
    assert (tmp_path / "count").read_text().strip() == "3"


def test_github_oidc_subject_uses_immutable_ids_when_known(tmp_path):
    # The shape GitHub actually presented, which broke a name-based credential (AADSTS700213).
    out = run_bash("github_oidc_subject Metafiziks/copilot-studio-agent dev 230289592 1468152408", tmp_path)
    assert out.stdout == "repo:Metafiziks@230289592/copilot-studio-agent@1468152408:environment:dev"


def test_github_oidc_subject_falls_back_to_the_name_form(tmp_path):
    out = run_bash("github_oidc_subject octo/hello prod", tmp_path)
    assert out.stdout == "repo:octo/hello:environment:prod"


def test_github_oidc_subject_needs_both_ids_for_the_immutable_form(tmp_path):
    out = run_bash("github_oidc_subject octo/hello prod 123", tmp_path)
    assert out.stdout == "repo:octo/hello:environment:prod"


def test_load_env_applies_defaults_and_validates_mode(tmp_path):
    ok = run_bash('load_env; echo "$ENV_NAME|$KB_ACCESS_MODE|$ENV_REGION"', tmp_path)
    assert ok.returncode == 0 and ok.stdout.strip() == "copilot-agent-dev|sample|north-america"
    bad = run_bash("KB_ACCESS_MODE=bogus load_env", tmp_path)
    assert bad.returncode != 0 and "KB_ACCESS_MODE" in bad.stderr
