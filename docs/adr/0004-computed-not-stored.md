# 4. Risk and overdue are computed on read, never stored

**Status:** accepted
**Date:** 2026-08-31

## Context

A commitment has a risk score and a lifecycle. The obvious design gives the
table a `risk_score` column and a status enum including `overdue`.

## Decision

`CommitmentStatus` carries only states a human or an agent actually set:
extracted, needs_clarification, confirmed, in_progress, done, dropped. Overdue,
at risk and slipped are arithmetic over the due date, slip count and silence
streak, computed on every read. Risk is a pure function over data already
loaded.

## Why

A stored score is what the score was on the day of the run. A commitment that
was fine on Tuesday is overdue by Friday without anything having been written to
it, so a stored value starts going stale the moment it is written, and every
read has to wonder how old it is.

Computing is free here: scoring is a weighted sum over fields the query already
fetched. There is no performance argument for caching it.

## Consequences

Risk is always correct as of now, and the same row can be read a week apart and
give two different answers, which is the point.

The scoring function is testable, and a weight change shows up as a diff in a
test rather than as a vibe.

It is explainable. The UI shows the per-factor contributions, so somebody told
their commitment is at risk can see which part is the problem. An LLM-produced
number cannot do that.

Weighting had to be set deliberately: evidence of trouble outranks gaps in
extraction. Slippage, silence and lateness have happened; a missing owner is
something the system does not yet know. An earlier cut had it the other way
around and scored a week-late, twice-moved commitment below one that was merely
unowned.
