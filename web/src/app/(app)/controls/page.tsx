import type { Metadata } from "next";
import { ControlsPage } from "@/components/controls/controls-page";

export const metadata: Metadata = { title: "Controls" };

export default function Page() {
  return <ControlsPage />;
}
