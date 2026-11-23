# Protect documents with sensitivity labels and site permissions

Set `KB_ACCESS_MODE=protected` to make the agent return only what the **signed-in user** may see.
The agent (a harness-type Copilot Studio agent) retrieves SharePoint content through its search and
file tools under the **signed-in user's own Entra identity**, so two things restrict what an answer can
draw on:

| Level | Mechanism | What you configure |
|---|---|---|
| **Site** | SharePoint permissions | A private site whose only member is the allowed user |
| **Document** | A Microsoft Purview sensitivity label **with encryption** | A label granting VIEW and EXTRACT to the allowed user only, applied inside SharePoint |

Unlike the Azure template's protected mode, there is no separate protected index and no token you
forward: the platform trims per user. That also means there is no "require a valid label on every
reference" rule; an unlabeled document is returned to anyone who can open it.

## 1. Prerequisites

- Microsoft 365 with SharePoint Online and a license that includes rights management (the test
  tenant used Business Premium: `RMS_S_ENTERPRISE`, `RMS_S_PREMIUM`).
- A sensitivity label, scoped to **files**, that applies **encryption** granting VIEW and EXTRACT to
  the allowed user only, with content expiry `Never` and no Double Key Encryption (SharePoint cannot
  process those). Verify what it actually enforces with Purview PowerShell, not the portal
  description:

  ```powershell
  Connect-IPPSSession -UserPrincipalName <admin>        # interactive sign-in; -Device is not supported
  (Get-Label -Identity "<label>").LabelActions            # look for Type=encrypt and the rightsdefinitions entry
  ```

- The label **published** to the user who will apply it (a label policy). In the test tenant only the
  allowed user could see the label in Office, so that user must do step 3.
- Two test users, **each holding a SharePoint-bearing license** (guests do not work for SharePoint answers
  under SSO). The license is not optional: for an unlicensed user the agent's SharePoint search tools fail
  ("Knowledge search · unknown") and it answers "I couldn't find that", even on a site the user owns. A
  licensed user and an unlicensed one look identical from the outside except for that, so check licenses
  first when answers are empty.

## 2. Provision in protected mode

```bash
cp .env.example .env     # set SP_HOST, KB_ACCESS_MODE=protected, ALLOWED_USER, DENIED_USER
bash scripts/provision.sh
```

This creates two private sites and uploads only the synthetic fixtures (never the sample corpus):

| File | Site | Members | Code |
|---|---|---|---|
| `control.docx` | main knowledge site | allowed + denied user | MAPLE-82 |
| `restricted.docx` | main knowledge site | allowed + denied user | HELIOS-31 |
| `site-restricted.docx` | restricted site | allowed user only | KESTREL-19 |

Then share the agent (Copilot Studio → Share → **Can use**) with both users. Without it the Agents SDK
gets `404 Error discovering conversation endpoints` for that user, which looks like a denial and is not
one. The checks below call the agent through the SDK, so no Editor access is needed.

## 3. Apply the label inside SharePoint

Sign in as the user the label is published to, in a **normal (not incognito) browser window**, open
`restricted.docx` from the main site's library in **Word for the web** in **Editing** mode, and use
the **Sensitivity** button on the Home tab (under "…" on a narrow window). Close the document.

Check from SharePoint's side that the change was saved: the file's version history should show new
versions by that user. Then confirm the protection itself: the **denied** user opening the file must
see "The document is protected by a rights management service."

> **Label the file inside SharePoint, on a file that was never labeled.** A file labeled elsewhere
> before upload carries the label metadata but is not protected, and Microsoft documents that
> SharePoint does not recognize labels applied before it could process them. In testing the
> denied user opened such a file freely and the agent returned its content to him.

## 4. Wait for indexing

A new site and a newly protected file take time to reach SharePoint search, and the agent can only
find what search has. Allow roughly 20 to 30 minutes. SharePoint's own **Search this site** is a cheap
check, and an empty agent answer before it finds the file proves nothing.

