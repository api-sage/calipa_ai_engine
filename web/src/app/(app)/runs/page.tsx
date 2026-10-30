import type { Metadata } from "next";
import { PageHeader } from "@/components/calipa/states";
import { RunsBoard } from "@/components/runs/runs-board";

export const metadata: Metadata = { title: "Audits" };

export default function RunsPage() {
  return (
    <div className="mx-auto max-w-6xl space-y-8 px-8 py-10">
      <PageHeader title="Audits" description="Every audit you started, newest first. Open one to follow it live or read its report." />
      <RunsBoard view="mine" />
    </div>
  );
}
