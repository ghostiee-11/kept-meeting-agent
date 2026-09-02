import { PageHeader } from "@/components/shell";
import { RunConsole } from "@/components/run-console";
import { SystemStatus } from "@/components/system-status";

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
