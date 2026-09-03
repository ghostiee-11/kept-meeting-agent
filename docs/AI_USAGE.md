# AI usage

The assignment asks how AI was used. A vague answer would be worth less than
none, so this is specific: what I directed, what I decided, what I rejected,
and the places AI was wrong and how I caught it.

I used AI heavily and deliberately on this project. I do not think that needs
apologising for, and I do not think it is interesting on its own. What is
interesting is the division of labour, because that is where the engineering
actually happened.

---

## How I worked

I treated the model as a fast pair, not as an author. Every phase started with
me writing down the constraints: the topology, the contracts between agents,
what each component was forbidden to do, and what "done" would have to look
like. AI wrote most of the first drafts against those constraints. I read every
line, ran it against real providers rather than mocks, kept what survived, and
committed in small pieces so anything that turned out to be wrong could be
found later. The commit history is that process rather than a tidied-up version
of it.

The rule I held to throughout: **AI telling me something worked was the
beginning of checking, not the end of it.** Nearly every test in this
repository exists because I did not believe a piece of code, including code I
had written myself.

---

## What I decided, and AI implemented

These are the choices the system is built on. They are mine, they are argued
in [ARCHITECTURE.md](ARCHITECTURE.md) with the alternatives I rejected, and I
would defend any of them in a review.

**A hybrid supervisor rather than a swarm or a pipeline.** An LLM routes at the
top where the decision is genuinely open; everything inside a team is
deterministic because its order is known. I rejected a full swarm because
termination is hard to guarantee and the trace becomes unreadable, and a fixed
pipeline because it cannot replan when half a batch is rejected.

**Grounding as a gate rather than an instruction.** Every item carries a
verbatim quote, and plain code verifies it against the source or the item does
not exist. Asking a model to "only use text from the transcript" is a request;
this is a property of the system.

**Abstention as a first-class outcome.** No agent invents an owner or a date to
satisfy a schema. I would rather ship a blank field and a precise question than
a confident guess, because a wrong owner becomes a task the real owner never
sees.

**Risk computed on read, never stored.** A score written down on Tuesday is
wrong by Friday without anything having changed. I also set the weights so
evidence of trouble outranks gaps in extraction, after I noticed the first cut
scored a week-late twice-moved commitment below one that was merely unowned.

**Deterministic first, models for language.** Turn segmentation is a regex,
deadline arithmetic is a calendar library, name matching is a roster lookup,
grounding is a substring search. Models are used for the part that is actually
language.

**A paid model in front of the reasoning tier, after measuring.** I did not
assume the free tier was the bottleneck, I measured it: 113 seconds in models
against 286 seconds waiting on a rate limit. The fallback chains stay
underneath so the system still runs on free keys alone.

---

## Where AI was wrong, and how I caught it

This is the part I would want to read if I were reviewing someone else's
submission.

**It reconciled in the wrong direction.** The first Historian matched new
mentions against the ledger, which reads sensibly and misses the case that
matters most: "legal came back clean, so I shipped it Monday" is correctly
classified as discussion, never becomes a mention, and so the promise it closes
stays open forever. I found it by printing what the Analyst actually extracted
from that line instead of trusting that the pipeline had seen it, and I had it
rewritten to iterate open commitments against the whole transcript.

**It wrote an evaluation metric that could not fail.** One gold case has no
commitment labels, so the fuzzy scorer compared an empty expected set against an
empty actual one and reported precision 1.00, recall 1.00. I caught it while
reading my own scorecard and asking why a hard case looked perfect. A report
that flatters itself is worse than no report, so that case is now excluded from
the extraction scores and covered by a real integration test instead.

**It planned around a model that does not exist.** The design named a Groq
model the account cannot serve. That is why I asked for a boot-time check
against each provider's own model list, and the same check later caught a wrong
Gemini identifier. It caught a third case after that: OpenAI's `gpt-5-mini`
appears in this account's model listing and returns 404 on use, because the
organisation is unverified. A listing is not an entitlement.

**It let a re-run double the ledger.** Meetings are keyed on the transcript
hash, so submitting the same text twice correctly reused the meeting and
incorrectly appended a second copy of everything under it. I found it by doing
the first thing any reviewer would do: clicking the same sample twice.

**It silently truncated long extractions.** No output token ceiling was set, so
a long meeting's extraction was cut off mid-object and came back as a 400
reading "Failed to validate JSON" with an empty payload, which points at the
prompt and is not the prompt. I diagnosed it by raising the ceiling and
watching the same model on the same transcript return all seven commitments.

**It duplicated promises across meetings.** The Historian updated the original
row and extraction wrote this meeting's copy anyway, so one obligation became
two, and the next meeting would report each as slipping independently. I caught
it on the person view, where the duplicates are obvious in a way they are not
in a global table.

---

## What I did not delegate

**Judgment about what counts as done.** Every claim in this repository is
backed by something that runs: a test, a live endpoint, or a number in the
evaluation report. Where something is not measured, the README says so.

**The honest parts.** The known limitations, the failure gallery in the
evaluation, and this file are deliberately not flattering. A submission that
only reports its wins is asking to be taken on trust, and the entire argument of
this system is that trust should be checkable.

**Deciding when the lazy answer was the right one.** Plenty of suggested
abstractions did not survive: an interface with one implementation, a
classifier where a substring search was exact, an LLM in a place where
arithmetic was correct and free. Rejecting good-sounding complexity is most of
what senior engineering is, and no model does it for you.
