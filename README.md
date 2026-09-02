# Kept

**Meetings make promises. Kept makes them accountable.**

A multi-agent system that reads a meeting transcript, works out which promises
are real, gives each one an owner and a date, creates the tasks, and keeps
score of the ones that slip.

Built for the UnleashX AI intern production assignment (#4, Meeting-to-Execution
Agent).

| | |
| --- | --- |
| Live app | **https://kept-meeting-agent.vercel.app** |
| API | https://kept-api-3lq6.onrender.com/health |
| API docs | https://kept-api-3lq6.onrender.com/docs |
| Demo key | `Mp7kPVabATQGfSbRJP-IpWxQ` (already set in the deployed app; needed only for direct API calls) |
| Stack | Next.js 15 · FastAPI · LangGraph 1.2 · LangChain 1.3 · Neon Postgres + pgvector |
| Models | Groq, Gemini, and OpenAI, tiered and with automatic fallback |

---

## The problem, and why the obvious build fails

Meetings produce two things: information and obligation. Information survives in
notes. Obligation evaporates.

The obvious build is one LLM prompt that returns JSON, rendered as a table. It
fails on exactly what the assignment says it is testing:

- **It cannot tell a commitment from a thought.** "We should probably look at
  Vanta" and "I'll get the Vanta quote by Thursday" are the same shape to a
  summarizer, and opposite in meaning.
- **It hallucinates owners and dates** to satisfy a schema that demands them. A
  wrong owner is worse than a blank one: it becomes a task the real owner never
  sees and nobody chases.
- **It is stateless.** It cannot know Priya has promised the same migration doc
  in three consecutive meetings, which is the only signal that actually predicts
  failure.
- **It has no notion of being wrong.** No confidence, no evidence, nothing to
  measure.

Everything below exists to fix one of those.

---

## What it does

Paste a transcript. Ten agents across four teams process it while you watch,
and you end up with:

**Grounded commitments.** Every extracted item carries a verbatim quote from the
transcript with character offsets. A deterministic verifier rejects anything
whose quote is not actually in the source, so a hallucinated commitment cannot
reach the database. Click any row in the execution view to see the sentence that
produced it.

**A five-way taxonomy, adversarially reviewed.** Decisions, commitments, action
items, suggestions, and discussion. An Analyst extracts generously; a Skeptic
running a different model attacks every candidate and throws out what does not
hold up, with a written reason you can check against the transcript.

**Owners and deadlines, or an honest question.** "I'll take it" said by Priya
resolves to Priya exactly. "Alex" in a room with three of them produces a
question, not a coin flip. Anything unresolved becomes a clarification for a
human rather than a guess.

**Real tasks.** Every owned commitment becomes a task in a mock tracker over
real HTTP, with an idempotency key derived from the commitment so a retry never
duplicates it. Unowned commitments are deliberately *not* assigned to anyone.

**Explainable risk.** Not an LLM's opinion: a pure function whose per-factor
contributions are shown. "Unmentioned for two meetings (+0.27), 5 days past due
(+0.25), owner inferred rather than stated (+0.04)". Evidence of trouble
outranks gaps in what was extracted, because slippage has happened and a
missing owner is only something the system does not yet know.

**Memory across meetings.** Paste the follow-up and the Historian checks every
still-open promise against it: what progressed, what moved to a new date, and
what nobody mentioned at all. Silence is the strongest failure signal in the
system and the one no summarizer produces.

**Something that runs when a date passes.** A nightly sweep finds what went
overdue and drafts one nudge per person who is late, because the failure
meetings are worst at catching is the one nobody convenes to notice. Drafted
and stored, never sent.

---

## Architecture in one paragraph

A **Chief of Staff** supervisor routes between three teams and decides the
genuinely open questions: re-extract when most of a batch was rejected, escalate
when owners could not be settled, stop early when a meeting really has no
commitments in it. Everything below the supervisor is deterministic, because
its order is known. Where a job has an exact answer, code does it: turn
segmentation is a regex, deadline arithmetic is a calendar, name matching is a
roster lookup, and grounding is a substring search. Models are used for
language, and only for language.

