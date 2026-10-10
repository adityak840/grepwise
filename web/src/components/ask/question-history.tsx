"use client";

import { History, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useQuestionHistory } from "@/hooks/use-question-history";
import { clearQuestions, removeQuestion } from "@/lib/question-history";

const MENU_ITEMS = 15;
const START_ITEMS = 6;
const NOTE = "Saved in this browser only.";

/** A "History" menu for the question bar: pick a past question to ask it again. */
export function HistoryMenu({ onPick, disabled }: { onPick: (question: string) => void; disabled?: boolean }) {
  const questions = useQuestionHistory();
  if (!questions.length) return null;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button type="button" variant="ghost" size="sm" disabled={disabled} aria-label="Recent questions">
          <History /> <span className="hidden sm:inline">History</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-80 max-w-[calc(100vw-2rem)]">
        <DropdownMenuLabel className="text-muted-foreground text-xs font-normal">Recent questions · {NOTE}</DropdownMenuLabel>
        {questions.slice(0, MENU_ITEMS).map((q) => (
          <DropdownMenuItem key={q.question} onSelect={() => onPick(q.question)} className="line-clamp-2 text-sm">
            {q.question}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={clearQuestions} className="text-muted-foreground text-xs">
          Clear history
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** "Your recent questions" on the start screen. */
export function RecentQuestions({ onPick }: { onPick: (question: string) => void }) {
  const questions = useQuestionHistory();
  if (!questions.length) return null;
  return (
    <section aria-labelledby="recent-heading" className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-2">
        <h2 id="recent-heading" className="text-muted-foreground text-sm font-medium">
          Your recent questions
        </h2>
        <button type="button" onClick={clearQuestions} className="text-muted-foreground hover:text-foreground text-xs underline-offset-2 hover:underline">
          Clear
        </button>
      </div>
      <ul className="flex flex-col gap-1">
        {questions.slice(0, START_ITEMS).map((q) => (
          <li key={q.question} className="group flex items-center gap-1">
            <button
              type="button"
              onClick={() => onPick(q.question)}
              className="hover:bg-muted focus-visible:ring-ring min-w-0 flex-1 truncate rounded-md px-2 py-1.5 text-left text-sm outline-none focus-visible:ring-2"
              title={q.question}
            >
              {q.question}
            </button>
            <button
              type="button"
              onClick={() => removeQuestion(q.question)}
              aria-label={`Remove “${q.question}” from history`}
              className="text-muted-foreground hover:text-foreground focus-visible:ring-ring rounded p-1 opacity-60 outline-none group-hover:opacity-100 focus-visible:opacity-100 focus-visible:ring-2"
            >
              <X className="size-3.5" />
            </button>
          </li>
        ))}
      </ul>
      <p className="text-muted-foreground text-xs">{NOTE}</p>
    </section>
  );
}
