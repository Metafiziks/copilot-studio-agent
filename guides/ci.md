# CI/CD in detail

The README lists the workflows and the one-time setup. This page has what it took to get them working.

**Verified runs.** `deploy.yml` signs in with OIDC as the app, clones the live agent into a fresh workspace,
applies `agent/`, pushes and publishes; a run with nothing changed reports "No local changes detected" and
still publishes. The federated credential and the role were each wrong once before it worked (details below). **A publish quirk:** `pac` 2.16.1 sometimes fails after the publish has already succeeded, with
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
and `AGENT_ENVIRONMENT_ID` (secret) / `AGENT_SCHEMA_NAME` (variable), which point at the dev agent whose site holds
the sample corpus. They are set, and the workflow passes with HHEM and the LLM judge (see [evaluation](evaluation.md)).

## Other things checked from the tenant side

| What | Result |
|---|---|
| e2e eval | The scripted eval also passes on the e2e agent against the sample corpus in its own new site `ol-eval-corpus` (keyword recall 0.965, citation 1.000, p95 34.6 s, memory 1.00), which failed with `NotFound in FederationWorkflow` about 12 hours earlier. The flat upload into an established site (`--with-corpus`) is therefore a precaution, not a requirement |
| Portal Monitor tab | Works for the harness agent, fed by the conversation transcripts: sessions, daily users, duration, success rate, with a delay of hours. Its 0% success and "no tool use" mirrored the failed searches, and it had not yet shown the newest sessions |
| Credit limits | A per-agent cap with *Stop usage* at 100% and an 80% notification on both `OL Memory Agent` entries (dev 2,000 after it hit 1,000; e2e 1,000), set in the admin center under Licensing > Copilot Studio > Manage Agents. The agent only appeared there after it had billed usage (631 and 398 credits by then). At about 4 credits per turn, a daily CI eval of 13 turns is roughly 50 credits, so budget the cap |
| Purview audit | Not evaluated. The Audit page reported it could not confirm that activity is being recorded and offered to start recording, a tenant-wide compliance setting that was left alone |
