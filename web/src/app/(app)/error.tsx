"use client";

import { useEffect } from "react";
import Link from "next/link";
import { RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { BrokenArt } from "@/components/calipa/illustrations";

// A render error inside a page. The shell stays, so the person can go elsewhere.
export default function PageError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => console.error(error), [error]);
  return (
    <div className="grid min-h-full place-items-center px-8 py-16">
      <div className="flex max-w-md flex-col items-center gap-4 text-center" role="alert">
        <BrokenArt />
        <div className="space-y-1.5">
          <h1 className="font-serif text-title font-medium">This page broke</h1>
          <p className="text-small text-ink-muted text-pretty">
            Something went wrong while showing it. Nothing was changed. Try again, and if it keeps happening, send the reference below to the Calipa team.
          </p>
          {error.digest && <p className="font-mono text-caption text-ink-subtle">ref {error.digest}</p>}
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" asChild>
            <Link href="/runs">Back to audits</Link>
          </Button>
          <Button size="sm" onClick={reset}>
            <RotateCcw /> Try again
          </Button>
        </div>
      </div>
    </div>
  );
}
