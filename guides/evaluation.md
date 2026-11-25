# Evaluation in detail

The README has the gates and the latest results. This page has the rest: how to run each scorer, every run so
far, what each scorer is and is not good for, how the agent's instructions were tightened, and what was found
when Copilot Studio's own graders were tried.

## Running it

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

The caller must be a **real user with a SharePoint-bearing license**, who is a member of the knowledge site and has the
agent shared with them (**Can use**): the agent retrieves SharePoint as the signed-in user, an app-only
caller has none, and for an unlicensed user every question comes back "not found".

HHEM needs CPU PyTorch; the LLM judge needs a key:

```bash
pip install -r requirements-hhem.txt --extra-index-url https://download.pytorch.org/whl/cpu   # about 1.5 GB on disk
.venv-eval/bin/python scripts/run_evals.py --hhem on          # --judge on as well once JUDGE_API_KEY is set (CI: --judge auto)
python scripts/hhem.py selftest                               # or the "Scorer self-tests" workflow, no agent needed
```

To switch the LLM judge on in CI, add `JUDGE_API_KEY` as a secret in the `dev` environment (and optionally
the variables `JUDGE_BASE_URL` and `JUDGE_MODEL`, default `https://api.openai.com/v1` and `gpt-4.1`); the
eval then judges every answer and the `Scorer self-tests` workflow checks the judge first. Use a different
model family from the agent so the judge does not favour its style.

## Results

Run as a licensed test user against the sample corpus, before and after tightening the agent's instructions:

| Metric | Before (dev) | After (e2e) | Live CI eval, after (dev) | Gate |
|---|---|---|---|---|
| Keyword recall | **0.986** | **0.924** | **0.949** | ≥ 0.65, passes |
| Citation recall | **1.000** | **1.000** | **1.000** | ≥ 0.60, passes |
| p95 latency | **25.7 s** | **29.2 s** | **20.9 s** | ≤ 45 s, passes |
| Memory recall | **1.00** | **1.00** | **1.00** | = 1.0, passes |
| HHEM claim-level mean | 0.61 to 0.68 | **0.77** (measured on the same answers in CI) | **0.82** | ≥ 0.65, passes |
| LLM judge, stated share | 0.92 to 0.96 (re-scored) | 0.99 (re-scored) | **0.974** | ≥ 0.95, passes |
| LLM judge, contradicted claims | 0 | 0 | **0** | 0, passes |
| LLM judge faithfulness / relevance / completeness | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 / 1.00 | **0.98 / 1.00 / 1.00** | ≥ 0.85 / 0.85 / 0.60, passes |
| Failed calls | 0 | 0 | 0 | 0, passes |

The live CI eval (Actions run 45707615037, `eval.yml`) scored every gate above but was marked failed by one
scorer error: on one answer the judge's claim-mode reply left a label blank. The judge now gets one correction
round for a malformed reply; re-scored with that fix, the same 12 answers judge with no errors (stated share
0.976). Its one flagged answer is a real find: asked about a line clearance error, the agent added "for Line 2
(second shift)", which is the eval user's stored preference, not something the standard says. A second live run
(Actions run 45710449948) passed every check: keyword recall 0.928, citation recall 1.00, p95 21.2 s, memory
1.00, HHEM 0.811, judge stated share 0.99 with no contradicted claims and no judge errors, faithfulness,
relevance and completeness 1.00. Its only added statement was "Only after these four steps have been completed
may the line be restarted", an inference from the standard's "before restarting", and the Line 2 aside did not
recur.

## The scorers

| Scorer | What it measures | Needs | Status |
|---|---|---|---|
| **HHEM** (`scripts/hhem.py`) | Probability (0 to 1) that each claim in an answer is consistent with its source documents, averaged. Vectara's HHEM-2.1-open: Apache-2.0, pinned revision, runs on CPU | PyTorch (`requirements-hhem.txt`); no key | **Running in CI.** Gate: claim-level mean ≥ 0.65; the worst single answer is reported, not gated |
| **LLM judge** (`scripts/judge.py`) | Faithfulness, relevance and completeness, 1 to 5 each | `JUDGE_API_KEY` for any OpenAI-compatible endpoint | **Runs live** (`gpt-4.1`). The 1 to 5 scores saturate (every saved answer 5/5/5), so they only catch a regression |
| **LLM judge, claim mode** (`scripts/judge.py`) | Each statement labelled stated, added or contradicted; the share stated | Same key | **Runs live.** Gate: stated share ≥ 0.95 and no contradicted claim. Separates the runs: 0.92 to 0.96 before tightening, 0.99 after |

Each scorer must pass a **self-test** before it scores anything: HHEM scored a faithful answer 0.90, an
invented one 0.03 and an off-topic one 0.005. If a scorer cannot tell known-good from known-bad answers the
run fails instead of reporting its numbers.

