"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiFetch, type Commitment } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { cn } from "@/lib/utils";

const VIEWS = [
  { key: "all", label: "All" },
  { key: "at_risk", label: "At risk" },
  { key: "unowned", label: "Unowned" },
  { key: "no_deadline", label: "No deadline" },
  { key: "overdue", label: "Overdue" },
] as const;

type ViewKey = (typeof VIEWS)[number]["key"];

const BAND = {
  low: "text-paper-muted",
  medium: "text-pending",
  high: "text-debt",
} as const;

export function ExecutionView() {
  const [view, setView] = useState<ViewKey>("all");
  const [rows, setRows] = useState<Commitment[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<Commitment | null>(null);

  // The reset happens in the click handler rather than at the top of the
  // effect: a synchronous setState inside an effect body causes a cascading
  // render, and React lints for it.
  useEffect(() => {
    let cancelled = false;

    apiFetch<Commitment[]>(`/commitments?view=${view}`)
      .then((data) => {
        if (!cancelled) setRows(data);
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "Could not load.");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [view]);

  return (
    <>
      <div className="border-rule flex gap-1 border-b px-8 py-2">
        {VIEWS.map((option) => (
          <button
            key={option.key}
            onClick={() => {
              if (option.key === view) return;
              setRows(null);
              setError(null);
              setView(option.key);
            }}
            aria-pressed={view === option.key}
            className={cn(
              "px-2.5 py-1 text-[0.8125rem] transition-colors",
              view === option.key
                ? "bg-surface-raised text-paper"
                : "text-paper-muted hover:text-paper-dim",
            )}
          >
            {option.label}
          </button>
        ))}
      </div>

      {error && (
        <p role="alert" className="text-debt px-8 py-6 text-[0.8125rem]">
          {error}
        </p>
      )}

      {rows === null && !error && (
        <div className="space-y-px px-8 py-4">
          {Array.from({ length: 5 }).map((_, index) => (
            <Skeleton key={index} className="h-11 w-full" />
          ))}
        </div>
      )}

      {rows?.length === 0 && <Empty view={view} />}

      {rows && rows.length > 0 && (
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-rule border-b">
              <Th className="w-[42%]">Commitment</Th>
              <Th>Owner</Th>
              <Th>Due</Th>
              <Th>Task</Th>
              <Th className="text-right">Risk</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.id}
                onClick={() => setOpen(row)}
                tabIndex={0}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setOpen(row);
                  }
                }}
                aria-label={`${row.text}. Open evidence.`}
                className="border-rule odd:bg-band hover:bg-surface-raised cursor-pointer border-b transition-colors"
              >
                <td className="px-8 py-2.5 text-[0.8125rem]">
                  {row.text}
                  {row.blocked_by && (
                    <span className="text-pending ml-2 text-[0.6875rem]">
                      blocked: {row.blocked_by}
                    </span>
                  )}
                </td>
                <td className="t-data px-3 py-2.5">
                  {row.owner && row.owner_id ? (
                    // Stops the row's evidence drawer from opening underneath
                    // the navigation.
                    <Link
                      href={`/people/${row.owner_id}`}
                      onClick={(event) => event.stopPropagation()}
                      className="hover:text-paper underline decoration-dotted underline-offset-2"
                    >
                      {row.owner}
                    </Link>
                  ) : (
                    <span className="text-debt">unowned</span>
                  )}
                </td>
                <td className="t-data px-3 py-2.5">
                  {row.due_date ?? <span className="text-pending">none</span>}
                  {row.slip_count > 0 && (
                    <span className="text-debt ml-1.5">+{row.slip_count}</span>
                  )}
                </td>
                <td className="t-data text-paper-muted px-3 py-2.5">
                  {row.external_task_id ?? "—"}
                </td>
                <td
                  className={cn(
                    "t-data px-8 py-2.5 text-right",
                    BAND[row.risk_band],
                  )}
                >
                  {row.risk_score.toFixed(2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <EvidenceSheet
        commitment={open}
        onOpenChange={(next) => !next && setOpen(null)}
      />
    </>
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
        "t-eyebrow px-3 py-2 text-left first:pl-8 last:pr-8",
        className,
      )}
    >
      {children}
    </th>
  );
}

function Empty({ view }: { view: ViewKey }) {
  const nothing =
    view === "all"
      ? "No commitments yet."
      : `Nothing is ${VIEWS.find((v) => v.key === view)?.label.toLowerCase()}.`;

  return (
    <div className="px-8 py-16 text-center">
      <p className="text-paper-dim text-[0.9375rem]">{nothing}</p>
      <Button asChild variant="outline" size="sm" className="mt-4">
        <Link href="/">Run a transcript</Link>
      </Button>
    </div>
  );
}

/**
 * The evidence drawer.
 *
 * Every row opens onto the sentence that produced it. This is the whole point
 * of enforcing verbatim quotes upstream: a commitment you can trace back to a
 * line someone actually said is checkable, and one you cannot is a guess with
 * good formatting.
 */
function EvidenceSheet({
  commitment,
  onOpenChange,
}: {
  commitment: Commitment | null;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Sheet open={commitment !== null} onOpenChange={onOpenChange}>
      <SheetContent className="bg-surface border-rule w-full sm:max-w-lg">
        {commitment && (
          <>
            <SheetHeader className="border-rule border-b">
              <SheetTitle className="t-heading">{commitment.text}</SheetTitle>
              <SheetDescription className="t-data text-paper-muted">
                {commitment.kind.replace("_", " ")} ·{" "}
                {commitment.status.replace("_", " ")}
              </SheetDescription>
            </SheetHeader>

            <div className="overflow-y-auto px-4 pb-6">
              <Field label="Said in the meeting">
                {commitment.evidence.map((item, index) => (
                  <blockquote
                    key={index}
                    className="border-l-rule-strong t-data text-paper mt-2 border-l-2 pl-3 leading-relaxed"
                  >
                    {item.speaker && (
                      <span className="text-paper-muted">{item.speaker}: </span>
                    )}
                    {item.quote}
                    {item.match && item.match !== "exact" && (
                      <span className="text-paper-muted block text-[0.6875rem]">
                        matched by {item.match}, offsets {item.start}–{item.end}
                      </span>
                    )}
                  </blockquote>
                ))}
              </Field>

              <Field label="Owner">
                <p className="t-data">
                  {commitment.owner ?? "nobody yet"}{" "}
                  <span className="text-paper-muted">
                    ({commitment.owner_confidence.toFixed(2)})
                  </span>
                </p>
                {commitment.owner_reason && (
                  <p className="text-paper-dim mt-1 text-[0.8125rem]">
                    {commitment.owner_reason}
                  </p>
                )}
              </Field>

              <Field label="Deadline">
                <p className="t-data">
                  {commitment.due_date ?? "none"}{" "}
                  <span className="text-paper-muted">
                    ({commitment.due_confidence.toFixed(2)})
                  </span>
                </p>
                {commitment.due_spoken_as && (
                  <p className="text-paper-dim mt-1 text-[0.8125rem]">
                    Spoken as &ldquo;{commitment.due_spoken_as}&rdquo;
                  </p>
                )}
                {commitment.original_due_date &&
                  commitment.original_due_date !== commitment.due_date && (
                    <p className="text-debt mt-1 text-[0.8125rem]">
                      Originally {commitment.original_due_date}
                    </p>
                  )}
              </Field>

              <Field
                label={`Risk ${commitment.risk_score.toFixed(2)} (${commitment.risk_band})`}
              >
                {commitment.risk_factors.length === 0 ? (
                  <p className="text-paper-dim text-[0.8125rem]">On track.</p>
                ) : (
                  <ul>
                    {commitment.risk_factors.map((factor) => (
                      <li
                        key={factor.name}
                        className="border-rule flex justify-between border-b py-1.5 text-[0.8125rem] last:border-b-0"
                      >
                        <span className="text-paper-dim">{factor.detail}</span>
                        <span className="t-data text-paper-muted">
                          +{factor.contribution.toFixed(2)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </Field>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-rule border-b py-4 last:border-b-0">
      <p className="t-eyebrow">{label}</p>
      {children}
    </section>
  );
}