Full reasoning and the rejected alternatives are in
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**. The agent roster, with what
each one may and may not touch, is in **[docs/AGENTS.md](docs/AGENTS.md)**.

```
                    ┌──────────────────┐
                    │  CHIEF OF STAFF  │   plans · routes · replans · finishes
                    └──┬────┬────┬───┬─┘   tools: handoffs and a terminator only
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

---

## Running it locally

Needs Python 3.12, Node 22, [uv](https://docs.astral.sh/uv/), and pnpm.

```bash
cp .env.example backend/.env      # fill in at least GROQ_API_KEY and DATABASE_URL
make install
make migrate
make seed                          # demo workspace and a roster of six people
make dev                           # API on :8000, app on :3000
```

A free Neon database and a free Groq key are enough to run everything. Neon is
used rather than Render's free Postgres because that one self-destructs 30 days
after creation.

```bash
make check     # lint, types, and tests, the same as CI
make test      # 152 tests
make eval      # regenerate docs/EVALUATION.md
```

Or drive it from the command line without the UI:

```bash
cd backend
uv run python -m app.cli analyse ../evals/gold/hard_cases.txt
```

---

## What it is measured on

`docs/EVALUATION.md` carries the numbers, the method, and a gallery of real
failures. Summarised: extraction precision and recall against a hand-labelled
gold set, five-way classification accuracy with the hard negatives reported
separately, owner and deadline accuracy *including the rate of correct
abstention*, grounding fidelity, false positives on a transcript containing no
commitments at all, and an adversarial suite covering prompt injection, empty
input, and ASR garble.

There is also an **ablation study**, because "multi-agent beats one prompt" is a
claim and claims should be tested. It measures the full team against a single
mega-prompt baseline, and against the team with the Skeptic, the Verifier, and
the specialist resolvers removed one at a time.

---

## Deployment

| Piece | Where | Notes |
| --- | --- | --- |
| Frontend | Vercel | Root directory `frontend` |
| Backend | Render free | `render.yaml` blueprint. Single worker: 0.1 CPU and 512MB |
| Database | Neon free | 0.5GB, scale-to-zero, no expiry |

The Render free instance sleeps after 15 minutes idle and takes 30 to 60 seconds
to wake. The UI says "waking the backend" rather than showing a spinner that
looks broken, because an honest wait beats a hidden one.

---

## Known limitations

Stated here rather than discovered by the reviewer.

**No OpenAI key is configured.** Groq and Gemini are, so the Skeptic does run
on a different provider from the Analyst and adversarial review is genuinely
independent. What is missing is the evaluation judge, which is pinned to a
provider that produced none of the output: with no third provider it is
disabled rather than quietly graded by a model marking its own work. Adding
`OPENAI_API_KEY` turns it on with no code change.

**Free-tier throughput is the binding constraint.** Groq allows 8000 tokens per
minute per model and, separately, a daily cap that no amount of backoff can
retry its way past. The system pages around the per-minute limit with a
sliding-window budget and alternates between two independently rate-limited
Groq accounts, which is a real doubling rather than a nominal one. A long
transcript still takes longer than it would on a paid tier, because waiting is
the correct behaviour and failing is not.

**Extraction varies between runs at temperature 0.** Free-tier fallback means
the same transcript can be processed by a different model on a different run.
The evaluation reports variance rather than a single flattering number.

**Diarization and ASR are out of scope.** Transcripts are speaker-attributed
text. The gold set includes an unpunctuated ASR sample to cover degraded input,
but audio is not handled.

**Emails and messages are drafted, never sent.** A system that writes on your
behalf should have to be told to send.

**Slippage matching is strong on re-mentions and weaker on heavy paraphrase**
across long gaps. Measured and reported rather than glossed.

---

## Repository

```
backend/          FastAPI, LangGraph, the agents, the services
  app/agents/     the roster, prompts, chassis, handoffs
  app/graph/      the supervisor graph and its blackboard
  app/services/   verifier, router, risk, temporal, roster, search
frontend/         Next.js console
evals/            gold transcripts, adversarial cases, the harness
docs/             architecture, agents, evaluation, AI usage, decisions
```
