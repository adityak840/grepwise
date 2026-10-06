import { describe, expect, it } from "vitest";

import { newTurn, type Turn } from "./answer";
import { buildHistory, MAX_HISTORY_CHARS } from "./history";

const done = (question: string, text: string): Turn => ({ ...newTurn(question), status: "done", text });

describe("buildHistory", () => {
  it("sends earlier finished turns as user/assistant pairs", () => {
    const turns = [done("q1", "a1"), { ...newTurn("q2"), status: "error" as const }, done("q3", "a3")];
    expect(buildHistory(turns)).toEqual([
      { role: "user", content: "q1" }, { role: "assistant", content: "a1" },
      { role: "user", content: "q3" }, { role: "assistant", content: "a3" },
    ]);
  });

  it("keeps the newest messages within the server's limits", () => {
    const turns = Array.from({ length: 8 }, (_, i) => done(`q${i}`, "x".repeat(1_500)));
    const history = buildHistory(turns);
    expect(history.length).toBeLessThanOrEqual(10);
    expect(history.reduce((n, m) => n + m.content.length, 0)).toBeLessThanOrEqual(MAX_HISTORY_CHARS);
    expect(history[0].role).toBe("user");
    expect(history.at(-1)).toEqual({ role: "assistant", content: "x".repeat(1_500) });
  });
});
