#!/usr/bin/env bash
# provision.sh - stand up the whole stack: environment, SharePoint sites, agent, knowledge.
# The Copilot Studio counterpart of `azd up`.
#
# Usage: bash scripts/provision.sh [--dry-run] [--skip-sharepoint] [--skip-agent]
#
#   --dry-run          Print every mutating step instead of running it (read-only checks still run).
#   --skip-sharepoint  Leave sites and documents alone.
#   --skip-agent       Leave the environment's agent alone.
#
# Configuration comes from .env (see .env.example). Idempotent: existing environments, groups and
# agents are reused. Stages:
#   1 preflight     tools and sign-ins
#   2 environment   Developer environment via pac
#   3 sharepoint    private site(s) as Microsoft 365 groups via Graph, then the documents
#   4 agent         create from the template if missing, bind a workspace, render knowledge, push, publish
#   5 next steps    what a script cannot do (sharing, labels) and what to wait for
set -euo pipefail
# shellcheck source=scripts/_common.sh
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

SKIP_SHAREPOINT=false
SKIP_AGENT=false
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --skip-sharepoint) SKIP_SHAREPOINT=true ;;
    --skip-agent) SKIP_AGENT=true ;;
    -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "unknown option: $arg" ;;
  esac
done

load_env
: "${SP_HOST:?SP_HOST is required, e.g. contoso.sharepoint.com (see .env.example)}"
if [ "$KB_ACCESS_MODE" = protected ]; then
  : "${ALLOWED_USER:?ALLOWED_USER is required in protected mode}"
  : "${DENIED_USER:?DENIED_USER is required in protected mode}"
fi
[ "$DRY_RUN" = true ] && log "DRY RUN: nothing will be changed"

# ---------------------------------------------------------------------------------------------
log "1/5 preflight"
require_cmd pac "Install: dotnet tool install --global Microsoft.PowerApps.CLI.Tool (see guides/setup.md)"
require_cmd az "Install the Azure CLI, then run 'az login' for the target tenant."
require_cmd python3 "Python 3.10+ is required."
require_cmd rsync "rsync is required to stage the agent template."
pac auth list 2>&1 | grep -q '\*' || die "No active pac profile. Run: pac auth create --deviceCode --tenant <tenant-id>"
az account show >/dev/null 2>&1 || die "Not signed in to az. Run: az login --tenant <tenant-id> --allow-no-subscriptions"
note "pac: $(pac auth list 2>&1 | grep '\*' | head -1 | tr -s ' ' | cut -c1-80)"
note "mode: $KB_ACCESS_MODE"

# ---------------------------------------------------------------------------------------------
log "2/5 environment '$ENV_NAME'"
ENV_URL="$(env_url)"
if [ -n "$ENV_URL" ]; then
  note "found: $ENV_URL"
else
  note "not found; creating a Developer environment in $ENV_REGION"
  run pac admin create --name "$ENV_NAME" --type Developer --macro-region "$ENV_REGION" --async
  if [ "$DRY_RUN" = false ]; then
    for _ in $(seq 1 120); do
      ENV_URL="$(env_url)"
      [ -n "$ENV_URL" ] && break
      sleep 10
    done
    [ -n "$ENV_URL" ] || die "environment did not appear within 20 minutes; check: pac admin status"
    note "created: $ENV_URL"
  else
    ENV_URL="https://<new-environment>.crm.dynamics.com/"
  fi
fi
run pac org select --environment "$ENV_URL"

# ---------------------------------------------------------------------------------------------
KB_URL="https://${SP_HOST}/sites/${KB_SITE_NICK}/Shared%20Documents"
RESTRICTED_URL="https://${SP_HOST}/sites/${RESTRICTED_SITE_NICK}/Shared%20Documents"

sp() { python3 "$ROOT/scripts/sharepoint.py" "$@"; }

if [ "$SKIP_SHAREPOINT" = true ]; then
  log "3/5 sharepoint (skipped)"
else
  log "3/5 sharepoint"
  mkdir -p "$ROOT/.build"
  rm -rf "$ROOT/.build/upload-kb" "$ROOT/.build/upload-restricted"
  if [ "$KB_ACCESS_MODE" = sample ]; then
    members=()
    for u in $(printf '%s' "${KB_MEMBERS:-}" | tr ',' ' '); do members+=(--member "$u"); done
    python3 "$ROOT/scripts/build_corpus.py" --out "$ROOT/.build/upload-kb" >/dev/null
    note "built $(find "$ROOT/.build/upload-kb" -name '*.docx' | wc -l | tr -d ' ') .docx documents from docs/"
    run sp ensure-site --nick "$KB_SITE_NICK" --title "$KB_SITE_TITLE" ${members[@]+"${members[@]}"}
    run sp upload --nick "$KB_SITE_NICK" --dir "$ROOT/.build/upload-kb"
  else
    # Protected mode never uploads the sample corpus: only the two-user acceptance fixtures.
    python3 "$ROOT/scripts/make_fixtures.py" "$ROOT/.build/fixtures" >/dev/null
    mkdir -p "$ROOT/.build/upload-kb" "$ROOT/.build/upload-restricted"
    cp "$ROOT/.build/fixtures/control.docx" "$ROOT/.build/fixtures/restricted.docx" "$ROOT/.build/upload-kb/"
    cp "$ROOT/.build/fixtures/site-restricted.docx" "$ROOT/.build/upload-restricted/"
    run sp ensure-site --nick "$KB_SITE_NICK" --title "$KB_SITE_TITLE" --member "$ALLOWED_USER" --member "$DENIED_USER"
    run sp upload --nick "$KB_SITE_NICK" --dir "$ROOT/.build/upload-kb"
    run sp ensure-site --nick "$RESTRICTED_SITE_NICK" --title "$RESTRICTED_SITE_TITLE" --member "$ALLOWED_USER"
    run sp upload --nick "$RESTRICTED_SITE_NICK" --dir "$ROOT/.build/upload-restricted"
  fi
