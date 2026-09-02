"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiFetch, type PersonSummary } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/** Who owes what, one line per person. The way to reach anyone's ledger. */
export function PeopleList() {
  const [people, setPeople] = useState<PersonSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiFetch<PersonSummary[]>("/people")
      .then((result) => {
        if (!cancelled) setPeople(result);
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

  if (error) {
    return (
      <p role="alert" className="text-debt px-8 py-6 text-[0.8125rem]">
        {error}
      </p>
    );
  }

  if (!people) {
    return (
      <div className="space-y-2 px-8 py-6">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    );
  }

  if (people.length === 0) {
    return (
      <p className="text-paper-muted px-8 py-6 text-[0.8125rem]">
        The roster is empty. Run `make seed` to create the demo workspace.
      </p>
    );
  }

  return (
    <ul className="px-8 py-6">
      {people.map((person) => (
        <li key={person.id} className="border-rule border-b last:border-b-0">
          <Link
            href={`/people/${person.id}`}
            className="hover:bg-band flex items-baseline justify-between gap-6 px-2 py-3 no-underline transition-colors"
          >
            <span>
              <span className="text-[0.9375rem] font-medium">
                {person.name}
              </span>
              <span className="text-paper-muted ml-2 text-[0.8125rem]">
                {person.role ?? "no role recorded"}
              </span>
            </span>
            <span className="t-data text-paper-muted shrink-0">
              {person.open_items} open
              {person.overdue > 0 && (
                <span className="text-debt"> · {person.overdue} overdue</span>
              )}
              {person.at_risk > 0 && (
                <span className={cn("text-pending")}>
                  {" "}
                  · {person.at_risk} at risk
                </span>
              )}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
