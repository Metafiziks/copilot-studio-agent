#!/usr/bin/env bash
# Shared helpers for provision.sh and teardown.sh. Source this file; do not run it.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY_RUN=false

# pac is a .NET global tool. These match a Homebrew install on macOS; override in .env if yours differ.
if [ -z "${DOTNET_ROOT:-}" ] && [ -d /opt/homebrew/opt/dotnet/libexec ]; then
  export DOTNET_ROOT=/opt/homebrew/opt/dotnet/libexec
fi
case ":$PATH:" in *":$HOME/.dotnet/tools:"*) ;; *) export PATH="$PATH:$HOME/.dotnet/tools" ;; esac

log()   { printf '► %s\n' "$*" >&2; }
note()  { printf '  %s\n' "$*" >&2; }
warn()  { printf 'WARNING: %s\n' "$*" >&2; }
die()   { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# run <command...>: execute, or print what would run under --dry-run.
run() {
  if [ "$DRY_RUN" = true ]; then
    printf '  [dry-run] %s\n' "$*" >&2
  else
    "$@"
  fi
}

# retry_on <regex> <attempts> <wait-seconds> <command...>
# Run the command. If it fails and its output matches <regex>, wait and try again, up to <attempts>
# times. Any other failure returns immediately. Used for conditions that clear on their own, such as
# a freshly created environment that is not yet ready for async operations.
retry_on() {
  local pattern="$1" attempts="$2" wait="$3"
  shift 3
  local out i
  for i in $(seq 1 "$attempts"); do
    if out="$("$@" 2>&1)"; then
      # RETRY_QUIET=true hides the output of a successful run (failures are always shown).
      [ "${RETRY_QUIET:-false}" = true ] || printf '%s\n' "$out" >&2
      return 0
    fi
    printf '%s\n' "$out" >&2
    printf '%s' "$out" | grep -Eqi "$pattern" || return 1
    note "not ready yet ($i/$attempts); waiting ${wait}s ..."
    sleep "$wait"
  done
  return 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is required. $2"
}

# Load .env (if present), then apply defaults for anything unset.
load_env() {
  if [ -f "$ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$ROOT/.env"
    set +a
  fi
  ENV_NAME="${ENV_NAME:-copilot-agent-dev}"
  ENV_REGION="${ENV_REGION:-north-america}"
  AGENT_NAME="${AGENT_NAME:-OL Knowledge Agent}"
  PUBLISHER_PREFIX="${PUBLISHER_PREFIX:-olk}"
  SOLUTION_NAME="${SOLUTION_NAME:-OLKnowledgeAgent}"
  KB_ACCESS_MODE="${KB_ACCESS_MODE:-sample}"
  KB_SITE_NICK="${KB_SITE_NICK:-ol-knowledge}"
  KB_SITE_TITLE="${KB_SITE_TITLE:-OL Knowledge}"
  RESTRICTED_SITE_NICK="${RESTRICTED_SITE_NICK:-ol-restricted}"
  RESTRICTED_SITE_TITLE="${RESTRICTED_SITE_TITLE:-OL Restricted}"
  case "$KB_ACCESS_MODE" in
    sample|protected) ;;
    *) die "KB_ACCESS_MODE must be 'sample' or 'protected' (got '$KB_ACCESS_MODE')" ;;
  esac
}

# Print the URL of the environment named $ENV_NAME, or nothing if it does not exist.
env_url() {
  pac admin list 2>/dev/null | grep -F "$ENV_NAME" | grep -oE 'https://[^ ]+' | head -1 || true
}

# Print the ID of the agent named $AGENT_NAME in the selected environment, or nothing.
agent_id() {
  pac copilot list 2>/dev/null | grep -F "$AGENT_NAME" | grep -oE '[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}' | head -1 || true
}
