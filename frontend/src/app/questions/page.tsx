import { PageHeader } from "@/components/shell";
import { ClarificationsInbox } from "@/components/clarifications-inbox";

export default function QuestionsPage() {
  return (
    <>
      <PageHeader title="Questions">
        <span className="text-paper-muted text-[0.8125rem]">
          What the agents refused to guess
        </span>
      </PageHeader>
      <ClarificationsInbox />
    </>
  );
}
