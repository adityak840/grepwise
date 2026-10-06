import type { Turn } from "./answer";

// The server accepts at most 10 history messages and 8,000 characters in total.
export const MAX_HISTORY_MESSAGES = 10;
export const MAX_HISTORY_CHARS = 8_000;

export interface HistoryMessage {
  role: "user" | "assistant";
  content: string;
}

/** Earlier finished turns as chat history, newest kept first when trimming to the limits. */
export function buildHistory(turns: Turn[]): HistoryMessage[] {
  const messages: HistoryMessage[] = [];
  for (const turn of turns) {
    if (turn.status !== "done" || !turn.text) continue;
    messages.push({ role: "user", content: turn.question }, { role: "assistant", content: turn.text });
  }
  const kept: HistoryMessage[] = [];
  let chars = 0;
  for (let i = messages.length - 1; i >= 0 && kept.length < MAX_HISTORY_MESSAGES; i--) {
    const room = MAX_HISTORY_CHARS - chars;
    if (room <= 0) break;
    const content = messages[i].content.slice(0, room);
    kept.unshift({ ...messages[i], content });
    chars += content.length;
  }
  // History must start with a question, not an orphaned answer.
  while (kept.length && kept[0].role !== "user") kept.shift();
  return kept;
}
