# The roster

Ten agents across four teams, plus two deterministic services that are
deliberately *not* agents.

Each agent has its own prompt, model tier, tool belt, and contract. That is what
makes "multi-agent" a description of the system rather than a label on it: you
can point at which agent is weak, change its prompt, and measure the difference
without disturbing the other nine.

---

## What each one may touch

The tool column is the security model, not a feature list.

| Agent | Team | Tier | Tools | Reads the transcript? |
| --- | --- | --- | --- | --- |
| `chief_of_staff` | — | fast | handoffs, `finish` | no |
| `scribe` | Intelligence | fast | none | yes |
| `analyst:decisions` | Intelligence | reason | **none** | yes |
| `analyst:commitments` | Intelligence | reason | **none** | yes |
| `analyst:blockers` | Intelligence | reason | **none** | yes |
| `skeptic` | Intelligence | skeptic | `read_transcript` | yes |
| `attributor` | Resolution | reason | roster lookup | turns only |
| `chronos` | Resolution | reason | date parsing, web search | no |
| `researcher` | Resolution | reason | Tavily search | no |
| `historian` | History | reason | ledger read, event append | yes |
| `operator` | Execution | fast | task API writes | **no** |
| `herald` | Execution | fast | none | no |

**The injection defence is that table.** The agents that read untrusted text
have no tools, and the agent with write access never sees the transcript. An
instruction hidden in a meeting has nothing to reach. This is structural, not a
prompt asking a model nicely, which is why it holds when the model is having a
bad day.

---

## Chief of Staff

Routes between teams and decides the questions that are genuinely open:
re-extract when most of a batch was rejected, escalate or proceed when owners
could not be settled, stop early when a meeting really contains nothing.

It runs on the **fast** tier, because routing is a short structured decision
over a few lines of progress and does not need the reasoning model's rate limit.

It keeps **no conversation history**. Each turn it is re-primed from a short
progress log. That was originally a bug fix (a ToolMessage crossing a subgraph
boundary without its AIMessage produces a malformed history that Groq rejects
outright) and turned out to be the better design: the supervisor routes on
outcomes rather than on a transcript of its own past reasoning, and its prompt
is the same size on the tenth hop as on the first.

Two guarantees wrap it. `ToolCallLimitMiddleware` bounds the loop. And if it
returns without routing while work remains, the graph advances anyway and
records that it overrode the decision, because an agent that abandons two thirds
of the work when a small model loses its place is not something to deploy.

---

## Intelligence

### Scribe
Splits the transcript into attributed turns. Only actually runs when the
deterministic parser cannot cope, which in practice means unpunctuated ASR
output. Almost every transcript is `Speaker: text`, and that is a parsing
problem with an exact answer.

### Analyst (three concurrent briefs)
Extracts decisions, obligations, and blockers. Three focused prompts rather than
one prompt doing three jobs, because a prompt asked to do all three regresses on
all three and cannot be improved on one axis without disturbing the others.

Instructed to err towards including a borderline item with honest confidence,
because a separate reviewer can remove what does not hold up and cannot recover
what was never returned.

**Zero tools, on purpose.** This is the agent that reads raw untrusted text.

### Skeptic
Reviews every candidate and returns `keep`, `downgrade`, or `reject` with a
reason a human can check against the transcript in ten seconds.

Pinned to a **different provider** from the Analyst wherever credentials allow.
Self-critique inside one prompt is weak because the model is grading text it
just committed to. `/health` reports `independent_review` so you can see whether
that guarantee is currently holding.

It has one tool, `read_transcript`, so it can go and look rather than trusting
the quote it was handed.

---

## Resolution

Every agent here has a deterministic pass in front of it, and only the residue
reaches a model. Matching a name against a roster is something code does
perfectly and a model does approximately.

### Attributor
Resolves owners. First person is arithmetic once turns are attributed: "I'll
take it" said by Priya *is* Priya. Names match the roster including aliases and
transcription errors, so "Preeya" is Priya rather than a seventh colleague.

What reaches the model is the genuine residue: "you", "he", a first name shared
by two people. Its answer is then matched back against the roster rather than
trusted, so a hallucinated colleague cannot become an owner.

**It abstains freely.** A collective ("we", "someone") names nobody. A name
matching three people returns a question with the candidates attached, so
answering is one click rather than an essay.

### Chronos
Turns spoken deadlines into dates. Weekday names, offsets, and period phrases
are computed. Two conventions worth naming because both are quietly wrong by
default: end of week is Friday, not Sunday, and "next Tuesday" is the Tuesday of
the following calendar week, scored lower than a bare weekday because English
genuinely disagrees with itself there.