## 5. Verify

Call the published agent as each user through the Agents SDK. Each user signs in once with a device
code; `AGENT_TOKEN_CACHE` keeps one sign-in per user, and every call starts a new conversation:

```bash
export AGENT_ENVIRONMENT_ID=<environment id>  AGENT_SCHEMA_NAME=olk_OLMemoryAgent
export AGENT_TOKEN_CACHE=.build/token_cache_jane.json        # then .build/token_cache_john.json
.venv-eval/bin/python scripts/agent_client.py probe "What is the unlabeled control code?"
```

Wording matters: "site-restricted test code" also matched `restricted.docx`, so use the questions below.

| Question | Allowed user | Denied user |
|---|---|---|
| Open the restricted site | opens | "You need access" |
| Open `restricted.docx` | opens | "protected by a rights management service" |
| "What is the KESTREL code?" | KESTREL-19, cites `site-restricted.docx` | not found |
| "What does the OL Restricted Test Document say?" | HELIOS-31, cites `restricted.docx` | not found |
| "What is the unlabeled control code?" | MAPLE-82 | MAPLE-82 |

### Results (test tenant, November 2026, harness agent, called through the SDK)

| Question | Jane (allowed) | John (denied) |
|---|---|---|
| Control code | `MAPLE-82`, cites `control.docx` | `MAPLE-82`, cites `control.docx` |
| Restricted document | `HELIOS-31`, cites `restricted.docx` | "I wasn't able to locate" it; only unrelated files returned |
| KESTREL code | `KESTREL-19`, cites `site-restricted.docx` | not found; the reply cites only `control.docx` and says it has no KESTREL information |

One trial per user per question. This verifies query-time trimming for the signed-in user on the
harness agent, for both the site level (KESTREL) and the document level (HELIOS). It was not tested
through Teams or Microsoft 365 Copilot. The same checks passed earlier in the test pane on the
classic, topic-based version of this agent.

## Things that cost time

- **Empty answers for a user who can open the files.** The agent's SharePoint search needs the signed-in
  user to hold a SharePoint-bearing license. The first test user here was an unlicensed site owner:
  files opened fine, but every question was "not found", for days. Jane and John (licensed) worked at once.
- **The label definition was never the problem.** It encrypted correctly from the start; the file
  was the problem (see step 3). Neither licenses nor the Purview "turn on labels for SharePoint"
  banner distinguished the cause.
- **No Sensitivity button.** The button follows the account used to open the file and needs the
  label published to that account; cross-site cookies and incognito windows also interfere. A file
  the user can only read, or a file type SharePoint does not support, hides it too.
- **Graph `assignSensitivityLabel` is a metered API** and returned 403, so labels are applied in the
  browser. SharePoint REST and file downloads returned 401 for an Azure CLI token.
- **Empty answers right after provisioning** are almost always indexing, not access control.

## Limits

- The agent can name the wrong file in its sentence while citing the right one (it credited a code
  to the wrong document once). That is a quality issue, not an access issue.
- Whether the tenant-wide SharePoint setting for labels was explicitly enabled was not confirmed;
  the outcome shows labeling and enforcement work for newly labeled files.
- A user in the denied group still receives anything in documents they may open. Unlabeled files
  are not blocked by this mode.
- **Telemetry can copy what the labels withheld.** On the earlier classic agent, connecting Application
  Insights logged the user's question, the agent's reply and the user's identity by default, so a reply
  withheld from one user by a label was readable by anyone who could query the workspace. The harness
  agent has no Application Insights setting (Settings > Advanced offers only solutions and a channel-name
  suffix), so that path does not exist today; if a connection appears later, test what it logs before
  enabling it. See the README's Observability section.
- **Agent memory is per user.** Memory stores the user's own preferences, so one user's memory is not
  shown to another. It is Microsoft-managed storage, not governed by sensitivity labels: do not tell the
  agent to "remember" the contents of protected documents.
