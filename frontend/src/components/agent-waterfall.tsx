"use client";

/**
 * The agent waterfall.
 *
 * One lane per agent, one bar per model call, positioned by when it started
 * and sized by how long it took. It is the clearest evidence that this is a
 * team rather than one prompt wearing ten hats: you can see the three Analyst
 * briefs overlap, the Skeptic run only after they finish, and the Attributor
 * and Chronos barely appear at all because most of their work is arithmetic
 * that never reaches a model.
 */

export interface Bar {
  agent: string;
  startMs: number;
  durationMs: number;
  provider: string | null;
  model: string | null;
  costUsd: number;
  tokensOut: number;
  failed?: boolean;
}

const TEAM_OF: Array<[RegExp, string]> = [
  [/^chief/, "Supervisor"],
  [/^(analyst|skeptic|scribe|intelligence)/, "Intelligence"],
  [/^(attributor|chronos|researcher|resolution)/, "Resolution"],
  [/^(operator|herald|execution)/, "Execution"],
];

function teamOf(agent: string): string {
  return TEAM_OF.find(([pattern]) => pattern.test(agent))?.[1] ?? "Other";
}

const TEAM_ORDER = [
  "Supervisor",
  "Intelligence",
  "Resolution",
  "Execution",
  "Other",
];

export function AgentWaterfall({
  bars,
  agents: seen,
  active,
  elapsedMs,
}: {
  bars: Bar[];
  agents: string[];
  active: Set<string>;
  elapsedMs: number;
}) {
  const agents = [
    ...new Set([...seen, ...bars.map((b) => b.agent), ...active]),
  ].sort(
    (a, b) =>
      TEAM_ORDER.indexOf(teamOf(a)) - TEAM_ORDER.indexOf(teamOf(b)) ||
      a.localeCompare(b),
  );

  if (agents.length === 0) {
    return <Roster />;
  }

  // Always scale to at least ten seconds, so the first short call does not
  // fill the whole width and then visibly shrink as the run goes on.
  const span = Math.max(elapsedMs, 10_000);

  // Grouped before rendering rather than tracked with a running variable:
  // mutating during render is a bug waiting to happen under concurrent React,
  // and the lint is right to refuse it.
  const teams = new Map<string, string[]>();
  for (const agent of agents) {
    const team = teamOf(agent);
    teams.set(team, [...(teams.get(team) ?? []), agent]);
  }

  return (
    <div className="px-5 py-4">
      {[...teams].map(([team, members]) => (
        <section key={team} className="mt-4 first:mt-0">
          <p className="t-eyebrow mb-1.5">{team}</p>
          {members.map((agent) => {
            const own = bars.filter((bar) => bar.agent === agent);
            const cost = own.reduce((total, bar) => total + bar.costUsd, 0);

            return (
              <div key={agent} className="flex items-center gap-3 py-1">
                <span
                  className="t-data text-paper-dim w-40 shrink-0 truncate"
                  title={agent}
                >
                  {active.has(agent) && (
                    <span className="bg-pending mr-1.5 inline-block size-1.5 animate-pulse rounded-full align-middle" />
                  )}
                  {agent}
                </span>

                <div className="bg-band relative h-4 flex-1 overflow-hidden">
                  {own.map((bar, index) => (
                    <span
                      key={index}
                      title={`${bar.model ?? "?"} · ${bar.durationMs}ms · $${bar.costUsd.toFixed(5)}`}
                      className={`absolute top-0 h-full ${
                        bar.failed ? "bg-debt" : "bg-kept"
                      } opacity-80`}
                      style={{
                        left: `${(bar.startMs / span) * 100}%`,
                        width: `${Math.max((bar.durationMs / span) * 100, 0.6)}%`,
                      }}
                    />
                  ))}
                </div>

                <span className="t-data text-paper-muted w-24 shrink-0 text-right">
                  {own.length > 0 ? `$${cost.toFixed(4)}` : "no model call"}
                </span>
              </div>
            );
          })}
        </section>
      ))}
    </div>
  );
}

/**
 * Who is about to work, shown before anything runs.
 *
 * A waiting screen that says "activity will appear here" wastes the one moment
 * the reader is actually looking. This is the answer to "what does multi-agent
 * mean here", available before you press anything.
 */
const ROSTER: Array<[string, Array<[string, string]>]> = [
  [
    "Supervisor",
    [
      [
        "chief_of_staff",
        "Routes between teams, replans, decides when it is done",
      ],
    ],
  ],
  [
    "Intelligence",
    [
      ["scribe", "Splits the transcript into attributed turns"],
      [
        "analyst:decisions",
        "Extracts settled decisions with verbatim evidence",
      ],
      ["analyst:commitments", "Extracts obligations and classifies each one"],
      ["analyst:blockers", "Extracts what the team is stuck behind"],
      [
        "skeptic",
        "Attacks every candidate and throws out what does not hold up",
      ],
    ],
  ],
  [
    "Resolution",
    [
      ["attributor", "Works out who owns it, or refuses to guess"],
      ["chronos", "Turns spoken deadlines into dates"],
      ["researcher", "Looks up what the transcript never explains"],
    ],
  ],
  [
    "Execution",
    [
      ["operator", "Creates the tasks, and only for owned commitments"],
      ["herald", "Drafts the recap and the nudges"],
    ],
  ],
];

function Roster() {
  return (
    <div className="px-5 py-4">
      <p className="text-paper-dim mb-4 text-[0.8125rem] leading-relaxed">
        Nine agents across three teams, the Analyst running three briefs at
        once. Each has its own prompt, model, and tools. Run a transcript to
        watch them work.
      </p>
      {ROSTER.map(([team, members]) => (
        <section key={team} className="mt-4 first:mt-0">
          <p className="t-eyebrow mb-1">{team}</p>
          {members.map(([name, does]) => (
            <div key={name} className="flex items-baseline gap-3 py-0.5">
              <span className="t-data text-paper-dim w-40 shrink-0 truncate">
                {name}
              </span>
              <span className="text-paper-muted min-w-0 text-[0.75rem]">
                {does}
              </span>
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}
