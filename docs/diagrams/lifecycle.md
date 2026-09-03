# Commitment lifecycle

```mermaid
stateDiagram-v2
    [*] --> extracted: Analyst finds it,<br/>Verifier grounds the quote

    extracted --> needs_clarification: owner or date unresolved
    extracted --> confirmed: owner and date both settled

    needs_clarification --> confirmed: a human answers<br/><i>actor_kind = human</i>
    needs_clarification --> dropped: dismissed as not needed

    confirmed --> in_progress: mentioned as progressing<br/>in a later meeting
    confirmed --> done: reported complete
    in_progress --> done: reported complete
    in_progress --> confirmed: promised again with a new date<br/><i>slip_count + 1</i>

    confirmed --> dropped: descoped, with agreement
    in_progress --> dropped: descoped, with agreement

    done --> [*]
    dropped --> [*]
```

**Overdue, at risk and slipped are not states.** They are arithmetic over the
due date, the slip count and the silence streak, recomputed on every read. A
status written down would start going stale the moment a day passed. See
[ADR 4](../adr/0004-computed-not-stored.md).

Every transition appends to `commitment_events`, which is never updated and
never deleted, and carries whether a person or an agent caused it. That log is
what the timeline in the console renders.

```mermaid
flowchart LR
    M1["Meeting 1<br/>promised, due Tue"] --> M2["Meeting 2<br/>promised again, due Fri<br/><b>slip 1</b>"]
    M2 --> M3["Meeting 3<br/>nobody mentions it<br/><b>silence streak 1</b>"]
    M3 --> RISK["Risk rises on both counts,<br/>nightly sweep drafts a nudge"]
```

Silence is the signal no summarizer produces: there is nothing in the
transcript to summarise, because the promise is precisely what nobody said.
