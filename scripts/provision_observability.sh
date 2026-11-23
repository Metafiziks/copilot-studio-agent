#!/usr/bin/env bash
# provision_observability.sh - create the Azure Monitor resources the agent's telemetry lands in:
# a resource group, a Log Analytics workspace, and a workspace-based Application Insights component.
# The Copilot Studio counterpart of the Azure template's Log Analytics + data collection setup.
#
# Usage: bash scripts/provision_observability.sh [--dry-run]
#
# Idempotent: existing resources are reused. Cost is bounded by a DAILY INGESTION CAP on the workspace
# (default 1 GB/day) and a 30-day retention; both are configurable below.
#
# The last step cannot be scripted: Copilot Studio exposes no API or YAML field for the Application
# Insights connection (none in the agent definition or the Dataverse bot table). In the portal:
#   Settings > Advanced > Application Insights > paste the connection string printed below.
#
# Configuration (environment or .env), defaults shown:
#   OBS_RESOURCE_GROUP=rg-copilot-agent-obs   OBS_LOCATION=eastus2
#   OBS_WORKSPACE=log-copilot-agent           OBS_APPINSIGHTS=appi-copilot-agent
#   OBS_DAILY_CAP_GB=1                        OBS_RETENTION_DAYS=30
set -euo pipefail
# shellcheck source=scripts/_common.sh
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    -h|--help) sed -n '2,19p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "unknown option: $arg" ;;
  esac
done

load_env
OBS_RESOURCE_GROUP="${OBS_RESOURCE_GROUP:-rg-copilot-agent-obs}"
OBS_LOCATION="${OBS_LOCATION:-eastus2}"
OBS_WORKSPACE="${OBS_WORKSPACE:-log-copilot-agent}"
OBS_APPINSIGHTS="${OBS_APPINSIGHTS:-appi-copilot-agent}"
OBS_DAILY_CAP_GB="${OBS_DAILY_CAP_GB:-1}"
OBS_RETENTION_DAYS="${OBS_RETENTION_DAYS:-30}"

[ "$DRY_RUN" = true ] && log "DRY RUN: nothing will be changed"

log "1/4 preflight"
require_cmd az "Install the Azure CLI, then run 'az login'."
SUBSCRIPTION="$(az account show --query name -o tsv 2>/dev/null)" || die "Not signed in to az. Run: az login"
note "subscription: $SUBSCRIPTION"
# The extension is needed for `az monitor app-insights`; install it explicitly so no prompt appears.
if ! az extension show --name application-insights >/dev/null 2>&1; then
  run az extension add --name application-insights --yes --only-show-errors
fi

log "2/4 resource group '$OBS_RESOURCE_GROUP' ($OBS_LOCATION)"
if [ "$(az group exists --name "$OBS_RESOURCE_GROUP")" = true ]; then
  note "found"
else
  run az group create --name "$OBS_RESOURCE_GROUP" --location "$OBS_LOCATION" --only-show-errors -o none
fi

log "3/4 Log Analytics workspace '$OBS_WORKSPACE' (cap ${OBS_DAILY_CAP_GB} GB/day, ${OBS_RETENTION_DAYS}-day retention)"
if az monitor log-analytics workspace show -g "$OBS_RESOURCE_GROUP" -n "$OBS_WORKSPACE" >/dev/null 2>&1; then
  note "found"
else
  run az monitor log-analytics workspace create -g "$OBS_RESOURCE_GROUP" -n "$OBS_WORKSPACE" \
    --location "$OBS_LOCATION" --retention-time "$OBS_RETENTION_DAYS" --quota "$OBS_DAILY_CAP_GB" \
    --only-show-errors -o none
fi

log "4/4 Application Insights '$OBS_APPINSIGHTS' (workspace-based)"
if az monitor app-insights component show -g "$OBS_RESOURCE_GROUP" --app "$OBS_APPINSIGHTS" >/dev/null 2>&1; then
  note "found"
else
  if [ "$DRY_RUN" = true ]; then
    note "[dry-run] az monitor app-insights component create --app $OBS_APPINSIGHTS (linked to $OBS_WORKSPACE)"
  else
    workspace_id="$(az monitor log-analytics workspace show -g "$OBS_RESOURCE_GROUP" -n "$OBS_WORKSPACE" --query id -o tsv)"
    az monitor app-insights component create -g "$OBS_RESOURCE_GROUP" --app "$OBS_APPINSIGHTS" \
      --location "$OBS_LOCATION" --workspace "$workspace_id" --kind web --application-type web \
      --only-show-errors -o none
  fi
fi

if [ "$DRY_RUN" = true ]; then
  log "dry run complete; nothing was changed"
  exit 0
fi

CONNECTION="$(az monitor app-insights component show -g "$OBS_RESOURCE_GROUP" --app "$OBS_APPINSIGHTS" --query connectionString -o tsv)"
cat >&2 <<EOF

Connect the agent (portal only): Copilot Studio > agent > Settings > Advanced > Application Insights,
then paste this connection string (it is an identifier, not a secret, but treat it as private):

  $CONNECTION

BEFORE connecting a protected-documents agent: turn OFF "Log conversation details" in the same panel and
publish the agent. By default the platform's events log the user's question, the agent's reply and the
user's identity; turning it off removes the text and display name (the Entra object ID stays). See
README > Observability.

Query it from scripts with:  az monitor app-insights query --app $OBS_APPINSIGHTS -g $OBS_RESOURCE_GROUP --analytics-query "customEvents | take 5"
EOF
