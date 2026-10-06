"use client";

import { ArrowUp, CircleStop, CloudSun, RotateCcw } from "lucide-react";
import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useChat } from "@/hooks/use-chat";
import { useHealth } from "@/hooks/use-health";
import type { Chip } from "@/lib/answer";
import { getSources, type SourceInfo } from "@/lib/api";

import { CitationViewer } from "./citation-viewer";
import { SourcePicker } from "./source-picker";
import { TurnView } from "./turn-view";

const EXAMPLES = [
  "How do I make an option required in Typer?",
  "How does TimestampSigner check whether a signature has expired?",
  "What does GET /pet/{petId} return?",
];
const MAX_QUESTION = 2_000;

export function AskView() {
  const { turns, busy, ask, stop, clear } = useChat();
  const health = useHealth();
  const [question, setQuestion] = useState("");
  const [sources, setSources] = useState<SourceInfo[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [openChip, setOpenChip] = useState<Chip | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (health !== "ok") return;
    const abort = new AbortController();
    getSources(abort.signal).then(setSources, () => setSources([]));
    return () => abort.abort();
  }, [health]);

  const sourceTypes = useMemo(() => new Map(sources.map((s) => [s.ref, s.type])), [sources]);
  const last = turns.at(-1);
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }, [turns.length, last?.status]);

  function submit(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    setQuestion("");
    void ask(q, selected);
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    submit(question);
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-6 px-4 py-6">
      {health === "waking" && (
        <div role="status" className="flex items-center gap-2 rounded-md border border-sky-500/30 bg-sky-500/10 p-3 text-sm">
          <CloudSun className="size-4 shrink-0" />
          Waking up the server (free hosting sleeps when idle)… this can take up to a minute.
        </div>
      )}
      {health === "down" && (
        <div role="alert" className="border-destructive/40 bg-destructive/10 rounded-md border p-3 text-sm">
          The Grepwise server isn&apos;t responding right now. Please try again later.
        </div>
      )}

      {turns.length === 0 ? (
        <section className="flex flex-col gap-4 pt-6 sm:pt-12">
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Ask about code, docs and APIs.</h1>
          <p className="text-muted-foreground max-w-prose">
            Grepwise searches indexed repositories, library docs and API specs, and answers with
            citations to exact lines. Every citation is checked before you see it.
          </p>
          <div className="flex flex-wrap gap-2" aria-label="Example questions">
            {EXAMPLES.map((q) => (
              <Button key={q} variant="outline" size="sm" className="h-auto max-w-full shrink justify-start py-1.5 text-left whitespace-normal" onClick={() => submit(q)}>
                {q}
              </Button>
            ))}
          </div>
        </section>
      ) : (
        <div className="flex flex-col gap-10">
          {turns.map((turn, i) => (
            <TurnView key={i} turn={turn} onOpenCitation={setOpenChip} />
          ))}
        </div>
      )}
      <div ref={endRef} />

      <form onSubmit={onSubmit} className="bg-background sticky bottom-0 mt-auto flex flex-col gap-2 rounded-xl border p-2 shadow-sm">
        <label htmlFor="question" className="sr-only">
          Your question
        </label>
        <Textarea
          id="question"
          value={question}
          maxLength={MAX_QUESTION}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit(question);
            }
          }}
          placeholder={turns.length ? "Ask a follow-up…" : "How do I make an option required in Typer?"}
          className="min-h-12 resize-none border-0 shadow-none focus-visible:ring-0"
          rows={2}
        />
        <div className="flex items-center gap-2">
          <SourcePicker sources={sources} selected={selected} onChange={setSelected} />
          {turns.length > 0 && !busy && (
            <Button type="button" variant="ghost" size="sm" onClick={clear}>
              <RotateCcw /> New chat
            </Button>
          )}
          {busy ? (
            <Button type="button" size="sm" variant="secondary" className="ml-auto" onClick={stop}>
              <CircleStop /> Stop
            </Button>
          ) : (
            <Button type="submit" size="icon-sm" className="ml-auto" disabled={!question.trim()} aria-label="Ask">
              <ArrowUp />
            </Button>
          )}
        </div>
      </form>

      <CitationViewer chip={openChip} sourceTypes={sourceTypes} onClose={() => setOpenChip(null)} />
    </div>
  );
}
