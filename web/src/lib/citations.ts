/**
 * Citation markers in answers, and citation strings. Mirrors the backend:
 * `agent/citations.py` (MARKER) and `search/citations.py` (parse from the right, since paths
 * may contain ':' , spaces, '{}' or '[]').
 */

export type CitationStatus = "verified" | "not_found" | "bad_range" | "unseen";

export interface CitationCheck {
  citation: string;
  status: CitationStatus;
  content?: string; // the verified lines, for `verified` only
}

export interface ParsedCitation {
  name: string;
  version: string;
  path: string;
  start: number;
  end: number;
}

/** A well-formed `[[name@version:path:Lx-Ly]]` marker (paths may contain brackets). */
export const MARKER = /\[\[((?:(?!\[\[)[^\n])+?:L\d+-L\d+)\s*\]\]/g;

const CITATION = /^([^@:]+)@([^@:]+):(.+):L(\d+)-L(\d+)$/;

export function parseCitation(text: string): ParsedCitation | null {
  const m = CITATION.exec(text.trim());
  if (!m) return null;
  const start = Number(m[4]);
  const end = Number(m[5]);
  if (start < 1 || end < start) return null;
  return { name: m[1], version: m[2], path: m[3], start, end };
}

export function sourceOf(c: ParsedCitation): string {
  return `${c.name}@${c.version}`;
}

export const STATUS_TEXT: Record<CitationStatus, string> = {
  verified: "Verified: these lines exist and a tool showed them to the agent.",
  not_found: "Not found in the indexed sources.",
  bad_range: "The line range is past the end of the file.",
  unseen: "Real, but no tool showed these lines to the agent, so it can't have learned it there.",
};
