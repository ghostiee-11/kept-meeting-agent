# Demo script

Five minutes, one continuous take. The structure is deliberate: show the
failure the product exists to fix, then show the system catching it, then show
the receipts.

Nothing here needs to be staged. Every beat runs against the deployed app with
the transcripts in `evals/gold/` and the samples built into the console.

---

## 0:00 — The problem, in one sentence

> "Meetings produce two things: information and obligation. Information
> survives in notes. Obligation evaporates. This is a system that keeps it."

On screen: the console, empty.

---

## 0:20 — Paste a transcript, watch a team work

Paste the launch-sync sample. Click **Run the team**.

Talk over the waterfall while it fills:

> "Ten agents across four teams. The Analyst extracts generously, a Skeptic
> running a different model attacks every candidate, and a verifier that is
> plain code throws out anything whose quote is not literally in the
> transcript. That last one is a gate, not a prompt instruction, which is why a
> hallucinated commitment cannot reach the database."

Point at the provider and latency on a bar as it lands.

> "Every call is recorded: which agent, which provider, how long, what it
> cost."

---

## 1:20 — What it found, and what it refused to find

Click **See what the team found**.

**Commitments tab.** Point at one row's evidence quote.

> "Seven obligations, each with the sentence that produced it."

Then the harder point:

> "It did *not* extract four things: somebody refusing work, a two-day estimate
> that was a forecast rather than a promise, something another team said they
> would handle, and a rhetorical question. A summarizer turns all four into
> action items."

**Rejected tab.**

> "Here is what the Skeptic threw out, with the reason. You can check it
> against the transcript in ten seconds. That is the difference between a
> system with judgment and a system with confidence."

---

## 2:00 — The thing it would not guess

**Questions** in the sidebar.

> "Two people were named in the room but neither accepted this. The system
> refused to pick one. A wrong owner is worse than a blank one: it becomes a
> task the real owner never sees."

Answer one, in one click.

> "Answered. That goes into the ledger with a human recorded as the source, not
> a model. Every event carries whether a person or an agent caused it."

---

## 2:40 — Memory across meetings

Back to **Run**, paste the follow-up transcript, run it.

When the history line appears:

> "This is the part no summarizer does. It takes every still-open promise and
> asks what this meeting said about it. One slipped for the second time. One
> was completed, and notice it was completed by a status report, not a new
> promise, which is exactly the case the naive version misses. And one nobody
> mentioned at all."

Land the point:

> "Silence is the strongest predictor of failure here, and there is nothing in
> the transcript to summarise, because the promise is precisely what nobody
> said."

---

## 3:20 — One person's ledger

**People**, then click an owner.

> "Their promises on a calendar, the ones with no date underneath, because
> undated work is where things disappear, and the tasks that actually left the
> system with real IDs."

Open a row's history in **Execution**:

> "The full trail across meetings. Promised Tuesday, moved to Friday, moved
> again. That is an argument nobody can wave away."

---

## 4:00 — Show the receipts

**Ops**.

> "Every run, every agent, cost and latency per call, and which provider
> actually served it after a fallback."

Then, briefly, the repo:

> "`docs/EVALUATION.md` has precision, recall, correct abstention scored as its
> own success, an adversarial suite including prompt injection, and an ablation
> against a single-prompt baseline, so the claim that a team beats one prompt
> is measured rather than asserted. It also carries a failure gallery, because
> a scorecard with no failures on it is not evidence."

---

## 4:40 — Close on the honest note

> "Known limitations are in the README rather than left for you to find:
> free-tier fallbacks are slower, extraction varies between providers, and
> emails are drafted and never sent, because a system that writes on your
> behalf should have to be told to send."

Last line:

> "Meetings make promises. This makes them accountable."

---

## Practical notes

- **Wake the backend first.** Render's free tier sleeps after fifteen minutes
  and takes 30 to 60 seconds to start. Load `/health` before recording.
- **Have the follow-up transcript on the clipboard** before the run finishes,
  so 2:40 is not spent scrolling.
- **Do not narrate the waiting.** If a run is slow, keep talking about what the
  agents are doing; the waterfall is showing it.
- If a run fails live, say so and show the fallback in Ops. A recovery is a
  better demo than a clean run.
