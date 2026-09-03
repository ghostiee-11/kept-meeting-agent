# 7. Pay for the reasoning tier, keep the free chains underneath

**Status:** accepted
**Date:** 2026-09-03

## Context

Everything ran on free tiers: Groq for reasoning, Gemini as a fallback. Runs
took several minutes and it was not obvious why.

## Decision

Measure first. Over a full run: **113 seconds in models, 286 seconds waiting**
for Groq's per-minute token window to roll over. The system was not slow, it was
queued.

So the reasoning and fast tiers now resolve to OpenAI first, with Groq and
Gemini still behind them in every chain.

## Which models, and why those

`gpt-5.4-mini` for reasoning at $0.75 and $4.50 per million tokens, which works
out at roughly two and a half cents for a short meeting and nine for a long one.

`gpt-5.4-nano` for routing and drafting, where the work is a few hundred tokens
of structured decision. Deliberately not `gpt-5-nano`, which is cheaper per
token and slower in practice: it spent 64 reasoning tokens deciding to answer
"ok", and the supervisor makes a dozen such calls per run.

Not `gpt-5-mini`, which would have been the value pick. It appears in this
account's model listing and returns 404 on use, because the organisation is
unverified. Every identifier in the registry was called once before being
written down.

## Consequences

Rate-limit waiting went to zero and extraction went from roughly thirty seconds
per brief to under three.

The token budget now paces only providers that impose a per-minute ceiling. It
was written for Groq's free tier, and applying it to a paid provider made the
system wait for a limit that provider does not impose.

The system still runs with no paid key at all, because the chains are the
point. It is simply slower, and `/health` says which providers are live.

Cost is now a real number rather than zero, so it is reported per run in the
console and per case in the evaluation.
