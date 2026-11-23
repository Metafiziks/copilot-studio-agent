# Protect documents with sensitivity labels and site permissions

Set `KB_ACCESS_MODE=protected` to make the agent return only what the **signed-in user** may see.
Copilot Studio retrieves SharePoint content through Microsoft Search under the user's own Entra
identity, so two things restrict what an answer can draw on:

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
- Two licensed test users (guests do not work for SharePoint answers under SSO).

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

Then share the agent (Copilot Studio → Share): **End user access** for both users. The test pane
needs **Editor**; grant it temporarily and remove it afterwards.

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

In the Copilot Studio test pane, in a **new session each time**, each user asks questions that match
only the fixture under test. Wording matters: "site-restricted test code" also matched `restricted.docx`.

| Question | Allowed user | Denied user |
|---|---|---|
| Open the restricted site | opens | "You need access" |
| Open `restricted.docx` | opens | "protected by a rights management service" |
| "What is the code in site-restricted.docx?" or "What is the KESTREL code?" | KESTREL-19, cited | no answer |
| "What does the OL Restricted Test Document say?" | HELIOS-31, cited with a label badge | not retrieved |
| "What is the unlabeled control code?" | MAPLE-82 | MAPLE-82 |

A good run also shows in the trace: the denied user's coverage lists only `control.docx`; the
allowed user's lists all three fixtures.

### Results (test tenant, November 2026)

| Test | Allowed user | Denied user |
|---|---|---|
| Open the restricted site | allowed | "You need access" |
| Open `restricted.docx` | opens | blocked by rights management |
| Site-level question | KESTREL-19, cited | no answer (also with "What is the KESTREL code?") |
| Document-level question | HELIOS-31, cited, label badge | `restricted.docx` absent from retrieval |
| Control question | MAPLE-82 | MAPLE-82 |

One clean trial per user per level, in the test pane only. This verifies query-time trimming for the
signed-in user. It was not tested through Teams or Microsoft 365 Copilot.

## Things that cost time

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
