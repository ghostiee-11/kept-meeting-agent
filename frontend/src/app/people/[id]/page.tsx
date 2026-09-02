import Link from "next/link";
import { PageHeader } from "@/components/shell";
import { PersonLedgerView } from "@/components/person-ledger";

export default async function PersonPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <>
      <PageHeader title="Ledger">
        <Link
          href="/people"
          className="text-paper-muted hover:text-paper t-data transition-colors"
        >
          Everyone
        </Link>
      </PageHeader>
      <PersonLedgerView personId={id} />
    </>
  );
}
