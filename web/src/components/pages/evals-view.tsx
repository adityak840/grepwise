"use client";

import { useEffect, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { getEvals } from "@/lib/api";

type Report = Record<string, unknown>;

function format(value: unknown): string {
  if (typeof value === "number") return value <= 1 && !Number.isInteger(value) ? `${(value * 100).toFixed(1)}%` : value.toLocaleString();
  return String(value ?? "–");
}

/** One table per section whose rows are objects of metrics: {section: {row: {metric: value}}}. */
function Section({ title, rows }: { title: string; rows: Record<string, Record<string, unknown>> }) {
  const metrics = [...new Set(Object.values(rows).flatMap((r) => Object.keys(r)))];
  return (
    <section className="mb-8">
      <h2 className="mb-2 text-lg font-semibold capitalize">{title.replace(/_/g, " ")}</h2>
      <div className="overflow-x-auto rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead />
              {metrics.map((m) => <TableHead key={m} className="text-right">{m}</TableHead>)}
            </TableRow>
          </TableHeader>
          <TableBody>
            {Object.entries(rows).map(([name, row]) => (
              <TableRow key={name}>
                <TableCell className="font-medium">{name}</TableCell>
                {metrics.map((m) => <TableCell key={m} className="text-right tabular-nums">{format(row[m])}</TableCell>)}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}

const isRows = (v: unknown): v is Record<string, Record<string, unknown>> =>
  typeof v === "object" && v !== null && Object.values(v).every((r) => typeof r === "object" && r !== null);

export function EvalsView() {
  const [report, setReport] = useState<Report | null | undefined>(undefined);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const abort = new AbortController();
    getEvals(abort.signal).then(setReport, () => !abort.signal.aborted && setFailed(true));
    return () => abort.abort();
  }, []);

  if (failed) return <p role="alert" className="text-destructive text-sm">Couldn&apos;t load the eval results.</p>;
  if (report === undefined) return <Skeleton className="h-40 w-full" />;
  if (report === null) {
    return (
      <p className="text-muted-foreground rounded-md border border-dashed p-6 text-sm">
        No eval results yet. They appear here once the eval suite has run.
      </p>
    );
  }
  return (
    <div>
      {typeof report.generated_at === "string" && (
        <p className="text-muted-foreground mb-4 text-xs">Generated {new Date(report.generated_at).toLocaleString()}</p>
      )}
      {Object.entries(report)
        .filter(([, v]) => isRows(v))
        .map(([title, rows]) => <Section key={title} title={title} rows={rows as Record<string, Record<string, unknown>>} />)}
    </div>
  );
}
