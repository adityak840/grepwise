import { describe, expect, it } from "vitest";

import {
  CHIP_PREFIX, hidePartialMarker, newTurn, type Turn, type TurnAction, turnReducer, withChips,
} from "./answer";
import type { CitationCheck } from "./citations";

function run(actions: TurnAction[], turn: Turn = newTurn("q")): Turn {
  return actions.reduce(turnReducer, turn);
}

const ev = (event: string, data: unknown): TurnAction => ({ type: "event", event, data });

describe("turnReducer", () => {
  it("builds the tool trail from step and tool_result", () => {
    const turn = run([
      ev("step", { n: 1, tool: "search", args: { query: "required option" } }),
      ev("tool_result", { n: 1, tool: "search", ok: true, summary: "6 results" }),
      ev("step", { n: 2, tool: "read_file", args: { path: "a.md" } }),
    ]);
    expect(turn.status).toBe("looking");
    expect(turn.steps).toEqual([
      { n: 1, tool: "search", args: { query: "required option" }, ok: true, summary: "6 results" },
      { n: 2, tool: "read_file", args: { path: "a.md" } },
    ]);
  });

  it("appends tokens", () => {
    const turn = run([ev("token", { text: "Hello" }), ev("token", { text: " world" })]);
    expect(turn.text).toBe("Hello world");
    expect(turn.status).toBe("streaming");
  });

  it("reset withdraws the text streamed so far", () => {
    const turn = run([
      ev("token", { text: "Let me look that up." }),
      ev("reset", {}),
      ev("step", { n: 1, tool: "search", args: {} }),
      ev("token", { text: "The answer." }),
    ]);
    expect(turn.text).toBe("The answer.");
    expect(run([ev("token", { text: "x" }), ev("reset", {})]).status).toBe("looking");
  });

  it("records citations, done and errors; ignores unknown events", () => {
    const citations: CitationCheck[] = [{ citation: "a@1:x.py:L1-L2", status: "verified", content: "x\n" }];
    const done = { run_id: 7, steps: 1, grounded: true, input_tokens: 1, output_tokens: 1,
                   seconds: 1, provider: "gemini", model: "m" };
    const turn = run([ev("token", { text: "hi" }), ev("future_event", { x: 1 }), ev("citations", citations), ev("done", done)]);
    expect(turn.citations).toEqual(citations);
    expect(turn.done).toEqual(done);
    expect(turn.status).toBe("done");
    const failed = run([ev("error", { code: "timeout", message: "slow" })]);
    expect(failed.error).toEqual({ code: "timeout", message: "slow" });
  });

  it("ignores events after Stop", () => {
    const turn = run([ev("token", { text: "partial" }), { type: "stopped" }, ev("token", { text: " more" })]);
    expect(turn.status).toBe("stopped");
    expect(turn.text).toBe("partial");
  });
});

describe("hidePartialMarker", () => {
  it("hides an unfinished citation at the end while streaming", () => {
    expect(hidePartialMarker("Use Annotated [[fastapi/typer@0.27")).toBe("Use Annotated ");
    expect(hidePartialMarker("Use Annotated [[a@1:x.py:L1-L2]")).toBe("Use Annotated ");
    expect(hidePartialMarker("Use Annotated [")).toBe("Use Annotated ");
  });

  it("leaves finished markers and other text alone", () => {
    expect(hidePartialMarker("Done [[a@1:x.py:L1-L2]].")).toBe("Done [[a@1:x.py:L1-L2]].");
    expect(hidePartialMarker("x[[0]] = 1")).toBe("x[[0]] = 1");
    expect(hidePartialMarker("plain")).toBe("plain");
  });
});

describe("withChips", () => {
  const answer =
    "Use Annotated [[fastapi/typer@0.27.2:docs/required.md:L1-L15]]. " +
    "Again [[fastapi/typer@0.27.2:docs/required.md:L1-L15]], made up [[a@1:nope.py:L1-L2]], " +
    "route [[web@1:app/[slug]/page.tsx:L3-L9]] and an attempt [[README lines 1-3]]. Code: x[[0]].";

  it("numbers pending chips by first appearance before citations arrive", () => {
    const { markdown, chips } = withChips(answer, null);
    expect(chips.map((c) => [c.n, c.citation, c.status])).toEqual([
      [1, "fastapi/typer@0.27.2:docs/required.md:L1-L15", "pending"],
      [2, "a@1:nope.py:L1-L2", "pending"],
      [3, "web@1:app/[slug]/page.tsx:L3-L9", "pending"],
    ]);
    expect(markdown).toContain(`Annotated [1](${CHIP_PREFIX}1). Again [1](${CHIP_PREFIX}1)`);
    expect(markdown).toContain(`route [3](${CHIP_PREFIX}3)`);
    expect(markdown).toContain("x[[0]]"); // not a citation
  });

  it("gives each chip the status the server reported", () => {
    const citations: CitationCheck[] = [
      { citation: "fastapi/typer@0.27.2:docs/required.md:L1-L15", status: "verified", content: "# Required\n" },
      { citation: "a@1:nope.py:L1-L2", status: "not_found" },
      { citation: "web@1:app/[slug]/page.tsx:L3-L9", status: "unseen" },
      { citation: "README lines 1-3", status: "not_found" },
    ];
    const { markdown, chips } = withChips(answer, citations);
    expect(chips.map((c) => [c.n, c.status])).toEqual([[1, "verified"], [2, "not_found"], [3, "unseen"], [4, "not_found"]]);
    expect(chips[0].content).toBe("# Required\n");
    expect(markdown).toContain(`attempt [4](${CHIP_PREFIX}4)`);
    expect(markdown).not.toMatch(/\[\[[^\]]*[@ ]/); // every citation marker became a chip
    expect(markdown).toContain("x[[0]]"); // code is left alone
  });

  it("leaves text without citations alone", () => {
    expect(withChips("No citations here.", [])).toEqual({ markdown: "No citations here.", chips: [] });
  });
});
