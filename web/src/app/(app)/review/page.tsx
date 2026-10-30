import type { Metadata } from "next";
import { PageHeader } from "@/components/calipa/states";
import { RunsBoard } from "@/components/runs/runs-board";

export const metadata: Metadata = { title: "Review queue" };

export default function ReviewPage() {
  return (
    <div className="mx-auto max-w-6xl space-y-8 px-8 py-10">
      <PageHeader
        title="Review queue"
        description="Reports waiting for a second person, oldest first. Audits you started are left out: nobody reviews their own work."
      />
      <RunsBoard view="review_queue" />
    </div>
  );
}
