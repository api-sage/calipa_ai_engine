import type { Metadata } from "next";
import { RunPage } from "@/components/run/run-page";

export const metadata: Metadata = { title: "Audit" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <RunPage id={id} />;
}
