import { PageHeader } from "@/components/shell";
import { PeopleList } from "@/components/people-list";

export default function PeoplePage() {
  return (
    <>
      <PageHeader title="People">
        <span className="text-paper-muted text-[0.8125rem]">
          Click anyone for their promises on a calendar
        </span>
      </PageHeader>
      <PeopleList />
    </>
  );
}
