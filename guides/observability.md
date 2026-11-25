# Observability in detail

The earlier classic agent emitted a `KnowledgeSearch` custom event from a topic. A harness agent has no
topics and no per-agent Application Insights setting (Settings > Advanced offers only solutions and a
channel-name suffix), so the two sources below replace it.

**1. Environment-level export to Application Insights (preview).** Configured by an administrator in the
Power Platform admin center (Manage > Data export > App Insights > New data export, data type *Copilot
Studio*), not on the agent. Microsoft's
[page](https://learn.microsoft.com/en-us/microsoft-copilot-studio/advanced-environment-level-agent-telemetry)
names the standard and Microsoft Copilot harnesses and never mentions agents created with
`pac copilot init --authoring-mode cli-copilot`; **tested here, it covers them.** Prerequisites: a managed
environment, Application Insights with *Local authentication* on, the public cloud.

A marked question to the harness agent produced, within about a minute, rows in `dependencies`:

| Span | What it carries |
|---|---|
| `InvokeAgent` (root) | the user's prompt (`gen_ai.input.messages`), the reply (`gen_ai.output.messages`), model (`Sonnet46`), duration, conversation and session IDs, channel, **user email, id and name**, client IP |
| `ExecuteTool` (one per retrieved document) | tool `SharePoint` (type `Knowledge`), `gen_ai.tool.call.arguments` and **`gen_ai.tool.call.result`** (document URI and content), same tool-call ID |
| `OutputMessages` | the final message (classic agents) |

```kusto
dependencies
| where name in ("InvokeAgent", "ExecuteTool", "OutputMessages") and timestamp > ago(1d)
| extend agent = tostring(customDimensions["gen_ai.agent.name"]),
         conv = tostring(customDimensions["gen_ai.conversation.id"])
| project timestamp, name, agent, conv, success, duration, customDimensions
| order by timestamp desc
```

Things to know:

- **Activation takes hours.** Conversations in the 40 minutes after the export was created (05:55 to 06:35
  UTC) produced nothing; the next conversation, about 12.5 hours later, was delivered in under a minute.
  The page says up to 24 hours for a new configuration. **An early "no rows" proves nothing**, and an empty
  table with no conversations in the window proves even less: send a marked question, then look.
- **The spans carry document text and the user's identity.** No setting to turn that off is documented. For
  a protected-documents agent that is the same exposure as the classic agent's default, so do not enable
  it on the protected-mode environment, and restrict read access to the workspace. Tool results contain
  what the user was allowed to retrieve, and the workspace is not governed by the labels.
- **Managed Environment licensing.** The environment must be managed, which carries a premium-license
  requirement for its users that this tenant does not hold: fine for a test environment, a decision for
  production.
- The destination was verified independently by posting a synthetic span to the ingestion endpoint (seen in
  `dependencies` within a minute), so an empty table means nothing is being sent, not that the resource is
  misconfigured. Avoid pointing both agent-level and environment-level telemetry at one Application
  Insights resource (the earlier classic agent did, which is why `customEvents` also filled up; it has been deleted).
- Preview limits from the page: possible small data loss, truncation of long content, no `duration` for some
  traces, no topic events.

**2. Dataverse conversation transcripts.** Harness conversations *do* land in `conversationtranscripts`
(an earlier version of this README said they did not; that check was too early, since rows appear with a
delay of hours). Each transcript holds the messages, the model's reasoning text (`ThinkingTrace`) and a
`ToolCallTrace` per tool call with its status, **including the exact error**: for the failing searches,
`sharepoint_semantic_search failed: NotFound in FederationWorkflow`. This is where that error was found.
Query it with the Dataverse Web API (`/api/data/v9.2/conversationtranscripts`, `content` is JSON); the bot
is `_bot_conversationtranscriptid_value`.

Also visible: the test pane shows each tool call and a credit estimate (about 4 Copilot Credits for a
question that searches SharePoint); the admin center shows credit usage per agent once billed usage is
reported.

`scripts/provision_observability.sh` creates the Log Analytics workspace (daily ingestion cap) and the
workspace-based Application Insights the export points at. Its printed "connect the agent" step applies to
classic agents only; for this agent create the export in the admin center. Teardown removes the resources
with `--delete-observability`; remove the export package and Managed Environments setting separately.

## Compared with the Azure template

| Azure template | Here | Status |
|---|---|---|
| Log Analytics `AgentTelemetry_CL` via a data collection endpoint | Environment-level export to Application Insights (`dependencies` spans): Environment-level export, preview; verified on this agent | Working (activation took hours) |
| Per-request retrieval score, reranker score, chunk count | Not exposed; the tool result lists the retrieved documents, with no scores | Partly reproducible (documents, no scores) |
| `IsolationForest` over six retrieval features | The feed now exists (spans per question and retrieved document); features are limited to counts, latency, success | Not built |
| HHEM faithfulness score in evals | `scripts/hhem.py` in the eval runner (see [evaluation](evaluation.md)); scoring live traffic from the spans is not built | Evals: running. Live traffic: not built |
| Weekly retrain workflow | | Not built |

What can be measured today comes from the eval runner (latency, recall) and billing data. In the test tenant
a pay-as-you-go plan is linked to the environments and an Azure budget alert watches the spend; both were
set up by hand and are not scripted.
