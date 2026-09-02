# Architecture

The reasoning behind the build, including the alternatives that were rejected
and why. The roster itself is in [AGENTS.md](AGENTS.md); the numbers are in
[EVALUATION.md](EVALUATION.md).

---

## The shape of the problem

A meeting produces information and obligation, and they behave differently.
Information survives in notes. Obligation evaporates, because a promise with no
owner and no date is indistinguishable from a promise nobody made.

So the system is not a summarizer with extra steps. It is a ledger: every row
has a person, a date, a verbatim sentence that produced it, and a history of
what happened to it afterwards. Everything in the design follows from wanting
that ledger to be trustworthy enough that somebody would act on it.

Three properties fall out of that, and they are in tension:

- **Recall**, because a promise the system missed is one nobody chases.
- **Precision**, because an invented promise costs more than a missed one: it
  makes the whole ledger something to double-check, and a ledger you check by
  hand is worse than no ledger.
- **Abstention**, because the correct answer is sometimes "three people are
  called Alex and I do not know which one". A system that always answers is a
  system that guesses.

One prompt cannot be tuned for all three at once, and that is the practical
argument for the topology below, ahead of any architectural taste.

---

## Topology: a hybrid supervisor

```
                    ┌──────────────────┐
                    │  CHIEF OF STAFF  │   LLM routing, replanning, finishing
                    └──┬────┬────┬───┬─┘   tools: handoffs and a terminator
        ┌──────────────┘    │    │   └────────────────┐
        ▼                   ▼    ▼                    ▼
┌───────────────┐  ┌──────────────┐  ┌──────────┐  ┌────────────┐
│ INTELLIGENCE  │  │  RESOLUTION  │  │ HISTORY  │  │ EXECUTION  │
│ scribe        │  │ attributor   │  │historian │  │ operator   │
│ analyst ×3    │  │ chronos      │  │          │  │ herald     │
│ skeptic       │  │ researcher   │  │          │  │            │
└───────┬───────┘  └──────┬───────┘  └────┬─────┘  └─────┬──────┘
        └─────────────────┴───────────────┴──────────────┘
                              ▼
        shared blackboard · typed artifacts only
        + deterministic services: verifier · risk scorer
```

**An LLM routes at the top, deterministic edges run inside each team.**

The supervisor decides things that are genuinely open: re-extract when the
Skeptic and the Verifier threw out most of a batch, escalate to a human or
proceed when owners could not be settled, stop early when a meeting really
contains nothing. Those are judgment calls whose right answer depends on what
just happened.

Inside a team the order is known in advance. The Attributor runs before Chronos
because a deadline resolved for the wrong person is wasted work, and no model
call is needed to discover that. Routing there with an LLM would buy
nondeterminism and tokens and return nothing.

### Rejected: full swarm

Peer-to-peer handoff with no coordinator. Rejected because termination is hard
to guarantee, cost is unbounded, and the trace becomes unreadable at exactly
the moment you need it. The point of naming agents is being able to say which
one was wrong.

### Rejected: fully deterministic pipeline

A fixed DAG with no supervisor is cheaper and completely predictable, and it
cannot replan. When the Verifier rejects half a batch, something has to decide
whether to re-extract with the rejection reasons attached or to proceed with
what survived. A hardcoded threshold is a worse version of that decision, not a
simpler one.

### Rejected: `langgraph-supervisor`

The prebuilt package is pre-1.0. The supervisor is hand-rolled on core
`Command(goto=..., graph=Command.PARENT)` handoffs, which are stable API. A
pre-1.0 dependency in the critical path of a system meant to be reviewed is a
bad trade for perhaps forty lines.

---

## Deterministic first, models for language

Every stage has code in front of it, and only the residue reaches a model.

| Job | Code does | Model does |
| --- | --- | --- |
| Speaker turns | regex segmentation, roster-normalised names | nothing |
| Owner resolution | exact and alias lookup against the roster | pronouns, coreference, genuinely ambiguous cases |
| Deadlines | `dateparser` over the meeting's timezone | spoken forms the parser cannot reach, external anchors |
| Grounding | substring search with offset repair | nothing |
| Risk | pure weighted function | nothing |
| Slippage candidates | lexical similarity ranking | adjudicating the top few |

This is not model minimalism for its own sake. Each of those code paths is
exactly reproducible, free, instant, and testable, and every one of them is a
place a model could have hallucinated instead. The models are left with the
part that is actually language: is this sentence a commitment, does this
promise refer to that one, is this person the person who accepted.

---

## The three mechanisms that make output trustworthy

### Grounding is a gate, not an instruction

Every extracted item carries a verbatim quote. A deterministic verifier finds
that quote in the source or the item does not exist. Exact match first, then
whitespace-normalised, then a bounded repair of the offsets; anything still
unfound is recorded as a rejection with a reason rather than dropped silently.

Asking a model to "only use text from the transcript" is a request. This is a
gate. It is also why the UI can highlight the exact sentence behind every row:
the offsets are real because they were computed, not reported.

### Adversarial review beats self-critique

The Analyst is instructed to be generous, the Skeptic to assume nothing is a
commitment until shown otherwise, and they run on different providers where
credentials allow. Self-critique inside one prompt is weak because the model is
grading text it has already committed to; an independent reviewer with the
opposite instruction and a tool to re-read the source is a different judgment,
not the same one twice.

