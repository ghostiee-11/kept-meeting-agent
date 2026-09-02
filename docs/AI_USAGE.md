# AI usage

The assignment asks for this, and a vague answer would be worth less than none.
This is what AI tooling did on this project, what it did not, and where it was
wrong.

Claude Code was used throughout, as a pair rather than as a generator: I set
the architecture and the constraints, it wrote most of the first drafts, and
every non-trivial claim it produced was checked against something that could
disagree with it, usually a test or a live API.

---

## Where it genuinely helped

**First drafts of well-specified code.** SQLAlchemy models from a schema I had
already designed, Pydantic contracts, FastAPI routers, the retry and
circuit-breaker client, the React components once the design system existed.
This is the work AI is straightforwardly good at, and it collapsed hours into
minutes.

**Adversarial test cases.** Asking for the ways an extraction pass could be
fooled produced the hard-negative taxonomy that shaped the Analyst prompt:
negated commitments, conditional ones, hypotheticals, third-party promises,
retractions, rhetorical questions. Several of those I would not have thought to
test for, and two of them are cases the system still gets wrong, which is
recorded in [EVALUATION.md](EVALUATION.md) rather than quietly dropped.

**Faster iteration on prose that has to be exact.** Agent prompts are code with
a bad type system. Rewriting one and immediately running it against the gold
set closed a loop that would otherwise have been slow enough that I would have
done it less often.

**Debugging by narration.** Explaining a failure to something that asks
follow-up questions is a fast way to notice the assumption you did not know you
were making. The Historian bug below was found that way.

---

## Where it was wrong, and how that was caught

These are real, and they are why nothing here was accepted on confidence.

**It reconciled in the wrong direction.** The first Historian matched new
mentions against the ledger, which reads sensibly and misses the most common
case entirely: "Legal came back clean, so I shipped it Monday" is correctly
classified as discussion, never becomes a mention, and so the open promise it
closes stays open forever. Caught by printing what the Analyst actually
extracted from that line rather than trusting that the pipeline saw it. The fix
was a rewrite: iterate open commitments, check each against the whole
transcript.

**It wrote an evaluation metric that could not fail.** The slippage gold case
has no `commitments` key, so the fuzzy-match scorer compared an empty expected
set against an empty actual one and reported precision 1.00, recall 1.00. A
scorecard that flatters itself is worse than no scorecard. That case is now
excluded from the extraction scores and covered by a real integration test
instead.

**It planned around a model that does not exist.** `llama-3.3-70b-versatile`
was in the design and is not on the Groq account this runs under. Found by the
boot-time check that queries each provider's own model list, which was itself
added because model identifiers rot. The same check later caught the Gemini
identifier being wrong.

**It got a risk weighting backwards.** A commitment a week past due and moved
twice scored lower than one that was merely missing an owner. Slippage and
lateness are things that have happened; a missing owner is something the system
does not yet know. Caught while writing the sweep, because the number it
returned did not match what a person would say about that row.

**It let a re-run double the ledger.** Meetings are keyed on the transcript
hash, so submitting the same text twice correctly reused the meeting and
incorrectly appended a second full copy of everything under it. Caught by
running the same sample twice while testing an unrelated page, which is the
first thing a reviewer would do.

---

## What I did not delegate

**The architecture.** The hybrid supervisor, the deterministic-first split, the
decision to make grounding a gate rather than an instruction, and the choice to
compute risk on read rather than store it are mine, and the alternatives I
rejected are written down in [ARCHITECTURE.md](ARCHITECTURE.md) with the
reasoning.

**What counts as done.** Every claim in this repository is backed by something
that runs: a test, a live endpoint, or a number in the evaluation report. When
AI told me something worked, that was the beginning of checking, not the end of
it. The tests here exist because I did not believe the code, including the code
I wrote myself.

**The honest parts.** The known limitations in the README, the failure gallery
in the evaluation, and this file are deliberately not flattering. A submission
that only reports its wins is asking to be taken on trust, and the whole point
of the system is that trust should be checkable.

---

## The workflow, concretely

Plan a phase, write the constraints down, have AI draft, read every line, run
it against real providers rather than mocks, keep what survived, and commit in
small pieces so that anything that turned out to be wrong could be found later.
The commit history is that process, not a tidied-up version of it.