What reaches the model needs the world: "before the Diwali break", "after the
client demo". Returning no date is a real answer, and better than a guess that
silently makes somebody look late.

### Researcher
Adds cited context for anything the transcript names but never explains: a
vendor, a standard, a regulation. Search is budgeted in code rather than asked
for in a prompt, cached for 24 hours, and degrades to no enrichment rather than
failing a run. Citations come from the search result, not from the model, so a
plausible-looking URL cannot be invented alongside a fact.

---

## History

### Historian
Owns everything that is true across meetings rather than inside one. It takes
each still-open commitment in the workspace and asks whether this transcript
says anything about it: progressed, completed, moved to a new date, blocked,
descoped, or contradicted. Anything with a date already past that nobody
mentioned at all increments a silence streak, which is the strongest predictor
of failure in the whole model and the one no summarizer produces.

The direction of that check is the design, and it was wrong in the first cut.
Matching *new mentions against the ledger* misses the most common case
entirely: "Legal came back clean, so I shipped it Monday" is correctly
classified as discussion rather than as a new commitment, so it never becomes a
mention and the old promise sits open forever. Going the other way, from each
open commitment to the whole transcript, is both correct and how a person
actually reconciles a list of promises.

Cost is bounded by ranking candidates with a cheap lexical similarity first and
only sending the top few to a model, because a workspace with two hundred open
commitments must not turn one meeting into two hundred model calls.

---

## Execution

### Operator
The only thing in the system that writes to the outside world, and deliberately
the least clever agent here. It creates a task per commitment and decides
nothing, because every judgment was already made by agents that could see the
transcript.

Two rules enforced in code rather than in a prompt. An unowned commitment never
becomes a task, because a task assigned to a guess is worse than no task: it
looks handled. And a commitment yields one task however many times a run is
retried, because the idempotency key is derived from the commitment rather than
the attempt.

### Herald
Drafts the recap after a meeting, and the per-owner nudges when the nightly
sweep finds something late. Told to use the words from the meeting, never to
invent a deadline or an owner, and to say plainly where something is unowned or
undated, because that is the line that needs a person.

The two jobs are separated on purpose. Right after a meeting nobody is late
yet, and a reminder about work agreed to four minutes ago is noise. Nudges are
written by `POST /internal/sweep`, which runs nightly because a date passing is
the one event no meeting convenes to notice.

Drafts are stored, never sent.

---

## Not agents

Two things are plain code, and it matters that they are.

**Verifier.** Finds each quote in the transcript and attaches its offsets, or
rejects the item. "Is this string in that string" has an exact answer, and
asking a model would reintroduce the very failure the gate exists to stop.

**Risk scorer.** A pure function whose per-factor contributions are shown in the
UI. Testable, free to recompute on every read so it never goes stale, and
explainable: someone told their commitment is at risk deserves to know which
part is the problem, and an LLM-produced number cannot tell them.

---

## Model tiers

| Tier | Who | Chain |
| --- | --- | --- |
| fast | Chief of Staff, Scribe, Operator, Herald | OpenAI gpt-5.4-nano, then Groq gpt-oss-20b, then Gemini Flash-Lite |
| reason | Analyst, Attributor, Chronos, Researcher, Historian | OpenAI gpt-5.4-mini, then Groq gpt-oss-120b, then Gemini Flash-Lite, then Groq gpt-oss-20b |
| skeptic | Skeptic | ordered to land on a provider the Analyst did not use |
| judge | evaluation only | never the vendor that produced the output |

Paid first and free underneath, which is the opposite of the obvious order and
the right one. Measured over a full run, 113 seconds went to models and 286
went to waiting for a free tier's token window to roll over. The chains still
resolve to Groq and Gemini when no paid key is configured, so the system runs
either way, just more slowly. See
[ADR 7](adr/0007-paid-reasoning-tier.md).

Every identifier is environment-overridable and checked against the provider's
own model list at boot. That check has earned itself three times: the Groq
model this project was planned around does not exist on the account it runs
under, the Gemini identifier was wrong, and OpenAI's `gpt-5-mini` appears in
this account's listing while returning 404 on use because the organisation is
unverified. A listing is not an entitlement.

Cost, per meeting, at current prices: roughly **$0.024** for a short meeting
and **$0.088** for a long one. The console shows it per run and the evaluation
reports it per case, because a number nobody can see is a number nobody can
challenge.
