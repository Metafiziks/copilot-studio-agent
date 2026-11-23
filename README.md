# copilot-studio-agent

A **Microsoft Copilot Studio** agent over your own SharePoint documents, defined as code and
provisioned with one script. Ask a question, get an answer with cited source files, drawn only from
what the **signed-in user** is allowed to see, from an agent that **remembers each user's preferences
across conversations**. It is the Copilot Studio counterpart of
[azd-foundry-search-agent](https://github.com/Metafiziks/azd-foundry-search-agent): same sample
corpus, same eval cases, same protected-mode idea, different platform.

> **Status.** Built as a **memory-enabled harness agent** (Claude Sonnet 4.6) and exercised against a real
> tenant. Done and verified: one-script provision (create or update, idempotent), per-user memory,
> per-user restriction at site and document level (two users, side by side), CI and OIDC deploy, and a
> **passing scored eval** (keyword recall 0.99, citation recall 1.00, p95 25.7 s, memory recall 1.00, run
> as a licensed user). **Not done:** the built-in LLM-judged graders and HHEM, anomaly detection, and
> per-agent telemetry (an environment-level preview export is set up on one test environment, with nothing
> received yet). **Two things will silently break it:** a user without a SharePoint-bearing license, and a
> brand-new SharePoint site (see Troubleshooting). Each section says exactly where it stands.

## Architecture

```mermaid
flowchart LR
    subgraph deploy["scripts/provision.sh"]
        direction TB
        D1["environment<br/>Developer env via pac"] --> D2["sharepoint<br/>private sites via Graph · docs uploaded"]
        D2 --> D3["agent<br/>pac copilot init · apply definition · push · publish"]
    end

    U([User]) -->|"Entra sign-in<br/>(SharePoint-licensed)"| A["Copilot Studio harness agent<br/>Claude Sonnet 4.6 · Memory on<br/>definition in agent/"]
    A -->|"knowledge search<br/>as the signed-in user"| S[("SharePoint<br/>Microsoft Search")]
    S -->|"security trimming<br/>site permissions · label encryption"| A
    D3 -.-> A
    D2 -.-> S
    A -.->|"not connected"| AI["Application Insights"]
    E["Evaluation<br/>scripts/run_evals.py<br/>as a licensed user"] --> A
```

The agent runs entirely inside Copilot Studio: there is no code to host. It is a **harness-type agent**
(`authoringModel: CliCopilot`, Claude Sonnet 4.6): the model plans, calls SharePoint search and
file tools itself, and keeps per-user memory. Its model, instructions and memory setting live in
[`agent/settings.mcs.yml`](agent/settings.mcs.yml); the SharePoint knowledge sources are rendered from your
site URLs by [`scripts/render_agent.py`](scripts/render_agent.py); `pac copilot` syncs both.

**SharePoint is the source of truth.** Retrieval runs under the signed-in user's identity, so site
permissions and encrypted sensitivity labels decide what an answer can draw on. See
[protected mode](guides/purview.md). Memory holds preferences only; documents supply the answers.

> **Every user needs a SharePoint-bearing license.** The agent's search tools run as the signed-in
> user, and for a user without one (for example Microsoft 365 Business Premium) they fail with
> "Knowledge search · unknown" and the agent says it found nothing. An unlicensed **site owner** can
> still open files and fetch them by name, which makes the failure look like a missing document. This
> cost days of misdiagnosis in testing; see [Troubleshooting](#troubleshooting).

## Quick start

**Prerequisites:** the [Power Platform CLI](https://learn.microsoft.com/power-platform/developer/cli/introduction)
(`pac`, version with `pac copilot`; 2.16 worked) and its .NET runtime, the
[Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli), Python 3.10+, and a Microsoft 365
tenant with SharePoint Online. Setup details are in [guides/setup.md](guides/setup.md).

```bash
git clone https://github.com/Metafiziks/copilot-studio-agent && cd copilot-studio-agent
pac auth create --deviceCode --tenant <tenant-id>
az login --tenant <tenant-id> --allow-no-subscriptions     # sign in as the SAME identity
cp .env.example .env                                         # set SP_HOST at minimum
bash scripts/provision.sh --dry-run                          # preview every step
bash scripts/provision.sh
```

| Stage | What happens |
|---|---|
| Preflight | Checks `pac`, `az`, Python and both sign-ins |
| Environment | Creates a Developer environment if missing |
| SharePoint | Creates a private site (a Microsoft 365 group) through Graph, converts `docs/` to `.docx`, uploads it |
| Agent | Creates the harness agent if missing (`pac copilot init`, about 3 minutes), applies `agent/settings.mcs.yml` and the SharePoint knowledge sources to a workspace rebuilt from the live agent, pushes and publishes |
| Next steps | Prints what a script cannot do: sharing the agent, checking licenses and applying labels |

Then share the agent (Copilot Studio → Share → **Can use**; without it the Agents SDK gets a 404 for that
user), make sure each user is SharePoint-licensed, and **wait for SharePoint to index a new site**,
roughly 20 to 30 minutes, before asking questions.

Tear down:

```bash
bash scripts/teardown.sh --yes --delete-environment
```

Groups are deleted **and purged**; otherwise Entra reserves the site name for 30 days.

## Bring your own documents

Replace `docs/` with your own `.txt` files, adjust the instructions in `agent/settings.mcs.yml`, update
`tests/eval_cases.json`, and re-run `bash scripts/provision.sh`. SharePoint knowledge accepts
`.docx`, `.pdf`, `.pptx` and `.xlsx` (not `.txt`), so text files are converted on upload and cited as
`.docx`. Or skip `docs/` and put those formats straight into the SharePoint library. Sample mode lets
every member of the site read the documents; for internal knowledge bases use
[protected mode](guides/purview.md): per-user trimming by site permission and by encrypted
sensitivity label.

## Evaluations

**Status: passing.** 13 cases (12 document questions, 1 two-turn memory case), scored on real replies from a
licensed test user against the dev agent.

`tests/eval_cases.json` holds the same 13 cases as the Azure template (12 RAG cases with expected
keywords and sources, one two-turn memory case), with source names mapped to `.docx`.

**Deterministic metrics** (keyword recall, citation recall, p95 latency, memory recall, failed calls) run
from a script that calls the agent as a signed-in user through Microsoft's Agents SDK. One-time setup:

```bash
bash scripts/setup_entra_app.sh          # app registration, no secret; prints EVAL_CLIENT_ID / EVAL_TENANT_ID
python3 -m venv .venv-eval && .venv-eval/bin/pip install microsoft-agents-copilotstudio-client msal
# put EVAL_CLIENT_ID, EVAL_TENANT_ID, AGENT_ENVIRONMENT_ID, AGENT_SCHEMA_NAME in .env
bash scripts/provision.sh --with-corpus   # protected mode only: also puts the sample corpus (flat) in the main site
export AGENT_TOKEN_CACHE=.build/token_cache_eval.json     # one sign-in per test user
.venv-eval/bin/python scripts/run_evals.py          # device-code sign-in on first use; exits 1 on a failed gate
```

Thresholds match the Azure template where a counterpart exists: keyword recall ≥ 0.65, citation recall
≥ 0.60, p95 ≤ 45 s, memory recall = 1.0, and any failed call or empty answer fails the run. The caller must
be a **real user with a SharePoint-bearing license**, who is a member of the knowledge site and has the
agent shared with them (**Can use**): the agent retrieves SharePoint as the signed-in user, an app-only
caller has none, and for an unlicensed user every question comes back "not found".

**Result** (Jane, licensed; dev agent; sample corpus in the main site):

| Metric | Result | Gate |
|---|---|---|
| Keyword recall | **0.986** (11 of 12 cases 1.00; one 0.83) | ≥ 0.65, passes |
| Citation recall | **1.000** (the right file named in every reply) | ≥ 0.60, passes |
| p95 latency | **25.7 s** | ≤ 45 s, passes |
| Memory recall | **1.00** | = 1.0, passes |
| Failed calls | 0 | 0, passes |

**How it got here (so you do not repeat it).** The first run, against the e2e site, scored 0.06 and 0.0.
The cause was not the agent being wrong. Two checks had been misleading:

- A browser search run as the unlicensed site owner returned nothing, which looked like "the site is not
  indexed". Run as a licensed user, SharePoint search found the documents in both corpus sites within
  hours. Always check as a licensed user.
- The agent itself still failed on the two brand-new corpus sites for hours (errors, then "no results"),
  for Jane and for John, while the same agent answered correctly from the two-day-old dev site. A new file
  added to an established site was retrievable **about 10 minutes** after upload. So the agent's search
  appears to take a long time to start covering a **brand-new site** (not confirmed, not a Microsoft
  statement), while new files on a covered site are quick. The corpus therefore runs in the established dev
  site, uploaded flat (`--with-corpus`); sub-folders were not re-tested after the site became usable.

**LLM-judged metrics** (relevance, groundedness) come from Copilot Studio's built-in
[Agent Evaluation](https://learn.microsoft.com/microsoft-copilot-studio/analytics-agent-evaluation-create).
Export the cases for it:

```bash
python3 scripts/export_eval_csv.py      # writes .build/eval_cases.csv
```

Import it under **Evaluation → New evaluation → Import**. The file has two columns (`Question`,
`Expected response`); the test method is chosen in the UI afterwards. The portal's **Evaluate** tab exists
for harness agents; running the built-in graders against this agent was not tried. How each Azure metric
maps:

| Azure metric | Copilot Studio | Status |
|---|---|---|
| Faithfulness (GPT-5 judge) | Built-in General quality (groundedness) | Not run |
| Answer relevance (GPT-5 judge) | Built-in General quality (relevance) | Not run |
| Keyword recall | `scripts/run_evals.py` (also built-in Keyword match) | **0.986** |
| Citation recall | `scripts/run_evals.py`; citations appear as file names in the reply payload | **1.000** |
| p95 latency | `scripts/run_evals.py` | **25.7 s** |
| HHEM hallucination score | Not available | Needs a custom runner; not built |
| Memory recall | `scripts/run_evals.py` | **1.00** |

The built-in Evaluation REST API (`api.powerplatform.com/copilotstudio/.../makerevaluation/testsets/{id}/run`)
needs a **user** access token too, and its documentation does not name the scope or say whether the
draft or published agent is evaluated, so triggering it from a script is not implemented.

## Memory

**Status: built and verified.** Turned on in code (`enableMemory: true` in
[`agent/settings.mcs.yml`](agent/settings.mcs.yml)) and exercised against a real tenant.

Copilot Studio's [Memory (preview)](https://learn.microsoft.com/en-us/microsoft-copilot-studio/agents-experience/memory-overview)
works on this agent type: the agent itself decides what to keep (preferences, feedback, ongoing work),
stored per user and per agent in Microsoft-managed storage, and it consults that memory when a
conversation starts. Memories are deleted after 28 days of inactivity, cannot be exported, an admin can
only view a user's memories through a support ticket, and turning the toggle off does not delete them
immediately. It is usage-billed.

**Verified.**

- Told a preference in one conversation ("my favorite color is teal"), the agent recalled it in a
  brand-new conversation (different conversation ID) after about 30 seconds.
- The two-turn eval case passes as a licensed user: second shift and Line 2 were stored in one
  conversation and returned in another (`memory_recall = 1.00`). Each eval turn is its own conversation,
  so only memory can supply the answer.
- **Not guaranteed to be applied.** The agent acknowledged a "two sentences" formatting preference and
  then answered in one sentence. Memory is the model's judgement, not a rule; documents supply the
  answers and memory supplies preferences only.

**Why it was not here before.** The first version of this repo was a classic, topic-based agent, which has
no Memory option. Memory belongs to the harness agent type, so the agent was rebuilt as one
(`authoringModel: CliCopilot`). That changed how knowledge is searched and how telemetry works; see
Observability.

| Azure template | Here |
|---|---|
| Foundry Memory Store, scoped per user, written in the background | Copilot Studio Memory, scoped per user and per agent, written by the agent |
| Memory is not the source of truth: documents supply answers, memory only preferences | The same, set in the agent's instructions |
| Two-turn recall eval | `tests/eval_cases.json` case `memory-preference-recall`, run by `scripts/run_evals.py` |
| Delete a user's memory | Not available to admins; support ticket only |

A memory eval case **writes into the signed-in user's memory**, and that persists, so use a dedicated
test user.

## Observability

**Status: the previous per-agent telemetry does not apply to this agent type. Microsoft documents an
environment-level telemetry preview that may cover it; it is not set up and untested here.**

On the earlier classic agent, a topic emitted a `KnowledgeSearch` custom event to Application Insights
(question length, answer length, search latency, answered or not; verified end to end, including a privacy
test that showed the platform's own events log question text, reply text and user identity unless
**Log conversation details** is turned off). A harness agent has no topics, and checked in the portal:

- **No per-agent Application Insights setting.** Settings > Advanced offers only solutions and a channel-name suffix. (An environment-level option exists; see below.)
- **No Dataverse transcripts.** Harness conversations did not appear in the `conversationtranscripts` table
  (queried directly after several conversations).
- **What is visible.** The test pane shows each tool call (including failures such as the search error
  described in Troubleshooting) and a per-conversation credit estimate (about 4 Copilot Credits for a
  question that searches SharePoint); the admin center shows credit usage per agent once billed usage is
  reported. The portal's **Monitor** tab exists; it was not evaluated.

**Environment-level telemetry (preview), documented but not tried.** Microsoft's
[environment-level telemetry](https://learn.microsoft.com/en-us/microsoft-copilot-studio/advanced-environment-level-agent-telemetry)
exports every agent in an environment to Application Insights, configured by an administrator in the
Power Platform admin center (an export package of type Copilot Studio), not on the agent. From the pages:

- **Coverage is unclear for this agent.** Both pages say it covers the standard and the Microsoft Copilot
  harness, and one note on the same page says only standard-harness agents built in Copilot Studio appear.
  Neither page mentions CLI-created agents (`pac copilot init --authoring-mode cli-copilot`), so whether this
  agent emits anything has to be tested.
- **Prerequisites.** A **managed environment** (the dev and e2e environments here are plain Developer
  environments), tenant-level Power Platform or Dynamics 365 administrator plus an environment admin role, an
  Azure Application Insights resource with *Local authentication* on, and the public cloud. Delivery of a new
  configuration can take up to 24 hours. Licensing and cost are not stated; scripting is not documented.
- **What it records.** OpenTelemetry-style spans in `dependencies` (`InvokeAgent`, `ExecuteTool`,
  `OutputMessages`) with the conversation ID, model, tool name, and, importantly, **the user's prompt, the
  agent's reply, tool arguments and tool results, and the user's id, email and name**. No setting to turn
  the content or identity off is documented. For a protected-documents agent that is the same exposure as
  the classic agent's default (an answer withheld from one user by a label would sit in a workspace the
  label does not govern, and tool results would carry document text), so it should not be enabled on the
  protected-mode environment, and the workspace needs restricted read access.
- **Limitations listed on the page:** preview, possible small data loss, truncation of large content, no
  duration for some traces, no topic events.

Trying it would mean enabling Managed Environments on a test environment holding only the non-sensitive
sample corpus and creating the export in the admin center, then checking whether any `dependencies` rows
appear for this agent.

`scripts/provision_observability.sh` (Log Analytics workspace with a daily ingestion cap, workspace-based
Application Insights) still works and is tested, but there is nothing to connect to it for this agent, so
it is not part of the provisioning path. Teardown removes it with `--delete-observability`.

| Azure template | Here | Status |
|---|---|---|
| Log Analytics `AgentTelemetry_CL` via a data collection endpoint | Environment-level export to Application Insights (`dependencies` spans): documented, preview, may not cover this agent type | Untested |
| Per-request retrieval score, reranker score, chunk count | Not exposed | Cannot be reproduced |
| `IsolationForest` over six retrieval features | Needs a telemetry feed; only possible if the export above covers this agent | Not built |
| HHEM faithfulness score in evals | Needs answer plus retrieved text; custom runner | Not built |
| Weekly retrain workflow | | Not built |

What can be measured today comes from the eval runner (latency, recall) and billing data. In the test tenant
a pay-as-you-go plan is linked to the environments and an Azure budget alert watches the spend; both were
set up by hand and are not scripted.

## CI/CD

**Status: `ci.yml`, `deploy.yml` and `eval.yml` are all verified on GitHub against the harness agent. `eval.yml` runs as a licensed test user from a stored sign-in cache.**

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` | push to `main`, pull requests | Unit tests and shell syntax checks. No tenant access, no secrets |
| `deploy.yml` | push to `main` touching `agent/`, or manual | Signs in to Power Platform with **GitHub OIDC** (`pac auth create --githubFederated`) and runs `provision.sh --skip-sharepoint` to push and publish the agent |
| `eval.yml` | manual | Runs `scripts/run_evals.py` and uploads the results; see the constraint below |

One-time setup, all scriptable except the last two:

```bash
bash scripts/setup_entra_app.sh --github-repo owner/repo --github-environment dev   # federated credential
pac admin assign-user --environment <url> --user <app-client-id> --application-user --role "System Customizer"   # see the role note below
gh api -X PUT repos/owner/repo/environments/dev                                      # the environment name is part of the credential's subject
gh variable set DATAVERSE_ENV_URL --env dev --body <url>                             # plus EVAL_CLIENT_ID, EVAL_TENANT_ID, SP_HOST, KB_ACCESS_MODE, KB_SITE_NICK, RESTRICTED_SITE_NICK
```

No secret is stored for deploys. A production environment was deferred.

**Verified runs.** `deploy.yml` signs in with OIDC as the app, clones the live agent into a fresh workspace,
applies `agent/`, pushes and publishes; a run with nothing changed reports "No local changes detected" and
still publishes. The federated credential and the role were each wrong once before it worked (details
below). **A publish quirk:** `pac` 2.16.1 sometimes fails after the publish has already succeeded, with
`System.ArgumentException: Invalid response format` from its status poller. `provision.sh` handles exactly
that error by reading the bot's `synchronizationstatus` (`pac org fetch`) and passing only if it shows a
publish that succeeded since the call began; every other error still fails the deploy, and the workflow
prints `pac`'s own log when a deploy fails.

**Role.** Pushing an agent that another user owns needs organization-level access to the bot and bot
component tables. In the test environment, `Bot Author`, `Bot Contributor`, `Bot Viewer` and
`Environment Maker` are all **user-level** (the app sees only records it owns, so `pac copilot list`
returned nothing for it), while `System Customizer` is **organization-level** for read, write and
create. `System Customizer` is the narrowest standard role that works; the app holds it (plus `Bot Author`, which became redundant). `deploy.yml` runs
`provision.sh --no-create`, so an agent the identity cannot see is a hard failure and never a re-import
of the template.

**Federated credential subject.** GitHub puts immutable numeric IDs into its OIDC subject for newer
repositories (`repo:owner@<id>/name@<id>:environment:dev`). A name-based credential never matches and
fails with `AADSTS700213`. `setup_entra_app.sh` looks the IDs up with `gh` and replaces a stale credential.

One constraint shapes the design: a federated GitHub identity is an **app**, and the Power Platform API
offers an application permission (`CopilotStudio.Copilots.Invoke`), but this agent retrieves SharePoint
as the **signed-in user**. An app-only call has no user for SharePoint to trim by, so I expect its
retrieval to come back empty (not tested). Deploying with `pac` can run from CI under the federated
identity; evals need a user who holds a SharePoint-bearing license, so they run on demand from a signed-in
session unless a dedicated test user's refresh token is stored as a secret, which breaks on conditional
access and idle expiry. `eval.yml` runs as that user from the `EVAL_TOKEN_CACHE` secret (Jane's sign-in cache in this test tenant)
and the variables `AGENT_ENVIRONMENT_ID` / `AGENT_SCHEMA_NAME`, which point at the dev agent whose site holds
the sample corpus. They are set, and the workflow has passed once (see Verification).

## Configuration

Set in `.env` (see `.env.example`).

| Variable | Description |
|---|---|
| `SP_HOST` | **Required.** Your SharePoint hostname, e.g. `contoso.sharepoint.com` |
| `KB_ACCESS_MODE` | `sample` (default) or `protected` |
| `ALLOWED_USER` / `DENIED_USER` | Required in protected mode: the two test users |
| `KB_MEMBERS` | Sample mode: extra members of the main site, comma separated |
| `ENV_NAME` / `ENV_REGION` | Environment name (default `copilot-agent-dev`) and macro-region (default `north-america`) |
| `AGENT_NAME` / `PUBLISHER_PREFIX` | Agent display name (default `OL Memory Agent`) and publisher prefix (default `olk`). Together they must give the schema name in `agent/settings.mcs.yml` (`olk_OLMemoryAgent`); the script refuses to apply the definition to a different agent |
| `KB_SITE_NICK` / `KB_SITE_TITLE` | Main site (default `ol-knowledge`) |
| `RESTRICTED_SITE_NICK` / `RESTRICTED_SITE_TITLE` | Restricted site in protected mode (default `ol-restricted`) |
| `EVAL_CLIENT_ID` / `EVAL_TENANT_ID` | Evals: the app registration from `scripts/setup_entra_app.sh` and its tenant |
| `AGENT_ENVIRONMENT_ID` / `AGENT_SCHEMA_NAME` | Evals: the agent to call (the environment ID is in the portal URL; the schema name is `olk_OLMemoryAgent`) |
| `AGENT_TOKEN_CACHE` | Evals: the sign-in cache file (default `.build/token_cache.json`). Use a different file per test user |
| `OBS_RESOURCE_GROUP` / `OBS_LOCATION` | Monitoring resource group and region (defaults `rg-copilot-agent-obs`, `eastus2`) |
| `OBS_WORKSPACE` / `OBS_APPINSIGHTS` | Log Analytics workspace and Application Insights names (defaults `log-copilot-agent`, `appi-copilot-agent`) |
| `OBS_DAILY_CAP_GB` / `OBS_RETENTION_DAYS` | Daily ingestion cap (default 1) and retention (default 30) |

## Verification so far

| What | Result |
|---|---|
| Unit tests | 96 pass (`pytest`): corpus and `.docx` conversion, agent-definition rendering, SharePoint/Graph client with a stubbed transport, shell helpers, provisioning stage with stub tools, eval cases and CSV export, eval and memory metrics and gating |
| `provision.sh` on the harness agent | Real runs against two environments: the dev agent (clone, apply definition, push, publish) and the e2e agent; a second run pushed nothing, and the portal showed the instructions, the knowledge source and Memory exactly as in `agent/`. The create path (`pac copilot init`, about 3 minutes) was run by hand to make the first dev agent; the script's create branch is covered by dry-run tests only |
| Memory | Recalled in a new conversation after about 30 s; eval memory case 1.00 as a licensed user (see Memory) |
| Access restriction | Two users side by side on the harness agent, at site and document level; see [guides/purview.md](guides/purview.md) |
| Licensing | Same agent, same question: answered for a SharePoint-licensed user, "not found" for an unlicensed site owner, until that owner was licensed. Root cause of days of failed probes |
| Eval runner | Scored run as a licensed user: keyword 0.986, citation 1.000, p95 25.7 s, memory 1.00; all gates pass (see Evaluations) |
| `setup_entra_app.sh` | Created the app registration (single tenant, public client, no secret) and granted consent; the delegated grants were read back from the directory |
| GitHub Actions | `ci.yml` green. `deploy.yml` ran on the harness agent as the app identity: OIDC sign-in, clone, apply, "No local changes detected" (idempotent), publish. `pac` 2.16.1 failed twice while parsing the publish status ("Invalid response format") although the bot record showed the publish had succeeded, then passed on a third run; a verification step handles that error (unit-tested with fake commands, not yet hit live). The step that prints `pac`'s own log on failure found it |
| SharePoint pipeline | Site create (idempotent), upload with subfolders, delete with purge, exercised for real |
| Telemetry | No per-agent telemetry; environment-level preview documented, not tried (see Observability) |
| `eval.yml` | Ran on GitHub as the licensed test user from the stored sign-in cache: keyword recall 0.949, citation recall 1.000, p95 30.3 s, memory recall 1.00; passed. Run-to-run keyword recall varied (0.986 locally, 0.949 in CI) because the answers are model-generated |
| New-site behavior | Two sites created hours earlier gave the agent search errors for two users while SharePoint search showed their documents; a new file in a two-day-old site was retrievable in about 10 minutes. Cause not confirmed |

## Troubleshooting

| Symptom | Fix |
|---|---|
| `pac: command not found`, or "You must install .NET" | Add `~/.dotnet/tools` to `PATH` and set `DOTNET_ROOT` (Homebrew: `/opt/homebrew/opt/dotnet/libexec`); the scripts do this when the Homebrew path exists |
| `pac auth` fails with "refresh token has expired" | Idle 90 days. Delete the profile and run `pac auth create --deviceCode` again |
| `pac admin create` rejects `unitedstates` | Pass `--macro-region north-america` (or `europe-uk`, `asia-pacific`); the CLI default is invalid. The scripts do |
| **"I couldn't find that" for every question, and the test pane's trace shows `sharepoint_lexical_search` / `sharepoint_semantic_search` failing with "Knowledge search · unknown"** | **The signed-in user has no SharePoint-bearing license** (for example Business Premium). The search tools run as that user and fail; the agent then reports "not found", which hides the failure. An unlicensed **site owner** can still open files and fetch them by name, so it looks like a missing document. Assign a license, wait a while for it to reach search, and test as a licensed user. Microsoft publishes nothing on this for harness agents; it was established by testing the same agent as a licensed and an unlicensed user |
| Search errors or "no results" for a licensed user on a **brand-new site**, while SharePoint's own search (run as a licensed user, not the owner) shows the documents | The agent's search was slow to cover a new site: two sites stayed unusable for hours for two users, while a new file added to a two-day-old site was usable in about 10 minutes. Use an established site, or wait; the cause is not confirmed. Do not conclude "not indexed" from a search run as an unlicensed user |
| The Agents SDK returns `404 Error discovering conversation endpoints` | The agent is not shared with the signed-in user: Copilot Studio → Share → **Can use**. It is not a denial from the platform |
| `pac copilot publish` fails "A required argument --bot is missing" | Pass the agent: `pac copilot publish --bot <schema or id>`. The script does |
| `pac` fails "You must install .NET" after a Homebrew upgrade | `pac` 2.16 targets .NET 10; with only .NET 11 installed set `DOTNET_ROLL_FORWARD=Major` and `DOTNET_ROOT=/opt/homebrew/opt/dotnet/libexec` |
| Re-provisioning fails because the site name exists | A deleted group is soft-deleted for 30 days. Use `teardown.sh`, which purges, or purge it in Entra |
| Denied user can open or retrieve a labeled file | The file was labeled before SharePoint could process it. Label a never-labeled file inside SharePoint ([guide](guides/purview.md)) |
| No Sensitivity button in Word for the web | Use the account the label is published to, a normal (not incognito) window, Editing mode; see the guide |
| Deploy fails with `AADSTS700213: No matching federated identity record` | The credential's subject lacks GitHub's immutable owner/repo IDs. Re-run `scripts/setup_entra_app.sh --github-repo owner/repo --github-environment dev` |
| Deploy says the agent was not found (`--no-create`) | The deploying app cannot read it, usually a user-level role; see the role note under CI/CD |
| SharePoint REST or file download returns 401 | Expected with an Azure CLI token. The scripts use Microsoft Graph |
| `GID: bad math expression` in zsh | `GID` is read-only in zsh; use another variable name |

## Comparison across clouds

|  | [Azure](https://github.com/Metafiziks/azd-foundry-search-agent) | **Copilot Studio (this repo)** | [AWS](https://github.com/Metafiziks/aws-bedrock-agent) | [GCP](https://github.com/Metafiziks/gcp-search-agent) |
|---|---|---|---|---|
| Provision | `azd up` | `bash scripts/provision.sh` | `bash scripts/provision.sh` | `bash scripts/provision.sh` |
| LLM | GPT-5 (Foundry) | Claude Sonnet 4.6, set in `agent/settings.mcs.yml` | Amazon Nova Lite | Gemini 3.5 Flash |
| Agent runtime | Foundry hosted agent (Python) | Copilot Studio harness agent, defined as YAML via `pac` | Bedrock Agents | Google ADK + Cloud Run |
| RAG | Azure AI Search | SharePoint knowledge (Microsoft Search) | Bedrock Knowledge Bases | Vertex AI Search |
| Vector store | AI Search (built in) | Managed by the platform | OpenSearch Serverless | Vertex AI Search (built in) |
| CI auth | Azure OIDC | Entra federated credential (GitHub OIDC; deploy verified) | GitHub OIDC | Workload Identity Federation |
| Eval judge | GPT-5 + HHEM | Deterministic metrics in `scripts/run_evals.py`; built-in graders not run | Amazon Nova Pro | Gemini 3.5 Flash |
| Memory | Foundry Memory Store | Copilot Studio Memory (`enableMemory`), per user; verified | Bedrock session-summary memory | ADK session/user state |
| Protected mode | `KB_ACCESS_MODE=purview` | `KB_ACCESS_MODE=protected` | `KB_ACCESS_MODE=protected` | `KB_ACCESS_MODE=protected` |
| Protection mechanism | Purview labels via Foundry IQ | SharePoint permissions + encrypted Purview labels | Knowledge Base document ACLs | Agent Search document ACLs |
| Who proves the user | The user's delegated token, checked by AI Search | The user's Entra sign-in, checked by the platform | Your backend (API Gateway JWT authorizer) | The user's Google token, checked by Agent Search |
| Teardown | `azd down` | `bash scripts/teardown.sh` | `bash scripts/teardown.sh` | `bash scripts/teardown.sh` |
