"""mask_ci_values.sh emits one ::add-mask:: line per derived form of each named value."""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "mask_ci_values.sh"
GUID = "01234567-89ab-cdef-0123-456789abcdef"


def run(**env):
    result = subprocess.run(["bash", str(SCRIPT), *env.keys()], capture_output=True, text=True,
                            env={"PATH": "/usr/bin:/bin", **env})
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


def test_guid_is_masked_with_dashes_without_dashes_and_as_the_derived_host_prefix():
    lines = run(ENV_ID=GUID)
    bare = GUID.replace("-", "")
    assert f"::add-mask::{GUID}" in lines and f"::add-mask::{bare}" in lines
    assert f"::add-mask::{bare[:30]}" in lines


def test_url_is_masked_whole_and_as_a_bare_host():
    lines = run(ENV_URL="https://org123.crm.dynamics.com/")
    assert "::add-mask::https://org123.crm.dynamics.com/" in lines
    assert "::add-mask::org123.crm.dynamics.com" in lines


def test_plain_host_is_masked_once_and_empty_or_unset_values_are_skipped():
    assert run(SP_HOST="tenant.sharepoint.com", EMPTY="") == ["::add-mask::tenant.sharepoint.com"]
