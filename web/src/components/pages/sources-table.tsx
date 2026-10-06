"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { getSources, type SourceInfo } from "@/lib/api";

const TYPE_LABEL = { repo: "Code", docs: "Docs", openapi: "API spec" } as const;

export function SourcesTable() {
  const [sources, setSources] = useState<SourceInfo[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const abort = new AbortController();
    getSources(abort.signal).then(setSources, () => !abort.signal.aborted && setFailed(true));
    return () => abort.abort();
  }, []);

  if (failed) {
    return <p role="alert" className="text-destructive text-sm">Couldn&apos;t load the sources. The server may be waking up; try again in a moment.</p>;
  }
  if (!sources) return <Skeleton className="h-40 w-full" />;
  if (!sources.length) return <p className="text-muted-foreground text-sm">Nothing is indexed yet.</p>;

  return (
    <div className="overflow-x-auto rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Source</TableHead>
            <TableHead>Type</TableHead>
            <TableHead className="text-right">Documents</TableHead>
            <TableHead className="text-right">Chunks</TableHead>
            <TableHead>Status</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sources.map((s) => (
            <TableRow key={`${s.type}:${s.ref}`}>
              <TableCell className="font-mono text-xs">
                {s.ref}
                {s.subpath && <span className="text-muted-foreground"> /{s.subpath}</span>}
              </TableCell>
              <TableCell>{TYPE_LABEL[s.type]}</TableCell>
              <TableCell className="text-right tabular-nums">{s.documents.toLocaleString()}</TableCell>
              <TableCell className="text-right tabular-nums">{s.chunks.toLocaleString()}</TableCell>
              <TableCell>
                <Badge variant={s.status === "ready" ? "secondary" : "destructive"}>{s.status}</Badge>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
