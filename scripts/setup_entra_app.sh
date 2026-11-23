#!/usr/bin/env bash
# setup_entra_app.sh - create (or reuse) the Entra app registration that lets scripts call the agent
# as a signed-in user, and optionally lets GitHub Actions authenticate without a secret.
#
# Usage: bash scripts/setup_entra_app.sh [--dry-run] [--github-repo owner/repo] [--github-environment NAME]
#
# What it creates, with the least privilege that works:
#   * a single-tenant app registration, public client, redirect URI http://localhost, NO client secret
#   * delegated permissions on the Power Platform API: CopilotStudio.Copilots.Invoke (chat with the
#     agent as the user) and CopilotStudio.MakerOperations.ReadWrite (trigger Agent Evaluation runs;
#     the documentation does not name the scope it needs, so this is an assumption to verify)
#   * Microsoft Graph User.Read (delegated), the sign-in default
#   * with --github-repo: a federated credential so GitHub Actions can sign in without a secret
#
# It deliberately does NOT add the Power Platform API *application* permission. This agent retrieves
# SharePoint content as the signed-in user, which an app-only caller does not have.
#
# Prints the CLIENT_ID and TENANT_ID to add to .env. Requires `az login` as a user who may register
# apps and grant admin consent. Idempotent.
set -euo pipefail
# shellcheck source=scripts/_common.sh
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

POWER_PLATFORM_API=8578e004-a5c6-46e7-913e-12f58912df43   # first-party resource application
GRAPH_API=00000003-0000-0000-c000-000000000000
APP_NAME="${EVAL_APP_NAME:-copilot-agent-eval}"
GITHUB_REPO=""
GITHUB_ENV=""

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --github-repo) GITHUB_REPO="${2:?--github-repo needs owner/repo}"; shift ;;
    --github-environment) GITHUB_ENV="${2:?--github-environment needs a name}"; shift ;;
    -h|--help) sed -n '2,22p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
  shift
done

require_cmd az "Install the Azure CLI, then run 'az login' for the target tenant."
TENANT_ID="$(az account show --query tenantId -o tsv 2>/dev/null)" || die "Not signed in to az. Run: az login"

# Resolve a delegated scope id by name, so nothing is hard-coded to one tenant.
scope_id() { # <resource-app-id> <scope-name>
  az ad sp show --id "$1" --query "oauth2PermissionScopes[?value=='$2'].id | [0]" -o tsv
}

log "1/4 app registration '$APP_NAME'"
APP_ID="$(az ad app list --display-name "$APP_NAME" --query "[0].appId" -o tsv 2>/dev/null || true)"
if [ -n "$APP_ID" ]; then
  note "found: $APP_ID"
else
  INVOKE="$(scope_id "$POWER_PLATFORM_API" CopilotStudio.Copilots.Invoke)"
  MAKER="$(scope_id "$POWER_PLATFORM_API" CopilotStudio.MakerOperations.ReadWrite)"
  USER_READ="$(scope_id "$GRAPH_API" User.Read)"
  [ -n "$INVOKE" ] && [ -n "$MAKER" ] && [ -n "$USER_READ" ] \
    || die "could not resolve scope ids; is the Power Platform API present? (az ad sp create --id $POWER_PLATFORM_API)"
  access="$(mktemp)"
  cat > "$access" <<EOF
[
  {"resourceAppId": "$POWER_PLATFORM_API",
   "resourceAccess": [{"id": "$INVOKE", "type": "Scope"}, {"id": "$MAKER", "type": "Scope"}]},
  {"resourceAppId": "$GRAPH_API",
   "resourceAccess": [{"id": "$USER_READ", "type": "Scope"}]}
]
EOF
  if [ "$DRY_RUN" = true ]; then
    note "[dry-run] az ad app create --display-name $APP_NAME (single tenant, public client, http://localhost)"
    APP_ID="<new-app-id>"
  else
    APP_ID="$(az ad app create --display-name "$APP_NAME" --sign-in-audience AzureADMyOrg \
      --public-client-redirect-uris http://localhost --is-fallback-public-client true \
      --required-resource-accesses "@$access" --query appId -o tsv)"
    note "created: $APP_ID"
  fi
fi

log "2/4 service principal"
if [ "$DRY_RUN" = true ]; then
  note "[dry-run] az ad sp create --id $APP_ID (if missing)"
elif az ad sp show --id "$APP_ID" >/dev/null 2>&1; then
  note "found"
else
  # The app can take a few seconds to become visible to the directory.
  RETRY_QUIET=true retry_on 'does not exist|not found|ResourceNotFound' 6 10 az ad sp create --id "$APP_ID"
  note "created"
fi

log "3/4 admin consent for the delegated permissions"
if [ "$DRY_RUN" = true ]; then
  note "[dry-run] az ad app permission admin-consent --id $APP_ID"
else
  # A brand-new app reports "removed or is configured to use an incorrect application identifier"
  # until the directory has replicated it; that clears on its own.
  RETRY_QUIET=true retry_on 'does not exist|not found|ResourceNotFound|propagat|removed or is configured' 12 15 \
    az ad app permission admin-consent --id "$APP_ID" \
    && note "granted" \
    || warn "admin consent failed; grant it in the Entra portal (App registrations > $APP_NAME > API permissions)"
fi

log "4/4 GitHub federated credential"
if [ -z "$GITHUB_REPO" ]; then
  note "skipped (no --github-repo). Re-run with --github-repo owner/repo once the repository exists."
else
  subject="repo:${GITHUB_REPO}:environment:${GITHUB_ENV:-dev}"
  cred_name="github-$(printf '%s' "${GITHUB_REPO}-${GITHUB_ENV:-dev}" | tr '/' '-')"
  existing="$(az ad app federated-credential list --id "$APP_ID" --query "[?name=='$cred_name'].name | [0]" -o tsv 2>/dev/null || true)"
  if [ -n "$existing" ]; then
    note "found: $cred_name"
  else
    params="$(mktemp)"
    cat > "$params" <<EOF
{"name": "$cred_name", "issuer": "https://token.actions.githubusercontent.com",
 "subject": "$subject", "audiences": ["api://AzureADTokenExchange"]}
EOF
    run az ad app federated-credential create --id "$APP_ID" --parameters "@$params" >/dev/null
    note "created: $cred_name (subject $subject)"
  fi
fi

cat >&2 <<EOF

Add to .env:
  EVAL_CLIENT_ID=$APP_ID
  EVAL_TENANT_ID=$TENANT_ID
EOF
[ "$DRY_RUN" = true ] && log "dry run complete; nothing was changed"
exit 0
