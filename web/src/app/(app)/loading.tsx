import { Skeleton } from "@/components/ui/skeleton";

// Between pages: the shape of a page header and its first block, so nothing jumps.
export default function Loading() {
  return (
    <div className="mx-auto max-w-6xl space-y-8 px-8 py-10" aria-busy="true" aria-label="Loading">
      <div className="space-y-3">
        <Skeleton className="h-9 w-56" />
        <Skeleton className="h-4 w-96" />
      </div>
      <Skeleton className="h-64 w-full rounded-xl" />
    </div>
  );
}
