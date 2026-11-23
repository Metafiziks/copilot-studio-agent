"""publish_check.py and publish_agent: the pac publish-status workaround must never hide a real failure."""

import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import publish_check as pc

ROOT = Path(__file__).resolve().parent.parent
COMMON = ROOT / "scripts" / "_common.sh"

FETCH = """publishedon        synchronizationstatus   botid
11/23/2026 5:22 AM {
  "$kind": "BotSynchronizationDetails",
  "lastFinishedPublishOperation": {
    "$kind": "PublishResultDetails",
    "operationStart": "%s",
    "operationEnd": "2026-11-23T05:22:37.6445452Z",
    "status": "%s"
  },
  "currentSynchronizationState": {"state": "Synchronized"}
} bbbbbbbb-cccc-dddd-eeee-ffffffffffff
"""
SINCE = datetime(2026, 11, 23, 5, 22, 0, tzinfo=timezone.utc)


def fetched(start="2026-11-23T05:22:32.1928233Z", status="Succeeded"):
    return FETCH % (start, status)


def test_a_recent_succeeded_publish_is_accepted_including_dotnet_seven_digit_times():
    assert pc.succeeded_since(fetched(), SINCE) is True


def test_a_publish_from_before_the_request_is_not_accepted():
    assert pc.succeeded_since(fetched(start="2026-11-23T04:00:00.0000000Z"), SINCE) is False


def test_a_small_clock_skew_is_tolerated():
    assert pc.succeeded_since(fetched(start="2026-11-23T05:21:30.0000000Z"), SINCE) is True


def test_failed_or_missing_status_is_not_accepted():
    assert pc.succeeded_since(fetched(status="Failed"), SINCE) is False
    assert pc.succeeded_since("no json here", SINCE) is False
    assert pc.succeeded_since("{}", SINCE) is False
    assert pc.succeeded_since("{ not json }", SINCE) is False


# --- publish_agent in a real bash with a fake pac ---

def fake_pac(tmp_path: Path, publish_exit: int, publish_text: str, fetch_text: str = "") -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pac = bin_dir / "pac"
    (tmp_path / "publish.txt").write_text(publish_text)
    (tmp_path / "fetch.txt").write_text(fetch_text)
    pac.write_text(f"""#!/usr/bin/env bash
case "$1 $2" in
  "copilot publish") cat "{tmp_path}/publish.txt"; exit {publish_exit} ;;
  "org fetch") cat "{tmp_path}/fetch.txt" ;;
esac
""")
    pac.chmod(pac.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def run_publish(tmp_path: Path, bin_dir: Path) -> subprocess.CompletedProcess:
    import os
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return subprocess.run(["bash", "-c", f'set -euo pipefail; . "{COMMON}"; publish_agent abc'],
                          capture_output=True, text=True, cwd=tmp_path, env=env)


def test_a_clean_publish_succeeds_without_consulting_the_bot_record(tmp_path):
    result = run_publish(tmp_path, fake_pac(tmp_path, 0, "Published successfully!"))
    assert result.returncode == 0 and "checking the bot record" not in result.stderr


def test_the_known_pac_parse_error_is_accepted_only_when_the_record_shows_success(tmp_path):
    from datetime import timedelta
    now = (datetime.now(timezone.utc) + timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")
    ok = run_publish(tmp_path, fake_pac(tmp_path, 1, "System.ArgumentException: Invalid response format", fetched(start=now)))
    assert ok.returncode == 0 and "checking the bot record" in ok.stderr


def test_the_known_parse_error_still_fails_when_the_record_shows_no_new_success(tmp_path):
    stale = run_publish(tmp_path, fake_pac(tmp_path, 1, "Invalid response format", fetched(start="2026-01-01T00:00:00.0000000Z")))
    assert stale.returncode != 0
    other = tmp_path / "other"
    other.mkdir()
    failed = run_publish(other, fake_pac(other, 1, "Invalid response format", fetched(status="Failed")))
    assert failed.returncode != 0


def test_any_other_publish_error_fails_and_never_consults_the_record(tmp_path):
    result = run_publish(tmp_path, fake_pac(tmp_path, 1, "Error: you do not have permission", fetched()))
    assert result.returncode != 0 and "checking the bot record" not in result.stderr
