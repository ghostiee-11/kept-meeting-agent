"use client";

import { useEffect, useState } from "react";
import { API_URL, apiFetch, type Health } from "@/lib/api";

type State =
  | { phase: "connecting" }
  | { phase: "waking" }
  | { phase: "ready"; health: Health }
  | { phase: "unreachable"; reason: string };

/** Render free spins down after 15 minutes idle. Past this, say so out loud. */
const COLD_START_HINT_MS = 3_000;

export function SystemStatus() {
  const [state, setState] = useState<State>({ phase: "connecting" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const coldStartTimer = setTimeout(() => {
      if (!cancelled) setState({ phase: "waking" });
    }, COLD_START_HINT_MS);

    apiFetch<Health>("/health")
      .then((health) => {
        if (!cancelled) setState({ phase: "ready", health });
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setState({
            phase: "unreachable",
            reason: error instanceof Error ? error.message : "Unknown error",
          });
        }
      })
      .finally(() => clearTimeout(coldStartTimer));

    return () => {
      cancelled = true;
      clearTimeout(coldStartTimer);
    };
  }, [attempt]);

  if (state.phase === "unreachable") {
    return (
      <div className="border-rule bg-surface border">
        <Bar tone="debt" label="Unreachable" detail={API_URL} />
        <div className="flex items-center justify-between gap-4 px-4 py-3">
          <p className="t-data text-paper-muted truncate">{state.reason}</p>
          <button
            onClick={() => {
              setState({ phase: "connecting" });
              setAttempt((n) => n + 1);
            }}
            className="border-rule-strong text-paper hover:bg-surface-raised shrink-0 border px-3 py-1.5 text-[0.8125rem] font-medium transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (state.phase !== "ready") {
    return (
      <div className="border-rule bg-surface border">
        <Bar
          tone="idle"
          label={state.phase === "waking" ? "Waking" : "Connecting"}
          detail={state.phase === "waking" ? "free instance, up to 60s" : ""}
        />
        <Grid>
          {["Env", "Build", "Models", "Search", "Database", "Auth"].map((k) => (
            <Cell key={k} label={k} value="—" />
          ))}
        </Grid>
      </div>
    );
  }

  const { health } = state;
  const models = Object.entries(health.providers)
    .filter(([, on]) => on)
    .map(([name]) => name);

  return (
    <div className="border-rule bg-surface border">
      <Bar
        tone={health.status === "ok" ? "kept" : "pending"}
        label={health.status === "ok" ? "Operational" : "Degraded"}
        detail={`${Math.round(health.uptime_seconds)}s uptime`}
      />
      <Grid>
        <Cell label="Env" value={health.environment} />
        <Cell label="Build" value={health.version} />
        <Cell
          label="Models"
          value={models.length ? models.join(" · ") : "none"}
          tone={models.length ? undefined : "debt"}
        />
        <Cell
          label="Search"
          value={health.web_search ? "tavily" : "fallback"}
        />
        <Cell label="Database" value={health.database ? "neon" : "none"} />
        <Cell label="Auth" value={health.auth_enforced ? "keyed" : "open"} />
      </Grid>
    </div>
  );
}

const TONE = {
  kept: "bg-kept",
  debt: "bg-debt",
  pending: "bg-pending",
  idle: "bg-paper-muted animate-pulse",
} as const;

function Bar({
  tone,
  label,
  detail,
}: {
  tone: keyof typeof TONE;
  label: string;
  detail?: string;
}) {
  return (
    <div className="border-rule flex items-center gap-2.5 border-b px-4 py-3">
      <span className={`size-1.5 rounded-full ${TONE[tone]}`} />
      <span className="t-heading text-[0.9375rem]">{label}</span>
      {detail && (
        <span className="t-data text-paper-muted ml-auto">{detail}</span>
      )}
    </div>
  );
}

function Grid({ children }: { children: React.ReactNode }) {
  return (
    <dl className="border-rule grid grid-cols-2 gap-px border-t sm:grid-cols-3">
      {children}
    </dl>
  );
}

function Cell({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "debt";
}) {
  return (
    <div className="bg-surface outline-rule px-4 py-3 outline outline-offset-0">
      <dt className="t-eyebrow">{label}</dt>
      <dd
        className={`t-data mt-1 truncate ${tone === "debt" ? "text-debt" : "text-paper"}`}
      >
        {value}
      </dd>
    </div>
  );
}
