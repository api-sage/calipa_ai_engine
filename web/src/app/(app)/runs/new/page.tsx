import type { Metadata } from "next";
import { PageHeader } from "@/components/calipa/states";
import { NewAudit } from "@/components/runs/new-audit";

export const metadata: Metadata = { title: "New audit" };

export default function NewAuditPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-8 px-8 pt-16 pb-10">
      <PageHeader title="New audit" description="Describe what to check in your own words. Name the period and the accounts if you can." />
      <NewAudit />
    </div>
  );
}
