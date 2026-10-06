"use client";

import { ChevronRight, CircleAlert, LoaderCircle, Wrench } from "lucide-react";
import { useState } from "react";

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { ToolStep } from "@/lib/answer";

function describeArgs(tool: string, args: ToolStep["args"]): string {
  if (!args) return "";
  const main = args.query ?? args.name ?? args.path ?? (tool === "list_sources" ? "" : undefined);
  if (typeof main === "string") return main ? `“${main}”` : "";
  return Object.values(args).filter((v) => typeof v === "string").join(" ");
}

interface ToolTrailProps {
  steps: ToolStep[];
  answering: boolean; // collapse by default once the answer starts
}

/** Each tool call and what it returned, e.g. "→ search “required option”: 6 results". */
export function ToolTrail({ steps, answering }: ToolTrailProps) {
  const [manual, setManual] = useState<boolean | null>(null); // the reader's own choice wins
  const open = manual ?? !answering;
  if (!steps.length) return null;
  const pending = steps.some((s) => s.summary === undefined);
  return (
    <Collapsible open={open} onOpenChange={setManual} className="text-sm">
      <CollapsibleTrigger className="text-muted-foreground hover:text-foreground focus-visible:ring-ring flex items-center gap-1.5 rounded-sm py-1 outline-none focus-visible:ring-2">
        <ChevronRight className={`size-4 transition-transform motion-reduce:transition-none ${open ? "rotate-90" : ""}`} />
        <Wrench className="size-3.5" />
        {steps.length} tool {steps.length === 1 ? "call" : "calls"}
        {pending && <LoaderCircle className="size-3.5 animate-spin motion-reduce:animate-none" aria-label="running" />}
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ol className="border-muted mt-1 ml-2 space-y-1 border-l pl-4 font-mono text-xs">
          {steps.map((s) => (
            <li key={s.n} className="flex flex-wrap items-baseline gap-x-1.5">
              <span className="text-muted-foreground">→</span>
              <span className="font-semibold">{s.tool}</span>
              <span className="text-muted-foreground max-w-full truncate">{describeArgs(s.tool, s.args)}</span>
              {s.summary !== undefined && (
                <span className={s.ok ? "text-muted-foreground" : "text-amber-600 dark:text-amber-400"}>
                  {!s.ok && <CircleAlert className="mr-0.5 inline size-3" />}: {s.summary}
                </span>
              )}
            </li>
          ))}
        </ol>
      </CollapsibleContent>
    </Collapsible>
  );
}
