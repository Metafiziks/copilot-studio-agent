#!/usr/bin/env bash
# teardown.sh - remove what provision.sh created. The counterpart of `azd down`.
#
# Usage: bash scripts/teardown.sh --yes [--dry-run] [--delete-environment] [--keep-sites]
#
#   --yes                  Required. Nothing is deleted without it.
#   --dry-run              Print the steps without running them.
#   --delete-environment   Also delete the Developer environment (and with it the agent).
#   --keep-sites           Leave the SharePoint sites/groups in place.
#
# SharePoint groups are deleted AND purged. Without the purge, Entra keeps the soft-deleted group for
# 30 days and reserves its mail nickname, which blocks provisioning the same site name again.
set -euo pipefail
# shellcheck source=scripts/_common.sh
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

YES=false
DELETE_ENV=false
KEEP_SITES=false
for arg in "$@"; do
  case "$arg" in
    --yes) YES=true ;;
    --dry-run) DRY_RUN=true ;;
    --delete-environment) DELETE_ENV=true ;;
    --keep-sites) KEEP_SITES=true ;;
    -h|--help) sed -n '2,14p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "unknown option: $arg" ;;
  esac
done
[ "$YES" = true ] || [ "$DRY_RUN" = true ] || die "refusing to delete without --yes (use --dry-run to preview)"

load_env
require_cmd python3 "Python 3.10+ is required."
sp() { python3 "$ROOT/scripts/sharepoint.py" "$@"; }

if [ "$KEEP_SITES" = true ]; then
  log "SharePoint sites kept"
else
  log "deleting SharePoint sites"
  run sp delete-site --nick "$KB_SITE_NICK" --purge
  run sp delete-site --nick "$RESTRICTED_SITE_NICK" --purge
fi

if [ "$DELETE_ENV" = true ]; then
  require_cmd pac "Install the Power Platform CLI (see guides/setup.md)."
  url="$(env_url)"
  if [ -n "$url" ]; then
    log "deleting environment '$ENV_NAME' ($url)"
    run pac admin delete --environment "$url"
  else
    note "environment '$ENV_NAME' not found; nothing to delete"
  fi
else
  note "environment kept (add --delete-environment to remove it)"
fi
[ "$DRY_RUN" = true ] && log "dry run complete; nothing was changed"
exit 0
