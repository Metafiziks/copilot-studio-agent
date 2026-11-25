# I Swore I'd Never Build a Fourth Agent. So I Built It Where It Doesn't Count.

*Part III. Copilot Studio, SharePoint, a license I didn't have, and an AI judge who gives everyone an A.*

In [Part I](https://www.linkedin.com/pulse/ai-evaluated-my-interview-so-i-built-same-agent-three-williams-gvq4c/), an AI evaluated my interview, so I built the same agent on three clouds and swore, in writing, that I would not build a fourth. Then I [gave them memory](https://www.linkedin.com/pulse/i-gave-rag-agents-memory-now-remember-things-which-somehow-even-uytle/). Then in [Part II](https://www.linkedin.com/pulse/i-tried-make-my-ai-agent-leak-secret-said-politely-twice-williams-15t9c/) I spent a week trying to make the Azure one leak a secret, and it refused. Politely. Twice.

So, about that promise.

Copilot Studio is not a cloud. It's a product that lives inside Microsoft 365, next to your email, judging you. Legally, spiritually, and for the purposes of this article, it doesn't count. I built the fourth one.

### There's no code. There's YAML. There's always YAML.

Same sample corpus as the other three: a fictional factory's safety procedures, quality standards and maintenance manuals. Same 13 eval questions. Same idea that the agent should only see what the person asking is allowed to see.

The agent is a Copilot Studio "harness" agent, which means the model plans its own searches, calls SharePoint itself, and keeps per-user memory. There is no server. There is no container. There is a YAML file and a CLI called `pac` that pushes it into the tenant, and one script that does the rest.

And the model inside this Microsoft product is **Claude Sonnet 4.6**. Later, I hire **GPT-4.1** to grade its homework. Microsoft hosts the venue, Anthropic does the talking, OpenAI does the judging. This agent's org chart is a Thanksgiving dinner where nobody agrees on who's carving.

### It said "I couldn't find that" for days. The document was right there.

Every question. "I couldn't find that." Lockout tagout procedure? Couldn't find it. Conveyor inspection? Never heard of her. The document was in SharePoint. I could open it. I could *ask the agent to fetch it by name*, and it would. It just couldn't find it by searching.

So I did what any reasonable engineer does: I blamed indexing. Then permissions. Then the site. Then the agent. Then, briefly, myself, before going back to blaming indexing.

![Diagram: the same question asked by two users. For the user with a SharePoint-bearing license, the agent's search succeeds and it answers with a citation. For a user on Microsoft 365 Business Premium, the search tools fail with "Knowledge search · unknown" and the agent says "I couldn't find that."](license-not-found.png)

*Figure 1. Same agent, same question, same document. The only difference is a license.*

The account I was testing with didn't have a **SharePoint-bearing license**. The agent's search tools run *as the signed-in user*, and for a user without one they fail quietly with "Knowledge search · unknown." The agent then does the polite thing and says it found nothing. It's not lying, exactly. It's the coworker who says "I looked everywhere" while standing in front of the fridge.

The test pane's trace showed the failure the whole time. I was not reading the trace. Nobody reads the trace. The trace is the terms of service of debugging. Microsoft's documentation on this for harness agents: none that I could find. I found it by testing the same agent as a licensed user and an unlicensed one, side by side, like a parent proving which twin broke the lamp.

### Then I made a new SharePoint site. Rookie mistake.

Fully licensed now, I created a fresh site for the eval corpus. The agent's search failed on it for hours with an error that sounds like a Marvel villain: `NotFound in FederationWorkflow`. SharePoint's own search found the documents fine. The agent, running as the same user, did not.

I found one Microsoft Q&A thread with the same error. No answer. Just a person, alone, in the dark, like a lighthouse keeper's diary.

About **12 hours later** the same site worked. Meanwhile, a new file added to an *established* site was searchable in about 10 minutes. So: new files, fast. New sites, take a nap. Not a Microsoft statement. A me statement, written in blood.

### Microsoft's own grader graded nothing

Copilot Studio has a built-in evaluation feature. I imported the questions, signed in, picked the LLM-judged "General quality" grader, and pressed run.

**12 of 12: Error.** In 34 seconds. For 0 credits. The telemetry showed the evaluator never even reached the agent. It's the fastest, cheapest test suite I've ever run, and it tested nothing. Microsoft documents evaluation for the standard harness, and this is the CLI-built one. Probably unsupported, possibly a bug. Either way: bring your own judge.

### So I brought three

**Judge one: HHEM.** Vectara's open hallucination model. Runs on a laptop CPU, no API key, scores each sentence of an answer against the source document. It is, and I say this with love, a pedant. It caught the agent adding "tears, fraying" to a conveyor inspection list that just says "belt damage," and telling people to "keep the press tagged out" when the guide never says tag out. It also gave "all guards are properly installed and in place" a 0.02 because the document phrased it differently. It's the friend who corrects your grammar at the funeral. Useful. Exhausting.

**Judge two: GPT-4.1, scoring each answer from 1 to 5.** I fed it all 54 saved answers, before and after. It gave **every single one 5/5/5**. Including one where the agent said "production must halt immediately upon discovery of the error," which the standard does not say. Not a judge. A golden retriever with a rubric. Everyone's a good boy. Everyone gets an A.

**Judge three: the same GPT-4.1, asked smaller questions.** Instead of "is this answer good?", I asked it to list every statement and label each one *stated* (the document says it), *added* (the document doesn't), or *contradicted*. Same model. Suddenly it had standards. It found the conveyor embellishments HHEM found, and before I trusted it I made it pass a self-test: a faithful answer scores 1.00, the same answer with made-up reasons tacked on scores 0.40, an invented one scores 0.00. It passed. Turns out the judge was never lenient. It was just being asked vague questions, like the rest of us.

![Bar chart comparing three judges on the same saved answers. HHEM rose from 0.61 to 0.68 before the instructions were tightened to 0.77 to 0.84 after. GPT-4.1 scoring whole answers gave all 54 answers 5/5/5. GPT-4.1 labelling each statement found 0.918 to 0.958 stated in the documents before and 0.974 to 0.990 after, with zero contradictions.](three-judges.png)

*Figure 2. Two of three judges noticed the agent embellishing. None found a fabricated fact.*

The good news, from all three: the agent never invented a step, a number or a procedure. The bad news: it couldn't stop *adding flavor*. A safety procedure does not need flavor. A safety procedure needs to be read out loud exactly as written by a person who is about to put their hand inside a hydraulic press.

### I told the agent to shut up. Politely. It complied maliciously.

The instructions already said "answer only from the documents." The agent interpreted that the way a teenager interprets "clean your room." So I rewrote them: use the documents' own wording, add no explanations or examples, answer only what was asked, no preamble, no bonus safety reminders, cite the document, say plainly what isn't covered.

My first draft also said "name the document once." The agent heard that and **stopped citing inline altogether** on two answers. Citation recall fell from 1.00 to 0.83. That's not a bug, that's a union. The final wording says "cite the document each answer comes from," and citations went back to 1.00.

The effect:

- **HHEM** went from 0.61–0.68 to 0.77–0.84.
- **Statements found in the documents**, per judge three: 0.92–0.96 before, 0.97–0.99 after.
- **Answers got half as long.** About 500 characters instead of 800 to 1,000. The agent lost its TED talk.
- **The price:** keyword recall slipped from 0.99 to about 0.93, because a terse agent occasionally leaves out an item. Still well above the gate. If completeness matters more to you than brevity, loosen one sentence.

### The live run: green, eventually

Then I went to run the whole thing for real, in CI, as a licensed test user, and found the agent had **hit its 1,000-credit cap and switched itself off**. About 4 Copilot Credits a question, and I'd been asking a *lot* of questions. Of all the gates in this project, the billing cap is the only one that fails everyone, instantly, without a self-test. I raised it.

Live run one: every agent metric passed. The run failed anyway, because the **judge** left one label blank on its own homework. The judge. Failed. The fix is one retry for a malformed reply; a second bad reply still fails the run, because a scorer that can't fill in its own form doesn't get to grade anyone else's.

Live run one also caught something real. Asked about a line clearance error, the agent added **"for Line 2 (second shift)"**. That's not in the standard. That's from the test user's *memory*: an earlier eval case tells the agent "my preferred maintenance shift is second shift and my preferred unit label is Line 2," and the agent decided this was relevant to a quality procedure.

In Part II, the Azure agent refused to leak a secret to the wrong person. In Part III, the Copilot agent leaked the right person's shift schedule into a policy answer. Not a security incident. A personality.

Live run two: **16 of 16 checks passed.**

![Live scorecard: keyword recall 0.928, citation recall 1.00, p95 latency 21.2 seconds, memory recall 1.00, HHEM 0.81, 0.99 of statements in the documents, zero contradicted, zero errors. Alongside: the built-in evaluation's 12 of 12 errors, the 1,000-credit cap, the judge's blank label, and the memory leaking into an answer.](copilot-live-scorecard.png)

*Figure 3. The run that passed, and everything that broke on the way to it.*

### Honesty notes, because the other three got them too

- At one point I declared the telemetry broken: zero rows in Application Insights after 10 hours. I had sent zero questions in those 10 hours. The telemetry was fine. I was the outage. Environment-level export does work for this agent type, it just takes hours to activate, and the spans contain the prompt, the reply, the retrieved document text and the user's identity. Don't switch it on for an environment with documents you're protecting.
- GitHub Models, my first choice for a free judge, answered every request with a bare `OK`. Just "OK." I've had that manager.
- GitHub prints repository *variables* in plain text in Actions logs, and on a public repo the logs are public. Put anything identifying in *secrets*.
- `pac copilot publish` sometimes crashes with "Invalid response format" *after the publish has already succeeded*. The deploy script now checks whether it actually worked before believing the error. I have had the same relationship with my own anxiety.
- Memory works, but it's the model's judgement, not a rule. It remembered my favorite color across conversations in 30 seconds. It acknowledged "answer in two sentences," and then answered in one.
- Not built: anomaly detection on the telemetry. The other three have it. This one has a credit cap and a sense of shame.

The [Copilot Studio template](https://github.com/Metafiziks/copilot-studio-agent) is public, with the [evaluation details](https://github.com/Metafiziks/copilot-studio-agent/blob/main/guides/evaluation.md), the scorers, and every saved answer the three judges argued about.

Four platforms now. I'm not promising anything about a fifth. Last time I made a promise, Microsoft sold me a loophole.
