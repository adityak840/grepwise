import { describe, expect, test } from "vitest";

import { createTokenBatcher } from "./token-batcher";

function setup() {
  const out: { event: string; data: unknown }[] = [];
  const frames: (() => void)[] = [];
  const batcher = createTokenBatcher(
    (event, data) => out.push({ event, data }),
    (cb) => {
      frames.push(cb);
      return frames.length;
    },
    () => {},
  );
  return { out, frames, batcher };
}

describe("createTokenBatcher", () => {
  test("joins the tokens that arrive within one frame into a single render", () => {
    const { out, frames, batcher } = setup();
    batcher.push("token", { text: "Hel" });
    batcher.push("token", { text: "lo " });
    batcher.push("token", { text: "world" });
    expect(out).toEqual([]); // nothing rendered until the frame
    frames.forEach((f) => f());
    expect(out).toEqual([{ event: "token", data: { text: "Hello world" } }]);
  });

  test("flushes pending tokens before any other event, keeping the order", () => {
    const { out, batcher } = setup();
    batcher.push("token", { text: "draft" });
    batcher.push("reset", {});
    batcher.push("token", { text: "answer" });
    batcher.flush();
    expect(out).toEqual([
      { event: "token", data: { text: "draft" } },
      { event: "reset", data: {} },
      { event: "token", data: { text: "answer" } },
    ]);
  });

  test("a late frame after an explicit flush renders nothing twice", () => {
    const { out, frames, batcher } = setup();
    batcher.push("token", { text: "a" });
    batcher.flush();
    frames.forEach((f) => f());
    expect(out).toEqual([{ event: "token", data: { text: "a" } }]);
  });
});