Rejections are stored and shown with their reasons, so the reviewer can check
the system's judgment instead of taking its output on faith. A rejection nobody
can inspect is just a deletion.

### Abstention is a first-class outcome

No agent invents an owner or a date to satisfy a schema. The Attributor
abstains and raises a specific, answerable question carrying the candidates it
already considered and the evidence. That question is answerable in one click,
and the answer is written to the ledger with `actor_kind=human` on every event
it produces, so "who decided this" is always answerable afterwards.

Clarification resolution is deliberately **not** a graph resume. The run that
raised the question has already finished and persisted everything it knew;
replaying a whole multi-agent pipeline to write one owner would spend a run's
budget to apply a fact a person just supplied.

---

## State, and what is deliberately not stored

The blackboard carries typed artifacts, not conversation. Each agent gets a
brief and the artifacts it needs, and writes back a contracted type. Passing
every message to every agent is the standard multi-agent failure: cost grows
quadratically and agents get confused by each other's reasoning.

In the database, `commitment_events` is append-only and is the audit trail the
timeline renders. Two things are computed on read rather than stored:

**Risk**, because a stored score is what the score was on the day of the run.
A commitment that was fine on Tuesday is overdue by Friday without anything
having been written to it. Scoring is a pure function over data already
loaded, so recomputing is free and is the only way the number is ever right.

**Overdue, at risk, and slipped**, for the same reason. `CommitmentStatus`
deliberately has no `overdue` member: it is arithmetic over a date, and the
moment it were written down it would start going stale.

Weighting inside the risk model puts evidence of trouble above gaps in
extraction. Slippage, silence and lateness have happened; a missing owner or a
soft date are things the system does not yet know. An earlier cut had those the
other way around, which scored a week-late twice-moved commitment lower than
one that was merely unowned, and that is backwards.

---

## Time, and the sweep

Everything above runs because a meeting happened. One thing runs because a date
passed: `POST /internal/sweep`, called nightly, finds what is overdue and has
the Herald write one nudge per person who is late.

It is not a graph. There is nothing to route and no ambiguity to resolve, so an
LLM supervisor would add cost and nondeterminism to a job whose steps are fully
known. It is closed when no token is configured rather than open, because an
unauthenticated caller could otherwise make the system spend model budget on a
schedule of their choosing. It doubles as the keep-warm ping for Render's free
tier.

---

## Failure, which is the normal case on free tiers

Free-tier rate limits are not an edge case here, they are the weather.

**Tiered routing with fallback chains.** Each tier is an ordered list of
provider and model pairs, filtered at boot to whatever has credentials, with
`ModelFallbackMiddleware` moving down the chain on 429 and 5xx. Model
identifiers are checked against each provider's own model list at boot, which
earned itself immediately: the model this project was planned around does not
exist on the Groq account it runs under.

**Token-budget pacing.** A sliding window per model reserves an estimate before
a call and reconciles the actual usage after, so the system waits rather than
being refused. Groq's per-minute ceiling is low enough that three concurrent
extraction briefs would otherwise trip it on a long transcript.

**Two Groq accounts, round-robin.** Confirmed independently rate-limited by
their own response headers before being relied on. This is the answer to the
*daily* cap, which no amount of backoff fixes.

**A supervisor that cannot abandon the work.** If the Chief of Staff returns
without routing while work remains, the graph advances anyway and records that
it overrode the decision. A small model losing its place must not cost the run.

**Idempotency at the edges.** Tasks carry a key derived from the commitment
rather than the attempt, so a retry never duplicates one. A transcript
submitted twice reuses the meeting and does not write a second copy of the
ledger, which matters more than it sounds: duplicates become open commitments
that the Historian then reports as phantom slippage in the next meeting.

---

## Security

The security model is the tool table in [AGENTS.md](AGENTS.md), not a paragraph
of intentions.

The agents that read raw transcript text have **no tools**. The agent that
writes to the outside world **never reads the transcript**, and accepts only
commitment IDs that have already been extracted, grounded, reviewed,
attributed, and dated. An instruction hidden in a meeting has nothing to reach,
structurally, rather than because a prompt asked a model nicely.

Around that: untrusted content is wrapped in explicit delimiters with a system
rule that it is data and never instruction, all output passes schema validation
plus span grounding, PII is redacted before external calls, the demo endpoints
are behind a key with per-IP rate limiting and a transcript size cap, and
secrets are never logged or returned. `/health` reports provider availability
as booleans only.

---

## Deployment shape

| Piece | Where | Constraint that shaped a decision |
| --- | --- | --- |
| Frontend | Vercel | — |
| Backend | Render free | 0.1 CPU, 512MB, sleeps after 15 minutes. One worker, tight pool, no in-process background threads |
| Database | Neon free | Chosen over Render Postgres, which self-destructs 30 days after creation and would break the demo link mid-review |
| Cron | GitHub Actions | Free, and the sweep call doubles as keep-warm |

The cold start is surfaced in the UI as "waking the backend" rather than hidden
behind a spinner, because an honest wait beats one that looks broken.
