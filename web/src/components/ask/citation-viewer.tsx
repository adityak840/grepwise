"use client";

import { Check, Copy, ExternalLink, LoaderCircle, Plus } from "lucide-react";
import { useEffect, useState } from "react";

import { CodeView } from "@/components/code-view";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useMediaQuery } from "@/hooks/use-media-query";
import type { Chip } from "@/lib/answer";
import { type ReadResult, readLines } from "@/lib/api";
import { parseCitation, sourceOf, STATUS_TEXT } from "@/lib/citations";
import { errorMessage } from "@/lib/errors";
import { githubUrl } from "@/lib/github";
import { langFromPath } from "@/lib/highlight";

const CONTEXT_LINES = 20;

interface CitationViewerProps {
  chip: Chip | null;
  sourceTypes: Map<string, string>; // ref -> repo | docs | openapi
  onClose: () => void;
}

export function CitationViewer({ chip, sourceTypes, onClose }: CitationViewerProps) {
  const wide = useMediaQuery("(min-width: 768px)");
  return (
    <Sheet open={chip !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent
        side={wide ? "right" : "bottom"}
        className="max-h-[85vh] gap-3 overflow-y-auto data-[side=right]:w-full data-[side=right]:sm:max-w-xl"
      >
        {chip && <ViewerBody key={chip.citation} chip={chip} sourceTypes={sourceTypes} />}
      </SheetContent>
    </Sheet>
  );
}

function ViewerBody({ chip, sourceTypes }: { chip: Chip; sourceTypes: Map<string, string> }) {
  const parsed = parseCitation(chip.citation);
  const [copied, setCopied] = useState(false);
  const [context, setContext] = useState<ReadResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(t);
  }, [copied]);

  const source = parsed ? sourceOf(parsed) : null;
  const github = parsed ? githubUrl(chip.citation, sourceTypes.get(source ?? "")) : null;

  async function loadContext() {
    if (!parsed || !source) return;
    setLoading(true);
    setError(null);
    try {
      setContext(await readLines(source, parsed.path, Math.max(1, parsed.start - CONTEXT_LINES), parsed.end + CONTEXT_LINES));
    } catch (e) {
      setError(errorMessage((e as { code?: string }).code ?? "network"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <SheetHeader className="pb-0">
        <SheetTitle className="flex items-center gap-2">
          Citation {chip.n}
          <span className={chip.status === "verified" ? "text-emerald-600 dark:text-emerald-400 text-sm font-normal" : "text-red-600 dark:text-red-400 text-sm font-normal"}>
            {chip.status === "verified" ? "verified" : chip.status === "pending" ? "checking…" : "not verified"}
          </span>
        </SheetTitle>
        <SheetDescription>{chip.status === "pending" ? "Checking this citation…" : STATUS_TEXT[chip.status]}</SheetDescription>
      </SheetHeader>
      <div className="flex flex-col gap-3 px-4 pb-4">
        <div className="bg-muted flex items-start gap-2 rounded-md p-2">
          <code className="flex-1 font-mono text-xs break-all">{chip.citation}</code>
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label={copied ? "Copied" : "Copy citation"}
            onClick={() => navigator.clipboard?.writeText(chip.citation).then(() => setCopied(true))}
          >
            {copied ? <Check /> : <Copy />}
          </Button>
        </div>

        {parsed && chip.status === "verified" && (
          <>
            {context ? (
              <CodeView code={context.content} lang={langFromPath(parsed.path)} firstLine={context.start_line} mark={{ from: parsed.start, to: parsed.end }} />
            ) : chip.content ? (
              <CodeView code={chip.content} lang={langFromPath(parsed.path)} firstLine={parsed.start} />
            ) : null}
            <div className="flex flex-wrap gap-2">
              {!context && (
                <Button variant="outline" size="sm" onClick={loadContext} disabled={loading}>
                  {loading ? <LoaderCircle className="animate-spin motion-reduce:animate-none" /> : <Plus />}
                  Show more context
                </Button>
              )}
              {github && (
                <Button variant="outline" size="sm" asChild>
                  <a href={github} target="_blank" rel="noreferrer noopener">
                    <ExternalLink /> Open on GitHub
                  </a>
                </Button>
              )}
            </div>
            {context && (
              <p className="text-muted-foreground text-xs">
                Lines {context.start_line}–{context.end_line} of {context.total_lines}; the cited lines are highlighted.
              </p>
            )}
            {error && <p className="text-destructive text-sm">{error}</p>}
          </>
        )}
      </div>
    </>
  );
}
