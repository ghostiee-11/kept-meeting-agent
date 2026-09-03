# 6. Neon rather than Render's free Postgres

**Status:** accepted
**Date:** 2026-08-28

## Context

The backend runs on Render's free tier. Render offers a free Postgres instance
in the same dashboard, which is the path of least resistance.

## Decision

The database is Neon.

## Why

Render's free Postgres is deleted 30 days after creation. The submission is a
link somebody will open at a time I do not control, and the failure mode of that
choice is the demo returning 500s during review, weeks after I stopped looking
at it.

Neon's free tier has no expiry, scales to zero, and supports pgvector, which the
ledger uses.

## Consequences

One more service to configure, and a connection string that has to be kept out
of git.

Neon scale-to-zero adds a cold start of its own on the first query after idle,
which compounds with Render's 15-minute spin-down. Both are surfaced in the UI
as "waking the backend" rather than hidden behind a spinner, because an honest
wait beats one that looks broken.

Pooling is sized for Render's 0.1 CPU and 512MB: a single uvicorn worker and a
small pool, because the fastest way to exhaust a free Postgres connection limit
is to open a pool per request.
