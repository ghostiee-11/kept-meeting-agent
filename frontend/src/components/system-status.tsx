"use client";

import { useEffect, useState } from "react";
import { apiFetch, type Health } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

type State =
  | { phase: "connecting" }
  | { phase: "waking" }
  | { phase: "ready"; health: Health }
  | { phase: "unreachable"; reason: string };

/** Render's free instance sleeps after 15 minutes. Past this, say so out loud. */
const COLD_START_HINT_MS = 3_000;

const TONE = {
  ok: "bg-kept",
  degraded: "bg-pending",
  down: "bg-debt",
} as const;

export function SystemStatus({ compact = false }: { compact?: boolean }) {
  const [state, setState] = useState<State>({ phase: "connecting" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const coldStart = setTimeout(() => {
      if (!cancelled) setState({ phase: "waking" });
    }, COLD_START_HINT_MS);

    apiFetch<Health>("/health")
      .then((health) => {
        if (!cancelled) setState({ phase: "ready", health });
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setState({
            phase: "unreachable",
            reason: cause instanceof Error ? cause.message : "Unknown error",
          });
        }
      })
      .finally(() => clearTimeout(coldStart));

    return () => {
      cancelled = true;
      clearTimeout(coldStart);
    };
  }, [attempt]);

  if (state.phase === "connecting") {
    return <Skeleton className="h-4 w-40" />;
  }

  if (state.phase === "waking") {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="t-data text-paper-muted flex items-center gap-2">
            <span className="bg-pending size-1.5 animate-pulse rounded-full" />
            waking the backend
          </span>
        </TooltipTrigger>
        <TooltipContent>
          The API runs on a free instance that sleeps after 15 minutes idle. The
          first request wakes it, which takes up to a minute.
        </TooltipContent>
      </Tooltip>
    );
  }

  if (state.phase === "unreachable") {
    return (
      <span className="t-data flex items-center gap-2">
        <span className={`${TONE.down} size-1.5 rounded-full`} />
        <span className="text-debt">API unreachable</span>
        <button
          onClick={() => {
            setState({ phase: "connecting" });
            setAttempt((n) => n + 1);
          }}
          className="text-paper-muted hover:text-paper underline underline-offset-2"
        >
          retry
        </button>
      </span>
    );
  }

  const { health } = state;
  const models = Object.entries(health.providers)
    .filter(([, on]) => on)
    .map(([name]) => name);

  const summary = compact
    ? `${models.join(" · ") || "no models"}${health.web_search ? " · search" : ""}`
    : health.environment;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="t-data text-paper-muted flex items-center gap-2">
          <span
            className={`${TONE[health.status === "ok" ? "ok" : "degraded"]} size-1.5 rounded-full`}
          />
          {summary}
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        <span className="block">
          {health.environment} · build {health.version} ·{" "}
          {health.database ? "database up" : "no database"} ·{" "}
          {health.auth_enforced ? "keyed" : "open"}
        </span>
        {health.notes.map((note) => (
          <span key={note} className="text-pending mt-1 block">
            {note}
          </span>
        ))}
      </TooltipContent>
    </Tooltip>
  );
}
