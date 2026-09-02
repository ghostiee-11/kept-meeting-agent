"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { apiFetch, type Commitment, type PersonLedger } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * One person's promises, on a calendar.
 *
 * Every other view here is organised by meeting, and nobody thinks about
 * their own work that way. They think "what have I said I would do, and what
 * is late", which is a question about dates. So the month is the layout, and
 * the two things a date cannot show, promises with no deadline and the tasks
 * that actually left the system, sit underneath it.
 */
export function PersonLedgerView({ personId }: { personId: string }) {
  const [data, setData] = useState<PersonLedger | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [monthStart, setMonthStart] = useState(() => startOfMonth(new Date()));

  useEffect(() => {
    let cancelled = false;
    apiFetch<PersonLedger>(`/people/${personId}`)
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "Could not load.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [personId]);

  const byDate = useMemo(() => {
    const map = new Map<string, Commitment[]>();
    for (const row of data?.commitments ?? []) {
      if (!row.due_date) continue;
      const existing = map.get(row.due_date);
      if (existing) existing.push(row);
      else map.set(row.due_date, [row]);
    }
    return map;
  }, [data]);

  if (error) {
    return (
      <p role="alert" className="text-debt px-8 py-6 text-[0.8125rem]">
        {error}
      </p>
    );
  }

  if (!data) {
    return (
      <div className="space-y-2 px-8 py-6">
        <Skeleton className="h-6 w-56" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  const undated = data.commitments.filter((row) => !row.due_date);

  return (
    <div className="px-8 py-6">
      <div className="flex flex-wrap items-baseline justify-between gap-4">
        <div>
          <h2 className="t-heading">{data.person.name}</h2>
          <p className="text-paper-muted mt-0.5 text-[0.8125rem]">
            {data.person.role ?? "no role recorded"} · also called{" "}
            {data.person.aliases.join(", ") || "nothing else"}
          </p>
          {data.person.source === "transcript" && (
            <p className="text-pending mt-1 text-[0.75rem]">
              Enrolled from a meeting they spoke in. Nobody has confirmed the
              spelling or the role yet.
            </p>
          )}
        </div>
        <dl className="flex gap-6">
          <Stat label="Open" value={data.person.open_items} />
          <Stat label="Overdue" value={data.person.overdue} tone="debt" />
          <Stat label="At risk" value={data.person.at_risk} tone="pending" />
        </dl>
      </div>

      <div className="mt-6 flex items-center justify-between">
        <p className="t-eyebrow">
          {monthStart.toLocaleString(undefined, {
            month: "long",
            year: "numeric",
          })}
        </p>
        <div className="flex gap-1.5">
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-[0.75rem]"
            onClick={() => setMonthStart(shiftMonth(monthStart, -1))}
          >
            Earlier
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-[0.75rem]"
            onClick={() => setMonthStart(startOfMonth(new Date()))}
          >
            This month
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-[0.75rem]"
            onClick={() => setMonthStart(shiftMonth(monthStart, 1))}
          >
            Later
          </Button>
        </div>
      </div>

      <Month monthStart={monthStart} byDate={byDate} />

      {undated.length > 0 && (
        <section className="mt-8">
          <p className="t-eyebrow">Promised with no date ({undated.length})</p>
          <p className="text-paper-muted mt-1 text-[0.8125rem]">
            Nothing here can go overdue, which is exactly why it goes missing.
          </p>
          <ul className="mt-2">
            {undated.map((row) => (
              <li
                key={row.id}
                className="border-rule border-b py-2 text-[0.8125rem] last:border-b-0"
              >
                {row.text}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="mt-8">
        <p className="t-eyebrow">Tasks created ({data.tasks.length})</p>
        {data.tasks.length === 0 ? (
          <p className="text-paper-muted mt-1 text-[0.8125rem]">
            No tasks yet. A commitment becomes a task only once it has an owner.
          </p>
        ) : (
          <ul className="mt-2">
            {data.tasks.map((task) => (
              <li
                key={task.external_id}
                className="border-rule flex items-baseline justify-between gap-4 border-b py-2 last:border-b-0"
              >
                <span className="text-[0.8125rem]">{task.title}</span>
                <span className="t-data text-paper-muted shrink-0">
                  {task.external_id} · {task.status}
                  {task.due_date ? ` · ${task.due_date}` : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const BAND_DOT = {
  low: "bg-kept",
  medium: "bg-pending",
  high: "bg-debt",
} as const;

function Month({
  monthStart,
  byDate,
}: {
  monthStart: Date;
  byDate: Map<string, Commitment[]>;
}) {
  const today = isoDate(new Date());
  // Weeks start on Monday, because a working week does.
  const offset = (monthStart.getDay() + 6) % 7;
  const days = daysInMonth(monthStart);

  const cells: (string | null)[] = [
    ...Array.from({ length: offset }, () => null),
    ...Array.from({ length: days }, (_, index) =>
      isoDate(
        new Date(monthStart.getFullYear(), monthStart.getMonth(), index + 1),
      ),
    ),
  ];

  return (
    <div className="border-rule mt-3 border-t border-l">
      <div className="grid grid-cols-7">
        {WEEKDAYS.map((day) => (
          <div
            key={day}
            className="border-rule t-eyebrow border-r border-b px-2 py-1.5"
          >
            {day}
          </div>
        ))}
        {cells.map((iso, index) => {
          const due = iso ? (byDate.get(iso) ?? []) : [];
          const overdue = iso !== null && iso < today && due.length > 0;

          return (
            <div
              key={iso ?? `pad-${index}`}
              className={cn(
                "border-rule min-h-24 border-r border-b p-1.5 align-top",
                iso === null && "bg-band",
                iso === today && "bg-surface-raised",
              )}
            >
              {iso && (
                <>
                  <span
                    className={cn(
                      "t-data",
                      iso === today ? "text-paper" : "text-paper-muted",
                    )}
                  >
                    {Number(iso.slice(8, 10))}
                  </span>
                  <ul className="mt-1 space-y-1">
                    {due.map((row) => (
                      <li key={row.id}>
                        <Link
                          href={`/execution?commitment=${row.id}`}
                          title={`${row.text} (${row.risk_why})`}
                          className={cn(
                            "border-rule hover:bg-band block border-l-2 px-1.5 py-1 text-[0.6875rem] leading-snug no-underline",
                            overdue && "text-debt",
                          )}
                        >
                          <span
                            className={cn(
                              "mr-1 inline-block size-1.5 rounded-full align-middle",
                              BAND_DOT[row.risk_band],
                            )}
                          />
                          {row.text.length > 44
                            ? `${row.text.slice(0, 44)}…`
                            : row.text}
                        </Link>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "debt" | "pending";
}) {
  return (
    <div>
      <dt className="t-eyebrow">{label}</dt>
      <dd
        className={cn(
          "t-data mt-0.5 text-[1.125rem]",
          value > 0 && tone === "debt" && "text-debt",
          value > 0 && tone === "pending" && "text-pending",
        )}
      >
        {value}
      </dd>
    </div>
  );
}

function startOfMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

function shiftMonth(date: Date, by: number): Date {
  return new Date(date.getFullYear(), date.getMonth() + by, 1);
}

function daysInMonth(monthStart: Date): number {
  return new Date(
    monthStart.getFullYear(),
    monthStart.getMonth() + 1,
    0,
  ).getDate();
}

/** Local calendar date, not UTC: a deadline is a day where the person is. */
function isoDate(date: Date): string {
  const month = `${date.getMonth() + 1}`.padStart(2, "0");
  const day = `${date.getDate()}`.padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}
