import { describe, expect, it } from "vitest";

import { createSSEParser, parseSSE } from "./sse";

describe("parseSSE", () => {
  it("parses event and JSON data frames", () => {
    const text = 'event: step\ndata: {"n":1}\n\nevent: token\ndata: {"text":"hi"}\n\n';
    expect(parseSSE(text)).toEqual([
      { event: "step", data: '{"n":1}' },
      { event: "token", data: '{"text":"hi"}' },
    ]);
  });

  it("joins multi-line data with newlines", () => {
    expect(parseSSE("event: x\ndata: line one\ndata: line two\n\n")).toEqual([
      { event: "x", data: "line one\nline two" },
    ]);
  });

  it("passes unknown events through and defaults the name to message", () => {
    expect(parseSSE("event: future_thing\ndata: 1\n\ndata: 2\n\n")).toEqual([
      { event: "future_thing", data: "1" },
      { event: "message", data: "2" },
    ]);
  });

  it("ignores comments, unknown fields and frames without data", () => {
    expect(parseSSE(": keep-alive\n\nid: 7\nretry: 100\nevent: done\ndata: {}\n\nevent: empty\n\n")).toEqual([
      { event: "done", data: "{}" },
    ]);
  });

  it("handles CRLF line endings and no space after the colon", () => {
    expect(parseSSE("event:token\r\ndata:{\"text\":\"a\"}\r\n\r\n")).toEqual([
      { event: "token", data: '{"text":"a"}' },
    ]);
  });

  it("returns a final frame that wasn't followed by a blank line", () => {
    expect(parseSSE("event: done\ndata: {}")).toEqual([{ event: "done", data: "{}" }]);
  });
});

describe("createSSEParser", () => {
  it("reassembles frames split across chunks at any point", () => {
    const text = 'event: token\ndata: {"text":"Hello"}\n\nevent: token\ndata: {"text":" world"}\n\n';
    for (let cut = 1; cut < text.length; cut++) {
      const parser = createSSEParser();
      const got = [...parser.push(text.slice(0, cut)), ...parser.push(text.slice(cut)), ...parser.flush()];
      expect(got).toHaveLength(2);
      expect(got[1]).toEqual({ event: "token", data: '{"text":" world"}' });
    }
  });

  it("keeps a CRLF split between chunks intact", () => {
    const parser = createSSEParser();
    expect(parser.push("event: a\r\ndata: 1\r\n\r")).toEqual([]);
    expect(parser.push("\nevent: b\r\ndata: 2\r\n\r\n")).toEqual([
      { event: "a", data: "1" },
      { event: "b", data: "2" },
    ]);
  });

  it("emits nothing until a frame is complete", () => {
    const parser = createSSEParser();
    expect(parser.push("event: token\ndata: {")).toEqual([]);
    expect(parser.push('"text":"x"}\n')).toEqual([]);
    expect(parser.push("\n")).toEqual([{ event: "token", data: '{"text":"x"}' }]);
  });
});
