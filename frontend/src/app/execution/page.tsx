import { PageHeader } from "@/components/shell";
import { ExecutionView } from "@/components/execution-view";

export default function ExecutionPage() {
  return (
    <>
      <PageHeader title="Execution">
        <span className="text-paper-muted text-[0.8125rem]">
          Click any row for the sentence that produced it
        </span>
      </PageHeader>
      <ExecutionView />
    </>
  );
}
