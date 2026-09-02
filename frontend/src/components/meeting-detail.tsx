"use client";

import { useEffect, useState } from "react";
import { apiFetch, type MeetingDetail as MeetingDetailData } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

/**
 * Everything one run produced, in one place. This is what "See who owes what"
 * leads to right after a run finishes: not a global filtered table, but the
 * actual output of the meeting just processed. Decisions, obligations with
 * risk, what the Skeptic threw out and why, and the drafted recap all live
 * here, because storing them and never showing them is the same as not
 * having built them.
 */
export function MeetingDetailView({ meetingId }: { meetingId: string }) {
  const [data, setData] = useState<MeetingDetailData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiFetch<MeetingDetailData>(`/meetings/${meetingId}`)
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
  }, [meetingId]);

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
        <Skeleton className="h-6 w-64" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  return (
    <div className="px-8 py-6">
      <h2 className="t-heading">{data.meeting.title}</h2>
      <p className="text-paper-muted mt-1 text-[0.8125rem]">
        {new Date(data.meeting.occurred_at).toLocaleString()} ·{" "}
        {data.meeting.participants.join(", ") || "no participants recorded"}
      </p>

      <Tabs defaultValue="commitments" className="mt-6">
        <TabsList>
          <TabsTrigger value="commitments">
            Commitments ({data.commitments.length})
          </TabsTrigger>
          <TabsTrigger value="decisions">
            Decisions ({data.decisions.length})
          </TabsTrigger>
          <TabsTrigger value="rejected">
            Rejected ({data.rejections.length})
          </TabsTrigger>
          <TabsTrigger value="recap">
            Follow-ups ({data.communications.length})
          </TabsTrigger>
        </TabsList>

        <TabsContent value="commitments" className="mt-4">
          <CommitmentsTab commitments={data.commitments} />
        </TabsContent>
        <TabsContent value="decisions" className="mt-4">
          <DecisionsTab decisions={data.decisions} />
        </TabsContent>
        <TabsContent value="rejected" className="mt-4">
          <RejectedTab rejections={data.rejections} />
        </TabsContent>
        <TabsContent value="recap" className="mt-4">
          <RecapTab communications={data.communications} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

const BAND = {
  low: "text-paper-muted",
  medium: "text-pending",
  high: "text-debt",
} as const;

function CommitmentsTab({
  commitments,
}: {
  commitments: MeetingDetailData["commitments"];
}) {
  if (commitments.length === 0) {
    return <Empty>No obligations were extracted from this meeting.</Empty>;
  }

  return (
    <ul>
      {commitments.map((row) => (
        <li key={row.id} className="border-rule border-b py-3 last:border-b-0">
          <div className="flex items-baseline justify-between gap-4">
            <span className="text-[0.8125rem]">{row.text}</span>
            <span className={cn("t-data shrink-0", BAND[row.risk_band])}>
              risk {row.risk_score.toFixed(2)}
            </span>
          </div>
          <p className="t-data text-paper-muted mt-1">
            {row.owner ?? "unowned"} · {row.due_date ?? "no deadline"}
            {row.slip_count > 0 && (
              <span className="text-debt"> · slipped {row.slip_count}×</span>
            )}
          </p>
          {row.evidence[0] && (
            <blockquote className="border-l-rule-strong t-data text-paper-dim mt-1.5 border-l-2 pl-3">
              {row.evidence[0].quote}
            </blockquote>
          )}
        </li>
      ))}
    </ul>
  );
}

function DecisionsTab({
  decisions,
}: {
  decisions: MeetingDetailData["decisions"];
}) {
  if (decisions.length === 0) {
    return <Empty>No decisions were settled in this meeting.</Empty>;
  }

  return (
    <ul>
      {decisions.map((row) => (
        <li key={row.id} className="border-rule border-b py-3 last:border-b-0">
          <p className="text-[0.8125rem]">{row.statement}</p>
          {row.rationale && (
            <p className="text-paper-dim mt-1 text-[0.8125rem]">
              {row.rationale}
            </p>
          )}
          {row.alternatives_considered.length > 0 && (
            <p className="text-paper-muted mt-1 text-[0.75rem]">
              Considered and not chosen:{" "}
              {row.alternatives_considered.join(", ")}
            </p>
          )}
          {row.evidence[0] && (
            <blockquote className="border-l-rule-strong t-data text-paper-dim mt-1.5 border-l-2 pl-3">
              {row.evidence[0].quote}
            </blockquote>
          )}
          {row.enrichment && (
            <div className="bg-band mt-2 p-2.5">
              <p className="t-eyebrow">Researched context</p>
              <p className="text-paper-dim mt-1 text-[0.8125rem] leading-relaxed">
                {row.enrichment.summary}
              </p>
              {row.enrichment.citations.map((url) => (
                <a
                  key={url}
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  className="t-data text-kept mt-1 block truncate underline underline-offset-2"
                >
                  {url}
                </a>
              ))}
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

/**
 * What the Skeptic threw out, and why. This is the tab that proves
 * adversarial review is real rather than decorative: a reviewer can check
 * each reason against the transcript in ten seconds.
 */
function RejectedTab({
  rejections,
}: {
  rejections: MeetingDetailData["rejections"];
}) {
  if (rejections.length === 0) {
    return (
      <Empty>
        Nothing was rejected. Either the meeting was clean, or the Skeptic found
        no reason to doubt what the Analyst extracted.
      </Empty>
    );
  }

  return (
    <ul>
      {rejections.map((row) => (
        <li key={row.id} className="border-rule border-b py-3 last:border-b-0">
          <div className="flex items-baseline gap-2">
            <span className="text-debt t-eyebrow">{row.rejected_by}</span>
            <span className="text-paper-muted t-data">at {row.stage}</span>
          </div>
          <p className="mt-1 text-[0.8125rem] line-through opacity-70">
            {row.text}
          </p>
          <p className="text-paper-dim mt-1 text-[0.8125rem]">{row.reason}</p>
        </li>
      ))}
    </ul>
  );
}

const KIND_LABEL: Record<string, string> = {
  recap_email: "Recap to everyone",
  owner_nudge: "Nudge, written by the nightly sweep",
  digest: "Digest",
};

/**
 * The recap written when the meeting was processed, plus any nudges the
 * nightly sweep has written since, because a deadline passed and nobody
 * convened to notice. Everything here is a draft: nothing in this system
 * sends anything on anyone's behalf.
 */
function RecapTab({
  communications,
}: {
  communications: MeetingDetailData["communications"];
}) {
  if (communications.length === 0) {
    return (
      <Empty>
        Nothing has been drafted. This meeting may have produced nothing worth
        summarising, or the Herald could not run.
      </Empty>
    );
  }

  return (
    <ul className="space-y-3">
      {communications.map((item) => (
        <li key={item.id} className="border-rule border p-4">
          <div className="flex items-baseline justify-between gap-4">
            <p className="t-heading text-[0.9375rem]">
              {item.subject ?? KIND_LABEL[item.kind] ?? item.kind}
            </p>
            <span className="t-data text-paper-muted shrink-0">
              {item.status === "sent" ? "sent" : "draft, not sent"}
            </span>
          </div>
          <p className="t-eyebrow mt-1">{KIND_LABEL[item.kind] ?? item.kind}</p>
          <p className="text-paper-dim mt-3 text-[0.8125rem] leading-relaxed whitespace-pre-wrap">
            {item.body}
          </p>
        </li>
      ))}
    </ul>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-paper-muted py-8 text-center text-[0.8125rem]">
      {children}
    </p>
  );
}
