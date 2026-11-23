# Setup

`bash scripts/provision.sh` automates sections 3 to 6 below (environment, agent, knowledge,
SharePoint sites); this guide is the manual reference behind it and a map of what each script does
under the hood. Section 5 (sharing) and the label step in [purview.md](purview.md) cannot be scripted.

Placeholders in `<angle brackets>` are yours to fill in. Commands were run on macOS.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest -q          # unit tests; no network or tenant needed
bash scripts/provision.sh --dry-run    # prints every mutating step; read-only checks run for real
```

The scripts use only the Python standard library, plus `pac` and `az` for sign-in.

## 1. Tooling

`pac` (Power Platform CLI) ships as a .NET global tool. The `pac copilot` command group
(`init`, `clone`, `pull`, `push`, `publish`, `pack`) needs a recent version; 2.9.x lacked it,
2.16.x has it.

```bash
brew install dotnet
dotnet tool install --global Microsoft.PowerApps.CLI.Tool   # or: dotnet tool update --global ...
export DOTNET_ROOT=/opt/homebrew/opt/dotnet/libexec         # Homebrew's .NET location
export PATH="$PATH:$HOME/.dotnet/tools"
pac --version
```

Add the two `export` lines to `~/.zprofile` to keep them.

## 2. Tenant prerequisites

- Microsoft 365 with SharePoint Online and a license that includes rights management
  (the test tenant used Business Premium: `RMS_S_ENTERPRISE`, `RMS_S_PREMIUM`).
- A published **sensitivity label with encryption** for the document-level test, scoped to files,
  granting VIEW and EXTRACT to the allowed user only. Content expiry must be `Never`, and it must
  not use Double Key Encryption; SharePoint cannot process those.
- Two test users, both licensed. A guest user does not work for SharePoint answers under SSO.

## 3. Environment

```bash
pac auth create --name <profile> --deviceCode --tenant <tenant-id>
pac admin create --name <env-name> --type Developer --macro-region <region> --async
pac admin status
pac auth select --name <profile>
pac org select --environment <environment-url>
```

`--macro-region` is required in practice: the CLI's default is rejected. Valid values include
`north-america`, `europe-uk`, `asia-pacific`. A Developer environment is free but meant for one
maker; the test users were given the Environment Maker role in it so they could open the agent.

## 4. The agent

`agent/` was scaffolded with `pac copilot init`, pushed to the environment once, then re-cloned so
the workspace is bound to it (the `.mcs/` folder). Two template defaults were changed because they
let the agent answer outside the SharePoint content:

- `agent.mcs.yml`: `gptCapabilities.webBrowsing: false`
- `settings.mcs.yml`: `aISettings.useModelKnowledge: false`

Authentication stays `Integrated` with trigger `Always` (Microsoft Entra sign-in), which is what
makes retrieval per-user.

### Sync commands (run inside `agent/`)

```bash
pac copilot clone --bot <agent-id> --output-dir <dir>   # one-off: creates the bound workspace
pac copilot pull
pac copilot push
pac copilot publish --bot <agent-id>
```

The CLI prints a stale timestamp after `publish`. The reliable check is the bot's `publishedon`
value in Dataverse.

### Knowledge sources

One file per SharePoint library in `agent/knowledge/`:

```yaml
mcs.metadata:
  componentName: <display name>
  description: <description>
kind: KnowledgeSourceConfiguration
source:
  kind: SharePointSearchSource
  triggerCondition: true
  site: https://<tenant>.sharepoint.com/sites/<site>/Shared%20Documents
```

The `SharePointSearchSource` kind and this layout were inferred from a cloned agent plus a
third-party reference, and confirmed by the service accepting and serving it. There is no
Microsoft schema page for it.

**`pac copilot pack` rejects a `knowledge/` directory** (`Unsupported directory`). It silently
imports an older zip if you ignore the error. Use `push`, not `pack` plus `solution import`, when
knowledge sources are involved.

## 5. Sharing

In Copilot Studio, Share the agent with the test users:

- **End user access** lets them chat but does not list the agent for them or give them a test pane.
- The test pane needs **Editor** (used here temporarily).
- **Agent viewer** is an analytics role, not a read-only test role, and conflicts with
  Environment Maker.

Remove Editor and Environment Maker when testing is done.

## 6. SharePoint sites

SharePoint REST and file downloads returned 401 for an Azure CLI token, so sites were created
through Microsoft Graph by creating a **private Microsoft 365 group**, which provisions a team site:

```bash
# POST https://graph.microsoft.com/v1.0/groups
# {"displayName":"<name>","mailNickname":"<nick>","groupTypes":["Unified"],
#  "mailEnabled":true,"securityEnabled":false,"visibility":"Private"}
# then POST /groups/<id>/members/$ref to add users; poll /groups/<id>/sites/root for the URL
```

This also creates a mailbox. New sites take roughly 20 to 30 minutes before SharePoint search, and
therefore the agent, can find their content; both users' first answers were empty until then.

## Gotchas

- **Retired model hint.** `agent.mcs.yml` pins `modelNameHint: GPT5Chat`, from the `pac copilot init`
  template. The portal reports that model as retired and silently runs "GPT Chat Latest" instead,
  with a switch-back available only until 12/12/2026. A `pull` does not change the stored hint, so
  it still says `GPT5Chat`. Before that date, choose a current model in the portal, run
  `pac copilot pull`, and commit the hint the portal writes, rather than guessing a name.

- In zsh, do not name a shell variable `GID` (read-only).
- The Graph `assignSensitivityLabel` call is a metered API and returned 403; labels were applied
  in the browser instead.
- Purview PowerShell (`Connect-IPPSSession`) has no `-Device` option and rejects an Azure CLI
  token; its interactive browser sign-in works and `Get-Label` shows a label's encryption settings.
