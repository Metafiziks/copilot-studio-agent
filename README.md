# Copilot Studio agent with permission-aware SharePoint retrieval

A Copilot Studio agent, defined as code, whose knowledge lives in SharePoint and whose answers
respect **who is asking**. It is the Copilot Studio counterpart to the Purview-protected mode of
[azd-foundry-search-agent](https://github.com/Metafiziks/azd-foundry-search-agent).

## What is demonstrated

Two users, **Jane** (allowed) and **John** (denied), ask the same agent the same question in the
Copilot Studio test pane. Each level of restriction has its own fixture:

| Level | How access is restricted | Jane | John |
|---|---|---|---|
| **Site** | A private SharePoint site whose only member is Jane | gets the code | cannot open the site; the agent returns nothing |
| **Document** | A sensitivity label with encryption (VIEW, EXTRACT for Jane only) on one file in a library both users can open | gets the code | cannot open the file; the agent returns nothing |

Details, fixtures and the exact results: [docs/access-restriction-test.md](docs/access-restriction-test.md).

## What is not demonstrated

- **End-user channels.** Everything was tested in the Copilot Studio test pane. Teams, Microsoft 365
  Copilot or the demo website were not tried.
- **A deployment pipeline.** There is no CI/CD. `pac copilot pack` cannot carry knowledge sources
  (see [docs/setup.md](docs/setup.md)), and a production target environment was deliberately deferred.
- **Statistical confidence.** One clean trial per user per level.
- **Answer faithfulness.** The agent sometimes names the wrong file in its sentence while citing the
  right one. This is a quality issue, separate from access control.

## Layout

```
agent/                      The agent as code (pac copilot workspace)
  agent.mcs.yml             Instructions and capabilities (web browsing off)
  settings.mcs.yml          Entra auth (Integrated, Always); model knowledge off
  knowledge/                One SharePointSearchSource per SharePoint site
  topics/                   Default topics from the template
tests/make_fixtures.py      Generates the synthetic .docx fixtures
docs/setup.md               Tooling, environments, sync commands, sharing
docs/access-restriction-test.md   Test procedure, results, lessons
```

`agent/.mcs/` (the environment binding created by `pac copilot clone`) ignores itself and is not
committed. The knowledge files contain your tenant's SharePoint URLs, so review them before
publishing this repository.

## Quick start

See [docs/setup.md](docs/setup.md). In short: install `pac`, sign in, create a Developer
environment, push the `agent/` workspace, add the SharePoint sites as knowledge, share the agent,
and run the two-user test.
