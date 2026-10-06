import type { Metadata } from "next";

import { SourcesTable } from "@/components/pages/sources-table";

export const metadata: Metadata = { title: "Sources" };

export default function SourcesPage() {
  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-8">
      <h1 className="text-2xl font-semibold tracking-tight">Indexed sources</h1>
      <p className="text-muted-foreground mt-1 mb-6 max-w-prose">
        Everything Grepwise can answer from: code repositories, library docs and API specs, each at
        one exact version.
      </p>
      <SourcesTable />
    </div>
  );
}
