import { PageHeader } from "@/components/shell";
import { OpsView } from "@/components/ops-view";

export default function OpsPage() {
  return (
    <>
      <PageHeader title="Ops">
        <span className="text-paper-muted text-[0.8125rem]">
          Every model call, what it cost, and where the tasks landed
        </span>
      </PageHeader>
      <OpsView />
    </>
  );
}
