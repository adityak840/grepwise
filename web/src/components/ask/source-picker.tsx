"use client";

import { ChevronDown, Database } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { SourceInfo } from "@/lib/api";

const TYPE_LABEL = { repo: "code", docs: "docs", openapi: "API" } as const;

interface SourcePickerProps {
  sources: SourceInfo[];
  selected: string[];
  onChange: (refs: string[]) => void;
}

/** Multi-select of indexed sources; none selected means all of them. */
export function SourcePicker({ sources, selected, onChange }: SourcePickerProps) {
  const label =
    selected.length === 0 ? "All sources" : selected.length === 1 ? selected[0] : `${selected.length} sources`;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="max-w-[60vw] justify-start sm:max-w-xs">
          <Database className="shrink-0" />
          <span className="truncate">{label}</span>
          <ChevronDown className="ml-auto shrink-0 opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-72">
        <DropdownMenuLabel>Search in</DropdownMenuLabel>
        <DropdownMenuItem onSelect={() => onChange([])} disabled={selected.length === 0}>
          All sources
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        {sources.length === 0 && (
          <DropdownMenuItem disabled>Couldn&apos;t load the sources</DropdownMenuItem>
        )}
        {sources.map((s) => (
          <DropdownMenuCheckboxItem
            key={`${s.type}:${s.ref}`}
            checked={selected.includes(s.ref)}
            onSelect={(e) => e.preventDefault()} // keep the menu open for multi-select
            onCheckedChange={(on) =>
              onChange(on ? [...new Set([...selected, s.ref])] : selected.filter((r) => r !== s.ref))
            }
          >
            <span className="truncate">{s.ref}</span>
            <span className="text-muted-foreground ml-auto pl-2 text-xs">{TYPE_LABEL[s.type]}</span>
          </DropdownMenuCheckboxItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
