"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { AgentWaterfall, type Bar } from "@/components/agent-waterfall";
import { SAMPLES } from "@/lib/samples";
import { ApiError, streamRun, type RunEvent } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Finished {
  runId: string;
  summary: string;
  counts: Record<string, number>;
  costUsd: number;
  tokens: { in: number; out: number };
}

export function RunConsole() {
  const [transcript, setTranscript] = useState("");
  const [title, setTitle] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [bars, setBars] = useState<Bar[]>([]);
  const [active, setActive] = useState<Set<string>>(new Set());
  const [reports, setReports] = useState<string[]>([]);
  const [seen, setSeen] = useState<string[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const [finished, setFinished] = useState<Finished | null>(null);

  const abort = useRef<AbortController | null>(null);
  const startedAt = useRef(0);
  const ticker = useRef<ReturnType<typeof setInterval> | null>(null);

  function reset() {
    setBars([]);
    setActive(new Set());
    setReports([]);
    setSeen([]);
    setFinished(null);
    setError(null);
    setElapsed(0);
  }

  async function run() {
    if (!transcript.trim() || running) return;

    reset();
    setRunning(true);
    startedAt.current = Date.now();
    ticker.current = setInterval(
      () => setElapsed(Date.now() - startedAt.current),
      250,
    );

    const controller = new AbortController();
    abort.current = controller;
    // Started-at per agent, so a bar can be placed and sized when its call
    // completes. The stream reports duration, not start time.
    const openedAt = new Map<string, number>();

    try {
      for await (const event of streamRun(
        { transcript, title: title.trim() || "Untitled meeting" },
        controller.signal,
      )) {
        apply(event, openedAt);
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(
          cause instanceof ApiError
            ? `The run failed (${cause.status}). ${cause.message.slice(0, 200)}`
            : cause instanceof Error
              ? cause.message
              : "The run failed.",
        );
      }
    } finally {
      if (ticker.current) clearInterval(ticker.current);
      setRunning(false);
      setActive(new Set());
    }
  }

  function apply(event: RunEvent, openedAt: Map<string, number>) {
    const now = Date.now() - startedAt.current;

    // Every agent that appears in any event gets a lane, not only those that
    // called a model. The Attributor and Chronos usually resolve everything by
    // arithmetic, and an empty lane is the evidence for that rather than an
    // omission.
    const agent = (event as { agent?: string }).agent;
    if (agent) {
      setSeen((current) =>
        current.includes(agent) ? current : [...current, agent],
      );
    }

    switch (event.type) {
      case "model_call_started":
        openedAt.set(event.agent, now);
        setActive((current) => new Set(current).add(event.agent));
        break;

      case "model_call": {
        const started = openedAt.get(event.agent) ?? now - event.latency_ms;
        openedAt.delete(event.agent);
        setBars((current) => [
          ...current,
          {
            agent: event.agent,
            startMs: started,
            durationMs: event.latency_ms,
            provider: event.provider,
            model: event.model,
            costUsd: event.cost_usd,
            tokensOut: event.tokens_out,
          },
        ]);
        setActive((current) => {
          const next = new Set(current);
          next.delete(event.agent);
          return next;
        });
        break;
      }

      case "grounding_retry":
        setReports((current) => [
          ...current,
          `${event.agent}: ${event.count} quote${event.count === 1 ? "" : "s"} were not in the transcript, asking again`,
        ]);
        break;

      case "team_report":
        setReports((current) => [...current, event.line]);
        break;

      case "error":
        setBars((current) => [
          ...current,
          {
            agent: event.agent,
            startMs: openedAt.get(event.agent) ?? now,
            durationMs: 400,
            provider: null,
            model: null,
            costUsd: 0,
            tokensOut: 0,
            failed: true,
          },
        ]);
        break;

      case "run_finished":
        setFinished({
          runId: event.run_id,
          summary: event.summary,
          counts: event.counts,
          costUsd: event.cost_usd,
          tokens: event.tokens,
        });
        break;

      case "run_failed":
        setError(event.error);
        break;
    }
  }

  const cost = bars.reduce((total, bar) => total + bar.costUsd, 0);

  return (
    <div className="grid min-h-[calc(100dvh-4.5rem)] grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      <section className="border-rule flex flex-col border-b lg:border-r lg:border-b-0">
        <div className="border-rule flex items-center justify-between gap-3 border-b px-6 py-3">
          <p className="t-eyebrow">Transcript</p>
          <div className="flex gap-1.5">
            {SAMPLES.map((sample) => (
              <Button
                key={sample.title}
                variant="outline"
                size="sm"
                disabled={running}
                onClick={() => {
                  setTranscript(sample.transcript);
                  setTitle(sample.title);
                  reset();
                }}
                className="h-7 text-[0.75rem]"
              >
                {sample.label}
              </Button>
            ))}
          </div>
        </div>

        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="Meeting title"
          aria-label="Meeting title"
          className="border-rule placeholder:text-paper-muted border-b bg-transparent px-6 py-2.5 text-[0.9375rem] font-medium outline-none"
        />

        <Textarea
          value={transcript}
          onChange={(event) => setTranscript(event.target.value)}
          placeholder={
            "Paste a transcript, or pick a sample above.\n\nPriya: I'll have the migration plan ready by Friday.\nAdit: Someone should look at the caching layer at some point."
          }
          aria-label="Meeting transcript"
          spellCheck={false}
          className="t-data min-h-[22rem] flex-1 resize-none rounded-none border-0 bg-transparent px-6 py-4 leading-relaxed shadow-none focus-visible:ring-0"
        />

        <div className="border-rule flex items-center justify-between gap-4 border-t px-6 py-3">
          <span className="t-data text-paper-muted">
            {transcript
              ? `${transcript.split("\n").filter(Boolean).length} lines`
              : ""}
          </span>
          <Button
            onClick={run}
            disabled={running || !transcript.trim()}
            size="sm"
          >
            {running ? "Running" : "Run the team"}
          </Button>
        </div>

        {error && (
          <p
            role="alert"
            className="text-debt border-debt/40 bg-debt/5 border-t px-6 py-3 text-[0.8125rem]"
          >
            {error}
          </p>
        )}
      </section>

      <section className="flex min-w-0 flex-col">
        <div className="border-rule flex items-baseline justify-between gap-3 border-b px-5 py-3">
          <p className="t-eyebrow">The team</p>
          <span className="t-data text-paper-muted">
            {running || bars.length > 0
              ? `${(elapsed / 1000).toFixed(1)}s · $${cost.toFixed(4)}`
              : ""}
          </span>
        </div>

        <AgentWaterfall
          bars={bars}
          agents={seen}
          active={active}
          elapsedMs={elapsed}
        />

        {reports.length > 0 && (
          <ol className="border-rule border-t px-5 py-3">
            {reports.map((line, index) => (
              <li
                key={index}
                className={cn(
                  "t-data py-0.5 leading-relaxed",
                  line.includes("WARNING") ? "text-pending" : "text-paper-dim",
                )}
              >
                {line}
              </li>
            ))}
          </ol>
        )}

        {finished && <Outcome finished={finished} />}
      </section>
    </div>
  );
}

function Outcome({ finished }: { finished: Finished }) {
  const entries: Array<[string, number]> = [
    ["Decisions", finished.counts.decisions ?? 0],
    ["Commitments", finished.counts.commitments ?? 0],
    ["Rejected", finished.counts.rejections ?? 0],
    ["Questions", finished.counts.clarifications ?? 0],
  ];

  return (
    <div className="border-rule mt-auto border-t">
      <dl className="border-rule grid grid-cols-4 border-b">
        {entries.map(([label, value]) => (
          <div
            key={label}
            className="border-rule border-r px-5 py-3 last:border-r-0"
          >
            <dt className="t-eyebrow">{label}</dt>
            <dd className="t-data mt-0.5 text-[1.125rem]">{value}</dd>
          </div>
        ))}
      </dl>

      <div className="px-5 py-4">
        <p className="text-paper-dim text-[0.8125rem] leading-relaxed">
          {finished.summary}
        </p>
        <div className="mt-3 flex items-center gap-4">
          <Button asChild size="sm" variant="outline">
            <Link href="/execution">See who owes what</Link>
          </Button>
          <span className="t-data text-paper-muted">
            ${finished.costUsd.toFixed(5)} · {finished.tokens.in}/
            {finished.tokens.out} tokens
          </span>
        </div>
      </div>
    </div>
  );
}
