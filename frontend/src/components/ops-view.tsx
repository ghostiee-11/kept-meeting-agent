"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiFetch, type Run, type Task } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface RunSummary {
  id: string;
  meeting_title: string;
  status: string;
  cost_usd: number;
  tokens_in: number;
  tokens_out: number;
  error: string | null;
  created_at: string;
  agents: number;
}

/**
 * The trust panel.
 *
 * Everything here exists so a claim in the README can be checked rather than
 * believed: which agents ran, which model actually answered each call, how long
 * it took, and whether the tasks really landed in a tracker.
 */
export function OpsView() {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    Promise.all([
      apiFetch<RunSummary[]>("/runs?limit=15"),
      apiFetch<Task[]>("/mock/v1/tasks?limit=20"),
    ])
      .then(([runList, taskList]) => {
        if (cancelled) return;
        setRuns(runList);
        setTasks(taskList);
        if (runList.length > 0) setSelected(runList[0].id);
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "Could not load.");
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;

    apiFetch<Run>(`/runs/${selected}`)
      .then((data) => {
        if (!cancelled) setRun(data);
      })
      .catch(() => undefined);

    return () => {
      cancelled = true;
    };
  }, [selected]);

  if (error) {
    return (
      <p role="alert" className="text-debt px-8 py-6 text-[0.8125rem]">
        {error}
      </p>
    );
  }

  if (runs === null) {
    return (
      <div className="space-y-px px-8 py-4">
        {Array.from({ length: 6 }).map((_, index) => (
          <Skeleton key={index} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  if (runs.length === 0) {
    return (
      <div className="px-8 py-16 text-center">
        <p className="text-paper-dim text-[0.9375rem]">No runs yet.</p>
        <Button asChild variant="outline" size="sm" className="mt-4">
          <Link href="/">Run a transcript</Link>
        </Button>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
      <section className="border-rule border-b xl:border-r xl:border-b-0">
        <p className="t-eyebrow border-rule border-b px-8 py-2">Runs</p>
        {runs.map((item) => (
          <button
            key={item.id}
            onClick={() => setSelected(item.id)}
            className={cn(
              "border-rule flex w-full items-baseline justify-between gap-4 border-b px-8 py-2.5 text-left transition-colors",
              selected === item.id ? "bg-surface-raised" : "hover:bg-surface",
            )}
          >
            <span className="min-w-0 flex-1 truncate text-[0.8125rem]">
              {item.meeting_title}
            </span>
            <span className="t-data text-paper-muted shrink-0">
              {item.agents} agents · {item.tokens_in + item.tokens_out} tokens
            </span>
            <span
              className={cn(
                "t-data w-20 shrink-0 text-right",
                item.status === "succeeded"
                  ? "text-kept"
                  : item.status === "failed"
                    ? "text-debt"
                    : "text-pending",
              )}
            >
              {item.status}
            </span>
          </button>
        ))}

        <p className="t-eyebrow border-rule mt-6 border-y px-8 py-2">
          Task board
        </p>
        {tasks?.length === 0 && (
          <p className="text-paper-muted px-8 py-4 text-[0.8125rem]">
            No tasks yet. A task is created for every owned commitment.
          </p>
        )}
        {tasks?.map((task) => (
          <div
            key={task.id}
            className="border-rule odd:bg-band flex items-baseline gap-3 border-b px-8 py-2"
          >
            <span className="t-data text-paper-muted w-16 shrink-0">
              {task.id}
            </span>
            <span className="min-w-0 flex-1 truncate text-[0.8125rem]">
              {task.title}
            </span>
            <span className="t-data text-paper-dim shrink-0">
              {task.assignee ?? "—"}
            </span>
          </div>
        ))}
      </section>

      <section className="min-w-0">
        <p className="t-eyebrow border-rule border-b px-6 py-2">
          Agent trace{run ? ` · ${run.trace.length} events` : ""}
        </p>
        {run === null ? (
          <div className="space-y-px px-6 py-4">
            {Array.from({ length: 10 }).map((_, index) => (
              <Skeleton key={index} className="h-6 w-full" />
            ))}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-rule border-b">
                  <Th>#</Th>
                  <Th>Agent</Th>
                  <Th>Event</Th>
                  <Th>Model</Th>
                  <Th className="text-right">Tokens</Th>
                  <Th className="text-right">ms</Th>
                </tr>
              </thead>
              <tbody>
                {run.trace.map((entry) => (
                  <tr
                    key={entry.seq}
                    className="border-rule odd:bg-band border-b"
                  >
                    <td className="t-data text-paper-muted py-1.5 pl-6">
                      {entry.seq}
                    </td>
                    <td className="t-data px-2 py-1.5">{entry.agent}</td>
                    <td
                      className={cn(
                        "t-data px-2 py-1.5",
                        entry.event === "error"
                          ? "text-debt"
                          : entry.event === "handoff"
                            ? "text-kept"
                            : "text-paper-dim",
                      )}
                    >
                      {entry.event}
                    </td>
                    <td className="t-data text-paper-muted max-w-40 truncate px-2 py-1.5">
                      {entry.model ?? "—"}
                    </td>
                    <td className="t-data text-paper-dim px-2 py-1.5 text-right">
                      {entry.tokens_in || entry.tokens_out
                        ? `${entry.tokens_in}/${entry.tokens_out}`
                        : "—"}
                    </td>
                    <td className="t-data text-paper-dim py-1.5 pr-6 text-right">
                      {entry.latency_ms || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function Th({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <th
      scope="col"
      className={cn(
        "t-eyebrow px-2 py-2 text-left first:pl-6 last:pr-6",
        className,
      )}
    >
      {children}
    </th>
  );
}
