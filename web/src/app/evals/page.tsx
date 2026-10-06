import type { Metadata } from "next";

import { EvalsView } from "@/components/pages/evals-view";

export const metadata: Metadata = { title: "Evals" };

const METRICS = [
  ["Recall@5", "How often the right file or section is in the top 5 search results."],
  ["MRR", "Mean reciprocal rank: 1 if the right result is first, ½ if second, and so on, averaged."],
  ["Answer correct", "Whether the answer matches a hand-written reference answer."],
  ["Citations verified", "The share of citations that point at real lines a tool showed the agent."],
  ["Grounded", "The share of answers with at least one verified citation."],
] as const;

export default function EvalsPage() {
  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-8">
      <h1 className="text-2xl font-semibold tracking-tight">Evals</h1>
      <p className="text-muted-foreground mt-1 mb-6 max-w-prose">
        How well Grepwise finds and answers, measured on hand-written questions, per AI model.
      </p>
      <EvalsView />
      <h2 className="mt-10 mb-3 text-lg font-semibold">What the numbers mean</h2>
      <dl className="grid gap-3 sm:grid-cols-[max-content_1fr] sm:gap-x-6">
        {METRICS.map(([name, text]) => (
          <div key={name} className="contents">
            <dt className="font-medium">{name}</dt>
            <dd className="text-muted-foreground text-sm">{text}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
