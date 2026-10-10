/**
 * One question-and-answer turn, driven by /chat's stream events. A pure reducer, so it's
 * easy to test: (turn, action) -> turn.
 */

import { type CitationCheck, type CitationStatus, MARKER } from "./citations";

export interface ToolStep {
  n: number;
  tool: string;
  args: Record<string, unknown> | null;
  ok?: boolean;
  summary?: string;
}

export interface DoneInfo {
  run_id: number;
  steps: number;
  grounded: boolean;
  input_tokens: number;
  output_tokens: number;
  seconds: number;
  provider: string;
  model: string;
  citation_retry?: boolean; // the agent was asked once to add citations to its first draft
}

export type TurnStatus =
  | "thinking" // sent; nothing back yet
  | "looking" // calling tools, or text was withdrawn by `reset`
  | "streaming" // answer text arriving
  | "done"
  | "error"
  | "stopped";

export interface Turn {
  question: string;
  status: TurnStatus;
  steps: ToolStep[];
  text: string;
  citations: CitationCheck[] | null; // null until the `citations` event
  done: DoneInfo | null;
  error: { code: string; message: string } | null;
}

export type TurnAction =
  | { type: "event"; event: string; data: unknown }
  | { type: "stopped" }
  | { type: "failed"; code: string; message: string };

export function newTurn(question: string): Turn {
  return {
    question,
    status: "thinking",
    steps: [],
    text: "",
    citations: null,
    done: null,
    error: null,
  };
}

const FINISHED: TurnStatus[] = ["done", "error", "stopped"];

export function turnReducer(turn: Turn, action: TurnAction): Turn {
  if (FINISHED.includes(turn.status)) return turn; // late events after Stop are ignored
  if (action.type === "stopped") return { ...turn, status: "stopped" };
  if (action.type === "failed") {
    return { ...turn, status: "error", error: { code: action.code, message: action.message } };
  }
  const data = action.data as Record<string, unknown>;
  switch (action.event) {
    case "step":
      return {
        ...turn,
        status: turn.text ? turn.status : "looking",
        steps: [
          ...turn.steps,
          {
            n: Number(data.n),
            tool: String(data.tool),
            args: (data.args as Record<string, unknown> | null) ?? null,
          },
        ],
      };
    case "tool_result":
      return {
        ...turn,
        steps: turn.steps.map((s) =>
          s.n === data.n ? { ...s, ok: Boolean(data.ok), summary: String(data.summary) } : s,
        ),
      };
    case "token":
      return { ...turn, status: "streaming", text: turn.text + String(data.text ?? "") };
    case "reset":
      // The model was thinking aloud before a tool call: withdraw that text.
      return { ...turn, status: "looking", text: "" };
    case "citations":
      return { ...turn, citations: action.data as CitationCheck[] };
    case "done":
      return { ...turn, status: "done", done: action.data as DoneInfo };
    case "error":
      return {
        ...turn,
        status: "error",
        error: { code: String(data.code), message: String(data.message ?? "") },
      };
    default:
      return turn; // unknown events are ignored
  }
}

// --- Citation chips ---------------------------------------------------------------------

export interface Chip {
  n: number; // 1-based, in order of first appearance
  citation: string;
  status: CitationStatus | "pending"; // pending: the `citations` event hasn't arrived yet
  content?: string;
}

export const CHIP_PREFIX = "#cite-";

/**
 * While an answer streams, a citation arrives in pieces (`[[fastapi/typer@0.2`…). Hide an
 * unfinished marker at the very end until its closing `]]` arrives, so raw citation text
 * doesn't flash up and then collapse into a chip.
 */
export function hidePartialMarker(text: string): string {
  return text.replace(/\[(?:\[[^\]\n]*\]?)?$/, "");
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Replace each `[[citation]]` marker with a Markdown link `[n](#cite-n)`, which the answer
 * renderer turns into a numbered chip. Once citations are checked, every citation the server
 * reported gets a chip with its status (including malformed attempts it reported as
 * not_found); before that, well-formed markers get `pending` chips.
 */
export function withChips(
  text: string,
  citations: CitationCheck[] | null,
): { markdown: string; chips: Chip[] } {
  const chips: Chip[] = citations
    ? citations.map((c, i) => ({ n: i + 1, citation: c.citation, status: c.status, content: c.content }))
    : [...new Set([...text.matchAll(MARKER)].map((m) => m[1].trim()))].map((citation, i) => ({
        n: i + 1,
        citation,
        status: "pending" as const,
      }));
  if (!chips.length) return { markdown: text, chips };
  const byCitation = new Map(chips.map((c) => [c.citation, c]));
  const alternatives = [...byCitation.keys()]
    .sort((a, b) => b.length - a.length)
    .map(escapeRegExp)
    .join("|");
  const markers = new RegExp(`\\[\\[\\s*(${alternatives})\\s*\\]\\]`, "g");
  const markdown = text.replace(markers, (_, citation: string) => {
    const chip = byCitation.get(citation);
    return chip ? `[${chip.n}](${CHIP_PREFIX}${chip.n})` : _;
  });
  return { markdown, chips };
}
