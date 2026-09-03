# 2. Grounding is a gate, not an instruction

**Status:** accepted
**Date:** 2026-08-29

## Context

The failure that makes an extraction system worthless is a confident
fabrication: a commitment nobody made, with an owner and a date, sitting in a
list that looks exactly like the true rows. Prompting is the usual answer. "Only
use text from the transcript."

## Decision

Every extracted item must carry a verbatim quote, and a deterministic verifier
finds that quote in the source or the item does not exist. Exact match first,
then whitespace-normalised, then a bounded offset repair. Anything still
unfound is recorded as a rejection with a reason, not dropped silently.

## Why not a prompt

A prompt is a request. A model that has already decided the meeting implied
something will phrase it as though it were said. The gate does not care what the
model decided: the string is in the transcript or it is not, and that question
has an exact answer that costs nothing to compute.

## Consequences

Hallucinated commitments cannot reach the database, which is a property of the
system rather than a property of a good day.

Character offsets are real because they were computed rather than reported, so
the UI can highlight the exact sentence behind any row.

Grounding fidelity becomes measurable, and the pre-validation rejection rate
says how often the gate earns its keep.

The cost is recall. An item whose quote the model paraphrased is thrown away
even when the underlying commitment was real. That is the right side to err on:
a missing row is visible to the person who made the promise, and a fabricated
one is not.
