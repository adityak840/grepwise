"use client";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { Chip } from "@/lib/answer";
import { STATUS_TEXT } from "@/lib/citations";
import { cn } from "@/lib/utils";

const STYLE: Record<Chip["status"], string> = {
  verified:
    "border-emerald-600/40 bg-emerald-500/10 text-emerald-700 hover:bg-emerald-500/20 dark:text-emerald-300",
  pending: "border-border bg-muted text-muted-foreground",
  not_found: "border-red-500/40 bg-red-500/10 text-red-700 line-through dark:text-red-300",
  bad_range: "border-red-500/40 bg-red-500/10 text-red-700 line-through dark:text-red-300",
  unseen: "border-red-500/40 bg-red-500/10 text-red-700 line-through dark:text-red-300",
};

export function CitationChip({ chip, onOpen }: { chip: Chip; onOpen: (chip: Chip) => void }) {
  const reason = chip.status === "pending" ? "Checking this citation…" : STATUS_TEXT[chip.status];
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={() => onOpen(chip)}
          className={cn(
            "focus-visible:ring-ring mx-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded-full border px-1.5 align-text-top font-mono text-[11px] leading-none no-underline outline-none focus-visible:ring-2",
            STYLE[chip.status],
          )}
          aria-label={`Citation ${chip.n}: ${chip.citation}. ${reason}`}
          data-status={chip.status}
        >
          {chip.n}
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-sm">
        <p className="font-mono text-[11px] break-all">{chip.citation}</p>
        <p className="mt-1 opacity-80">{reason}</p>
      </TooltipContent>
    </Tooltip>
  );
}
