#!/usr/bin/env bash
# mask_ci_values.sh NAME... - hide tenant-identifying values in a GitHub Actions log.
#
# Repository *variables* are printed in clear text in run logs (only secrets are masked), and on a public
# repository those logs are public. This registers a mask for each named environment variable's value, plus
# its host part when it is a URL and its dash-less form when it is a GUID (the platform derives host names
# from environment IDs that way). Call it as an early step, before anything prints those values.
set -euo pipefail

for name in "$@"; do
  value="${!name:-}"
  [ -n "$value" ] || continue
  echo "::add-mask::$value"
  host="${value#https://}"; host="${host%%/*}"
  [ "$host" = "$value" ] || echo "::add-mask::$host"
  bare="${value//-/}"
  if [ "$bare" != "$value" ] && [ "${#bare}" -eq 32 ]; then
    echo "::add-mask::$bare"
    echo "::add-mask::${bare:0:30}"   # <env-id minus last two hex digits>.<last two>.environment.api...
  fi
done
