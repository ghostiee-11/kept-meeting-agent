import { SystemStatus } from "@/components/system-status";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-dvh max-w-2xl flex-col justify-center gap-8 px-6 py-16">
      <header className="flex items-baseline justify-between gap-6">
        <h1 className="t-display">Kept</h1>
        <p className="text-paper-dim text-right text-[0.8125rem] leading-snug">
          Meetings make promises.
          <br />
          Kept makes them accountable.
        </p>
      </header>

      <SystemStatus />
    </main>
  );
}
