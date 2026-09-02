import Link from "next/link";
import { PageHeader } from "@/components/shell";
import { MeetingDetailView } from "@/components/meeting-detail";

export default async function MeetingPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <>
      <PageHeader title="Meeting">
        <Link
          href="/execution"
          className="text-paper-muted hover:text-paper t-data transition-colors"
        >
          All commitments
        </Link>
      </PageHeader>
      <MeetingDetailView meetingId={id} />
    </>
  );
}
