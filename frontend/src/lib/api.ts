export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const DEMO_KEY = process.env.NEXT_PUBLIC_DEMO_KEY;

export interface Health {
  status: "ok" | "degraded";
  version: string;
  environment: string;
  uptime_seconds: number;
  auth_enforced: boolean;
  providers: Record<string, boolean>;
  web_search: boolean;
  database: boolean;
  notes: string[];
}

export interface Evidence {
  quote: string;
  speaker: string | null;
  start: number | null;
  end: number | null;
  match: string | null;
}

export interface RiskFactor {
  name: string;
  contribution: number;
  detail: string;
}

export interface Commitment {
  id: string;
  text: string;
  kind: "commitment" | "action_item";
  status: string;
  owner: string | null;
  owner_confidence: number;
  owner_reason: string | null;
  due_date: string | null;
  original_due_date: string | null;
  due_spoken_as: string | null;
  due_confidence: number;
  slip_count: number;
  silence_streak: number;
  blocked_by: string | null;
  external_task_id: string | null;
  evidence: Evidence[];
  risk_score: number;
  risk_band: "low" | "medium" | "high";
  risk_why: string;
  risk_factors: RiskFactor[];
  meeting_id: string | null;
}

export interface Decision {
  id: string;
  statement: string;
  rationale: string | null;
  alternatives_considered: string[];
  confidence: number;
  evidence: Evidence[];
  enrichment: { summary: string; citations: string[] } | null;
}

export interface Rejection {
  id: string;
  text: string;
  rejected_by: string;
  stage: string;
  reason: string;
}

export interface MeetingSummary {
  id: string;
  title: string;
  occurred_at: string;
  project: string | null;
  participants: string[];
  commitments: number;
  decisions: number;
  open_questions: number;
}

export interface MeetingDetail {
  meeting: MeetingSummary;
  transcript: string;
  decisions: Decision[];
  commitments: Commitment[];
  rejections: Rejection[];
}

export interface TraceEntry {
  seq: number;
  agent: string;
  event: string;
  provider: string | null;
  model: string | null;
  tokens_in: number;
  tokens_out: number;
  latency_ms: number;
  cost_usd: number;
  payload: Record<string, unknown>;
}

export interface Run {
  id: string;
  meeting_id: string;
  status: string;
  cost_usd: number;
  tokens_in: number;
  tokens_out: number;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  trace: TraceEntry[];
}

export interface Task {
  id: string;
  title: string;
  description: string | null;
  assignee: string | null;
  due_date: string | null;
  status: string;
  url: string;
  created_at: string;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function headers(): Headers {
  const value = new Headers({ "Content-Type": "application/json" });
  if (DEMO_KEY) value.set("X-Demo-Key", DEMO_KEY);
  return value;
}

/**
 * The backend runs on Render's free tier, which sleeps after 15 minutes idle
 * and takes 30 to 60 seconds to wake. Callers need to tell a cold start from a
 * real outage so the UI can say which is happening, rather than showing a
 * spinner that looks broken.
 */
export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  { timeoutMs = 90_000 }: { timeoutMs?: number } = {},
): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: headers(),
    cache: "no-store",
    signal: AbortSignal.timeout(timeoutMs),
  });

  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText);
    throw new ApiError(detail || response.statusText, response.status);
  }

  return (await response.json()) as T;
}

export type RunEvent =
  | {
      type: "run_started";
      run_id: string;
      meeting_id: string;
      turns: number;
      reused_meeting: boolean;
      models: Record<string, { primary: string; fallbacks: string[] } | null>;
    }
  | { type: "model_call_started"; agent: string }
  | {
      type: "model_call";
      agent: string;
      provider: string | null;
      model: string | null;
      tokens_in: number;
      tokens_out: number;
      latency_ms: number;
      cost_usd: number;
    }
  | { type: "grounding_retry"; agent: string; count: number }
  | { type: "team_report"; node: string; line: string }
  | { type: "error"; agent: string; error: string }
  | {
      type: "run_finished";
      run_id: string;
      summary: string;
      counts: Record<string, number>;
      cost_usd: number;
      tokens: { in: number; out: number };
      by_agent: Record<string, { calls: number; cost_usd: number }>;
    }
  | { type: "run_failed"; error: string };

/**
 * Stream a run.
 *
 * Uses fetch rather than EventSource because the run is a POST carrying a
 * transcript, and EventSource can only issue GETs.
 */
export async function* streamRun(
  body: { transcript: string; title: string; timezone?: string },
  signal?: AbortSignal,
): AsyncGenerator<RunEvent> {
  const response = await fetch(`${API_URL}/meetings/run`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok || !response.body) {
    const detail = await response.text().catch(() => response.statusText);
    throw new ApiError(detail || response.statusText, response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    // SSE frames are separated by a blank line. Anything after the last one is
    // a partial frame and has to wait for the next chunk.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      const data = frame
        .split("\n")
        .find((line) => line.startsWith("data: "))
        ?.slice(6);
      if (data) yield JSON.parse(data) as RunEvent;
    }
  }
}
