import type { Metadata } from "next";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { LostArt } from "@/components/calipa/illustrations";
import { EmptyState } from "@/components/calipa/states";
import { AppShell } from "@/components/shell/app-shell";

export const metadata: Metadata = { title: "Not found" };

export default function NotFound() {
  return (
    <AppShell>
      <div className="grid min-h-full place-items-center px-8 py-16">
        <EmptyState
          art={LostArt}
          title="There is no page here"
          body="The address may be mistyped, or the page has moved. Search with ⌘K, or go back to your audits."
          action={
            <Button asChild variant="outline" size="sm">
              <Link href="/runs">Back to audits</Link>
            </Button>
          }
        />
      </div>
    </AppShell>
  );
}
