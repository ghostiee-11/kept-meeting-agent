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

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * The backend runs on Render's free tier, which spins down after 15 minutes
 * idle and takes 30 to 60 seconds to come back. Callers need to distinguish a
 * cold start from a real outage so the UI can say which one is happening,
 * rather than showing a spinner that looks broken.
 */
export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  { timeoutMs = 90_000 }: { timeoutMs?: number } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  if (DEMO_KEY) headers.set("X-Demo-Key", DEMO_KEY);

  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers,
    signal: AbortSignal.timeout(timeoutMs),
  });

  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText);
    throw new ApiError(detail || response.statusText, response.status);
  }

  return (await response.json()) as T;
}
