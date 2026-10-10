"use client";

import { AlertTriangle, CircleStop, LoaderCircle, Search, ShieldCheck } from "lucide-react";

import { type Chip, hidePartialMarker, type Turn } from "@/lib/answer";
import { errorMessage, isQuotaError } from "@/lib/errors";

import { AnswerMarkdown } from "./answer-markdown";
import { ToolTrail } from "./tool-trail";

export function TurnView({ turn, onOpenCitation }: { turn: Turn; onOpenCitation: (chip: Chip) => void }) {
  const working = turn.status === "thinking" || turn.status === "looking";
  return (
    <article className="flex flex-col gap-3" aria-busy={working || turn.status === "streaming"}>
      <h2 className="text-base font-semibold leading-snug">{turn.question}</h2>
      <ToolTrail steps={turn.steps} answering={turn.text.length > 0 || turn.status === "done"} />

      {working && !turn.text && (
        <p className="text-muted-foreground flex items-center gap-2 text-sm" role="status">
          {turn.status === "looking" ? (
            <Search className="size-4 animate-pulse motion-reduce:animate-none" />
          ) : (
            <LoaderCircle className="size-4 animate-spin motion-reduce:animate-none" />
          )}
          {turn.status === "looking" ? "Looking something up…" : "Thinking…"}
        </p>
      )}

      {turn.text && (
        <AnswerMarkdown
          text={turn.status === "streaming" ? hidePartialMarker(turn.text) : turn.text}
          citations={turn.citations}
          onOpenCitation={onOpenCitation}
          streaming={turn.status === "streaming" || turn.status === "looking"}
        />
      )}

      {turn.status === "stopped" && (
        <p className="text-muted-foreground flex items-center gap-1.5 text-sm">
          <CircleStop className="size-4" /> Stopped.
        </p>
      )}

      {turn.error && (
        <div
          role="alert"
          className={`rounded-md border p-3 text-sm ${isQuotaError(turn.error.code) ? "border-amber-500/40 bg-amber-500/10" : "border-destructive/40 bg-destructive/10"}`}
        >
          {errorMessage(turn.error.code, turn.error.message)}
        </div>
      )}

      {turn.done && (
        <footer className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          {turn.done.grounded ? (
            <span className="flex items-center gap-1 text-emerald-700 dark:text-emerald-400">
              <ShieldCheck className="size-3.5" /> Grounded in the sources
            </span>
          ) : (
            <span className="flex items-center gap-1 text-amber-700 dark:text-amber-400">
              <AlertTriangle className="size-3.5" /> No verified citations: check this answer yourself
            </span>
          )}
          <span>
            {turn.done.steps} tool {turn.done.steps === 1 ? "call" : "calls"} · {turn.done.seconds.toFixed(1)}s ·{" "}
            {turn.done.provider}/{turn.done.model}
            {turn.done.citation_retry && " · asked once for citations"}
          </span>
        </footer>
      )}
    </article>
  );
}