**What HHEM is, and is not, good for here.** It scores each sentence or list item against the cited
documents (scoring a whole multi-item answer in one pass was unreliable: one correct answer scored 0.31, 0.16
and 0.07 on three runs). It is **strict**: it flags any claim the document does not state outright. That
catches real embellishment, such as "tears, fraying" for the conveyor check, "a qualified technician can
assess and repair it", or "keep the press tagged out" (the guide never says tag out). It also flags harmless
restatement: "Guard placement: all guards are properly installed and in place" scored 0.02. So it measures
**how closely the agent sticks to the documents' wording**, which is a useful trend (a drop means the agent
is drifting), but it cannot separate harmless paraphrase from fabrication, so there is **no per-answer
floor**; the lowest answer is reported and the gate is on the mean. Telling the two apart was meant to be the LLM judge's
job, but the judge turned out too lenient to do it: see "What the LLM judge showed" below, and claim mode after it. The weakest claims per answer are saved in
`eval_results.json` under `hhem.weakest_claims`, so a low score can be read, not just trusted.

**What the LLM judge showed.** `python scripts/judge.py rescore` (the `Scorer self-tests` workflow runs it) judges the
saved answers with `gpt-4.1` and costs no agent credits. All 54 answers, before and after tightening, scored
faithfulness, relevance and completeness 5/5/5, with no unsupported claims listed. That agrees with HHEM on the
main point: the agent invents no facts, steps or numbers. It cannot show the improvement HHEM measured, and it
missed embellishments a reader can find: "Production must halt immediately upon discovery of the error" and
"physically separated and held" are not in the line clearance standard (which says only "Stop the line, segregate
affected product, ..."), and neither lowered the score. A 1 to 5 whole-answer score saturates on correct answers,
so use the judge to catch a regression (one answer dropping to 3 or below fails the run) and HHEM to track drift.

**Claim mode closes that gap.** `python scripts/judge.py rescore --claims` asks the judge to list each statement in
the answer and label it stated (the documents say it, in any words), added (an explanation, reason, example or
detail they do not contain, however plausible) or contradicted. Its self-test requires it to rate a faithful answer
above the same answer with added detail (1.00 against 0.40 with `gpt-4.1`) and to catch an invented one (0.00). On
the 54 saved answers:

| Run | Stated share | Added statements per answer | Contradicted |
|---|---|---|---|
| Before tightening (run 1, 2, 3) | 0.918, 0.958, 0.955 | 0.58, 0.42, 0.33 | 0 |
| After ("name once", final "cite") | 0.986, 0.988 | 0.08, 0.08 | 0 |
| Live CI eval on dev, final wording | 0.974 (0.976 re-scored); 0.990 on the second run | 0.17; 0.08 | 0 |

It agrees with HHEM on direction and finds what a reader would: the old conveyor answer adds "tears, fraying" and
"on or around the conveyor" to a manual that lists only "unusual noise, belt damage, loose fasteners, foreign
objects". It is noisy on a single answer (it let "halt immediately" through), so the gate is on the run mean, at
0.95: that fails the most elaborate old run, and a stricter value would fail correct runs on noise, since one
added claim in a five-claim answer costs a 12-answer run about 0.015. No answer in any run contradicted the
documents. The eval stores the statements that were not stated in `eval_results.json` under
`judge.claims.not_stated`.

## Tightening the agent's instructions

**Fixing it: tighter instructions, measured.** The agent's instructions said to answer only from the
documents, yet it elaborated. They now also say to use the documents' wording, add no explanations, examples or
inferences, answer only what was asked with no preamble or extra warnings, cite the document, and say plainly
which part is not covered (`agent/settings.mcs.yml`). Effect, from the full 12-answer runs below:

- Faithfulness: the HHEM claim-level mean rose from 0.61 to 0.68 to **0.77**, and the share of claims HHEM
  supports from 63 to 71% to **83%**. The weakest answer went from 0.28 to 0.36 to 0.44.
- Answers are about **half as long** (about 500 characters against 800 to 1,000).
- Citations: a first wording ("name the document once") scored higher still (0.84) but made two answers drop
  their inline citation, so citation recall fell from 1.00 to 0.83; the final wording ("cite the document each
  answer comes from") restored it to **1.00**.
- Cost: keyword recall slipped from about 0.96 to **0.92** (the terser answers omit an item now and then; the
  zero-energy answer went from 0.75 to 0.50). Still above its 0.65 gate. If completeness matters more than
  tightness, relax the "answer exactly what was asked" sentence.

Single runs vary (the agent words answers differently each time), so treat the 0.77 as a level, not a point.

| Full run (12 answers; run 4 has 6) | Whole answer | Claim by claim | Claims HHEM supports |
|---|---|---|---|
| 1 | 0.55 | **0.68** | 71% |
| 2 | 0.59 | **0.65** | 68% |
| 3 | 0.50 | **0.61** | 63% |
| 4 (6 answers, final code) | n/a | **0.58** | 60% |
| 5, tightened instructions, "name the document once" (dev) | 0.78 | **0.84** | 91% |
| 6, final instructions (e2e) | 0.78 | **0.77** | 83% |

The answers from these runs and from the live CI evals are saved in `tests/fixtures/agent_answers.json`, and
`python scripts/hhem.py rescore` re-scores them (also a step of the `Scorer self-tests` workflow), so a change
to the scoring can be checked against real answers without spending agent credits.

## How the first scored runs failed (so you do not repeat it)

**How it got here (so you do not repeat it).** The first run, against the e2e site, scored 0.06 and 0.0.
The cause was not the agent being wrong. Two checks had been misleading:

- A browser search run as the unlicensed site owner returned nothing, which looked like "the site is not
  indexed". Run as a licensed user, SharePoint search found the documents in both corpus sites within
  hours. Always check as a licensed user.
- The agent itself still failed on the two brand-new corpus sites for hours (errors, then "no results"),
  for Jane and for John, while the same agent answered correctly from the two-day-old dev site. A new file
  added to an established site was retrievable **about 10 minutes** after upload. So the agent's search
  appears to take a long time to start covering a **brand-new site** (not a Microsoft statement), while new
  files on a covered site are quick. The conversation transcripts give the failing call's exact error,
  `sharepoint_*_search failed: NotFound in FederationWorkflow` (every search but one, from 03:02 to 06:35 UTC
  on the e2e agent), and the telemetry shows the same e2e agent retrieving from the same new site, `ol-eval-corpus`,
  successfully 12.5 hours later. A Microsoft Q&A thread reports the same error text, with no answer. The corpus therefore runs in the established dev
  site, uploaded flat (`--with-corpus`); sub-folders were not re-tested after the site became usable.

## Copilot Studio's built-in graders

**The built-in graders**, tried first, are Copilot Studio's
[Agent Evaluation](https://learn.microsoft.com/microsoft-copilot-studio/analytics-agent-evaluation-create).
Export the cases for it:

```bash
python3 scripts/export_eval_csv.py      # writes .build/eval_cases.csv
```

Import it under **Evaluate → Create your first evaluation → Import**. What was found doing exactly that on
the harness agent:

- The **Evaluate** tab exists for harness agents, and the 12 single-turn cases imported (the memory case is
  multi-turn and is skipped). Methods offered: General quality, Content safety, Compare meaning,
  Capability use, Keyword match, Text similarity, Exact match, Classification.
- **Keyword match does not read the CSV.** It wants keywords typed per row in the UI (Any or All applies to
  the whole set); the `Expected response` column did not populate them. Not worth ~60 hand-typed entries
  when `run_evals.py` already scores keyword recall, so only **General quality** (the LLM-judged one) is set.
- **A run was attempted and fails for this agent type.** The run needs an authenticated account
  (Manage profile and connections > Add an account, an OAuth sign-in by a person), done here as a licensed
  user. All 12 cases returned *Error* ("The agent rejected this request as invalid, so this test case
  couldn't be evaluated") in 34 s at 0 credits, and Application Insights shows no `InvokeAgent` span for the
  run, so the evaluator never reached the agent. Microsoft documents evaluation only for agents on the
  *standard harness* (with No authentication or Authenticate with Microsoft), never mentions CLI-created
  agents, and gives the *Error* state no meaning. Treat the built-in evaluation as unsupported for this agent
  type; `scripts/run_evals.py` is the evaluation. A standard-harness agent was not run as a control (the
  classic agent that could have been was deleted just before), so this is the likely cause, not a proven one.

The built-in Evaluation REST API (`api.powerplatform.com/copilotstudio/.../makerevaluation/testsets/{id}/run`)
needs a **user** access token too, and its documentation does not name the scope or say whether the
draft or published agent is evaluated, so triggering it from a script is not implemented.

## Mapping to the Azure template

How each Azure metric maps:

| Azure metric | Copilot Studio | Status |
|---|---|---|
| Faithfulness (GPT-5 judge) | HHEM (`scripts/hhem.py`) and the LLM judge's claim mode; built-in General quality rejects this agent type | HHEM **0.61 to 0.68** before tightening, **0.77 to 0.82** after; claim mode **0.92 to 0.96** before, **0.97 to 0.99** after |
| Answer relevance (GPT-5 judge) | LLM judge (`scripts/judge.py`); built-in General quality rejects this agent type | **1.00** on every run, a ceiling. Claim mode (faithfulness): stated share **0.92 to 0.96** before tightening, **0.99** after |
| Keyword recall | `scripts/run_evals.py` (also built-in Keyword match) | **0.92 to 0.95** (0.986 before the instructions were tightened) |
| Citation recall | `scripts/run_evals.py`; citations appear as file names in the reply payload | **1.000** |
| p95 latency | `scripts/run_evals.py` | **21 to 29 s** |
| HHEM hallucination score | `scripts/hhem.py`, the same model the Azure template uses | **Running in CI**; gate: claim-level mean ≥ 0.65 |
| Memory recall | `scripts/run_evals.py` | **1.00** |
