"use client";

import dynamic from "next/dynamic";
import { PageHeader } from "@/components/shell";
import { SystemStatus } from "@/components/system-status";

// Client-only, so the console can restore a pasted transcript from session
// storage during its first render. Leaving the page and coming back must not
// throw away what somebody typed, and there is nothing here worth rendering
// on the server anyway: the whole panel is a live stream.
const RunConsole = dynamic(
  () => import("@/components/run-console").then((module) => module.RunConsole),
  { ssr: false },
);

export default function Home() {
  return (
    <>
      <PageHeader title="Run">
        <SystemStatus compact />
      </PageHeader>
      <RunConsole />
    </>
  );
}
