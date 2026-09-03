# 1. An LLM supervisor on top, deterministic edges underneath

**Status:** accepted
**Date:** 2026-08-29

## Context

The work decomposes into jobs with different shapes: segmenting a transcript,
extracting candidates, challenging them, resolving a person, resolving a date,
reconciling against history, creating tasks. Something has to decide what runs
when.

Three topologies were available. A fixed pipeline. A full swarm, where agents
hand off peer to peer with no coordinator. Or a supervisor that routes.

## Decision

A supervisor agent routes between four teams and decides only the questions
that are genuinely open: re-extract when the Verifier and Skeptic threw out
most of a batch, escalate or proceed when owners could not be settled, stop
early when a meeting contains nothing. Inside each team, flow is deterministic
graph edges.

## Why not a fixed pipeline

It cannot replan. When the Verifier rejects half a batch, something has to
decide between re-extracting with the rejection reasons attached and proceeding
with what survived. A hardcoded threshold is a worse version of that decision,
not a simpler one.

## Why not a swarm

Termination is hard to guarantee, cost is unbounded, and the trace becomes
unreadable at exactly the moment you need it. The point of naming agents is
being able to say which one was wrong.

## Why deterministic inside a team

The order is known in advance. The Attributor runs before Chronos because a
deadline resolved for the wrong person is wasted work, and no model call is
needed to discover that. LLM routing there would buy nondeterminism and tokens
and return nothing.

## Consequences

The supervisor is the only component whose behaviour is not reproducible, which
makes it the first suspect when a run looks wrong, and it is bounded by a tool
call limit so it cannot loop.

It also turned out to need a floor. A small model occasionally returns without
routing while work remains, so the graph advances anyway and records that it
overrode the decision. An agent that abandons two thirds of the work when a
model loses its place is not something to deploy.

`langgraph-supervisor` was not used. It is pre-1.0, and the handoff is forty
lines on core `Command(goto=..., graph=Command.PARENT)`, which is stable API. A
pre-1.0 dependency in the critical path of a system meant to be reviewed is a
bad trade.
