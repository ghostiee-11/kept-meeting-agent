# 3. Abstain rather than guess, and ask a specific question

**Status:** accepted
**Date:** 2026-08-30

## Context

Schemas want values. An owner field and a due date field invite a model to fill
them, and a model asked for an owner will produce one. Three people in a room
answer to "Alex" and the transcript says only "Alex will take it".

## Decision

No agent invents an owner or a date. When the roster lookup is ambiguous or the
date cannot be resolved, the agent abstains and raises a specific, answerable
question carrying the candidates it already considered and the evidence it saw.
A human answers it in one click, and the answer is written to the ledger with
`actor_kind=human` on every event it produces.

## Why

A wrong owner is worse than a blank one. A blank field is visibly incomplete and
somebody fixes it. A wrong field looks handled: it becomes a task the real owner
never sees, and nobody chases it, and the meeting's decision quietly does not
happen.

This also shapes the evaluation. Correct abstention is scored as a success in
its own column, because a scorecard that only counts correct answers would mark
a refusal as a miss and push the system towards guessing, which is the exact
failure this decision exists to prevent.

## Consequences

The system asks questions, so there has to be somewhere to answer them, which
is a UI surface that would not otherwise exist.

Resolution is deliberately not a graph resume. The run that raised the question
has already finished and persisted everything it knew; replaying a whole
multi-agent pipeline to write one owner would spend a run's budget to apply a
fact a human just supplied.

"Who decided this" is always answerable afterwards, because every event carries
whether it came from a person or an agent.
