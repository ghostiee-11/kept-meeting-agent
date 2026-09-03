"use client";

import { useEffect, useState } from "react";
import {
  apiFetch,
  ApiError,
  type Clarification,
  type Resolution,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * The other half of ambiguity handling.
 *
 * Refusing to guess an owner is only worth something if somebody can supply
 * the answer. The agents abstain and ask a specific question carrying the
 * candidates they already considered; this is where a person spends ten
 * seconds settling it, and the ledger records that a human decided rather
 * than a model.
 */
export function ClarificationsInbox() {
  // The toggle remounts this via `key`, so each filter starts from its own
  // loading state instead of showing the previous filter's rows while the
  // next request is in flight.
  const [showResolvedOuter, setShowResolvedOuter] = useState(false);

  return (
    <Inbox
      key={String(showResolvedOuter)}
      showResolved={showResolvedOuter}
      onToggle={() => setShowResolvedOuter((current) => !current)}
    />
  );
}

function Inbox({
  showResolved,
  onToggle,
}: {
  showResolved: boolean;
  onToggle: () => void;
}) {
  const [items, setItems] = useState<Clarification[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiFetch<Clarification[]>(`/clarifications?only_open=${!showResolved}`)
      .then((result) => {
        if (!cancelled) setItems(result);
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "Could not load.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [showResolved]);

  function settled(id: string) {
    setItems((current) => (current ?? []).filter((item) => item.id !== id));
  }

  if (error) {
    return (
      <p role="alert" className="text-debt px-8 py-6 text-[0.8125rem]">
        {error}
      </p>
    );
  }

  if (!items) {
    return (
      <div className="space-y-2 px-8 py-6">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  return (
    <div className="px-8 py-6">
      <div className="mb-4 flex items-baseline justify-between gap-4">
        <p className="text-paper-dim max-w-2xl text-[0.8125rem] leading-relaxed">
          Each of these is something the agents refused to guess. Answering one
          writes the answer to the ledger with you recorded as the source, not
          the model.
        </p>
        <Button
          variant="outline"
          size="sm"
          className="h-7 shrink-0 text-[0.75rem]"
          onClick={onToggle}
        >
          {showResolved ? "Open only" : "Include answered"}
        </Button>
      </div>

      {items.length === 0 ? (
        <p className="text-paper-muted py-10 text-center text-[0.8125rem]">
          Nothing open. Either every obligation had a clear owner and date, or
          somebody has already been through these.
        </p>
      ) : (
        <ul className="space-y-3">
          {items.map((item) => (
            <Question key={item.id} item={item} onSettled={settled} />
          ))}
        </ul>
      )}
    </div>
  );
}

function Question({
  item,
  onSettled,
}: {
  item: Clarification;
  onSettled: (id: string) => void;
}) {
  const [owner, setOwner] = useState("");
  const [due, setDue] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [done, setDone] = useState<Resolution | null>(null);

  // The candidates the Attributor already considered, so the common case is a
  // click rather than typing a name the system will only have to match again.
  const options = item.options
    .map((option) => String(option.label ?? option.name ?? ""))
    .filter(Boolean);

  async function send(body: Record<string, unknown>) {
    setBusy(true);
    setFailed(null);
    try {
      const result = await apiFetch<Resolution>(
        `/clarifications/${item.id}/resolve`,
        { method: "POST", body: JSON.stringify(body) },
      );
      setDone(result);
      // Left on screen for a beat so the answer is visibly accepted rather
      // than the row simply vanishing.
      setTimeout(() => onSettled(item.id), 1200);
    } catch (cause) {
      setFailed(
        cause instanceof ApiError
          ? cause.message.replace(/^\{"detail":"|"\}$/g, "")
          : "Could not save that.",
      );
    } finally {
      setBusy(false);
    }
  }

  const answered = item.status !== "open" || done !== null;

  return (
    <li
      className={cn(
        "border-rule border p-4 transition-opacity",
        answered && "opacity-60",
      )}
    >
      <p className="text-[0.9375rem]">{item.question}</p>
      {item.commitment_text && (
        <p className="t-data text-paper-muted mt-1">
          About: {item.commitment_text}
        </p>
      )}

      {item.evidence.length > 0 && (
        <blockquote className="border-l-rule-strong t-data text-paper-dim mt-2 border-l-2 pl-3">
          {String(item.evidence[0].quote ?? "")}
        </blockquote>
      )}

      {done ? (
        <p className="text-kept mt-3 text-[0.8125rem]">
          {done.status === "abandoned"
            ? "Dismissed."
            : `Saved. ${Object.entries(done.applied)
                .filter(([, value]) => value)
                .map(([field, value]) => `${field}: ${value}`)
                .join(", ")}`}
        </p>
      ) : (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {options.map((label) => (
            <Button
              key={label}
              size="sm"
              variant="outline"
              disabled={busy}
              className="h-7 text-[0.75rem]"
              onClick={() => send({ owner: label, answered_by: "reviewer" })}
            >
              {label}
            </Button>
          ))}

          <input
            value={owner}
            onChange={(event) => setOwner(event.target.value)}
            placeholder="Someone else"
            aria-label="Owner"
            className="border-rule placeholder:text-paper-muted h-7 w-36 border bg-transparent px-2 text-[0.75rem] outline-none"
          />
          <input
            value={due}
            onChange={(event) => setDue(event.target.value)}
            placeholder="Deadline, e.g. next Friday"
            aria-label="Deadline"
            className="border-rule placeholder:text-paper-muted h-7 w-48 border bg-transparent px-2 text-[0.75rem] outline-none"
          />
          <Button
            size="sm"
            disabled={busy || (!owner.trim() && !due.trim())}
            className="h-7 text-[0.75rem]"
            onClick={() =>
              send({
                owner: owner.trim() || null,
                due_date: due.trim() || null,
                answered_by: "reviewer",
              })
            }
          >
            Save
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            className="text-paper-muted h-7 text-[0.75rem]"
            onClick={() => send({ dismiss: true, answered_by: "reviewer" })}
          >
            Not needed
          </Button>
        </div>
      )}

      {failed && (
        <p role="alert" className="text-debt mt-2 text-[0.75rem]">
          {failed}
        </p>
      )}
    </li>
  );
}