fi

# ---------------------------------------------------------------------------------------------
if [ "$SKIP_AGENT" = true ]; then
  log "4/5 agent (skipped)"
else
  log "4/5 agent '$AGENT_NAME'"
  AGENT_ID="$(agent_id)"
  if [ -z "$AGENT_ID" ]; then
    note "not found; creating from the template (pack cannot carry knowledge, so it is added after)"
    stage="$(mktemp -d)"
    mkdir -p "$stage/workspace"
    rsync -a --exclude knowledge --exclude .mcs "$ROOT/agent/" "$stage/workspace/"
    run pac copilot pack --publisher-prefix "$PUBLISHER_PREFIX" --project-dir "$stage/workspace" \
      --solution-name "$SOLUTION_NAME" --output-path "$stage"
    run pac solution import --path "$stage/$SOLUTION_NAME.zip" --publish-changes --async --max-async-wait-time 30
    if [ "$DRY_RUN" = false ]; then
      AGENT_ID="$(agent_id)"
      [ -n "$AGENT_ID" ] || die "agent not found after import; check: pac copilot list"
    else
      AGENT_ID="<new-agent-id>"
    fi
  else
    note "found: $AGENT_ID"
  fi

  # A pushable workspace needs the .mcs/ binding for THIS environment. Re-create it with a clone
  # when it is missing or points elsewhere.
  if ! grep -qF "${ENV_URL%/}" "$ROOT/agent/.mcs/conn.json" 2>/dev/null; then
    note "binding agent/ to this environment"
    if [ "$DRY_RUN" = true ]; then
      note "[dry-run] pac copilot clone --bot $AGENT_ID, then copy its .mcs/ into agent/"
    else
      clone_dir="$(mktemp -d)"
      pac copilot clone --bot "$AGENT_ID" --output-dir "$clone_dir" >/dev/null
      src="$(find "$clone_dir" -maxdepth 2 -name .mcs -type d | head -1)"
      [ -n "$src" ] || die "clone did not produce a .mcs/ folder"
      rm -rf "$ROOT/agent/.mcs"
      cp -R "$src" "$ROOT/agent/.mcs"
    fi
  fi

  sources=(--source "OL Knowledge Library|Documents in the main knowledge site|$KB_URL")
  if [ "$KB_ACCESS_MODE" = protected ]; then
    sources+=(--source "OL Restricted Library|Documents in the restricted site|$RESTRICTED_URL")
  fi
  run python3 "$ROOT/scripts/render_knowledge.py" --out "$ROOT/agent/knowledge" "${sources[@]}"

  if [ "$DRY_RUN" = true ]; then
    note "[dry-run] (cd agent && pac copilot push) && pac copilot publish --bot $AGENT_ID"
  else
    (cd "$ROOT/agent" && pac copilot push)
    pac copilot publish --bot "$AGENT_ID"
    published="$(pac copilot list 2>/dev/null | grep -F "$AGENT_NAME" | tr -s ' ' | cut -c1-120)"
    note "agent: $published"
  fi
fi

# ---------------------------------------------------------------------------------------------
log "5/5 next steps"
cat >&2 <<EOF
  Environment : $ENV_URL
  Knowledge   : $KB_URL
EOF
[ "$KB_ACCESS_MODE" = protected ] && echo "  Restricted  : $RESTRICTED_URL" >&2
cat >&2 <<EOF

  Not scriptable, do these by hand:
   1. Share the agent: Copilot Studio > agent > Share. Grant users 'End user access'; the test pane
      needs 'Editor' (remove it afterwards).
   2. Wait for SharePoint indexing. New sites take roughly 20-30 minutes before the agent can find
      their content; 'Search this site' in SharePoint is a cheap check.
EOF
if [ "$KB_ACCESS_MODE" = protected ]; then
  cat >&2 <<EOF
   3. Apply the sensitivity label to restricted.docx INSIDE SharePoint: as the user the label policy
      is published to, in Word for the web (normal window, Editing mode), use Sensitivity. Files
      labeled elsewhere before upload are not enforced. See guides/purview.md.
   4. Run the two-user check from guides/purview.md.
EOF
fi
[ "$DRY_RUN" = true ] && log "dry run complete; nothing was changed"
exit 0
