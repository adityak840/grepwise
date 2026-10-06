"use client";

import { useCallback, useReducer, useRef } from "react";

import { newTurn, type Turn, type TurnAction, turnReducer } from "@/lib/answer";
import { API_URL, toApiError } from "@/lib/api";
import { buildHistory } from "@/lib/history";
import { getOwnKey } from "@/lib/own-key";
import { createSSEParser } from "@/lib/sse";

type Action = { type: "start"; question: string } | { type: "turn"; index: number; action: TurnAction } | { type: "clear" };

function reducer(turns: Turn[], action: Action): Turn[] {
  switch (action.type) {
    case "start":
      return [...turns, newTurn(action.question)];
    case "turn":
      return turns.map((t, i) => (i === action.index ? turnReducer(t, action.action) : t));
    case "clear":
      return [];
  }
}

export function useChat() {
  const [turns, dispatch] = useReducer(reducer, []);
  const controller = useRef<AbortController | null>(null);
  const turnsRef = useRef(turns);
  turnsRef.current = turns;

  const busy = turns.length > 0 && ["thinking", "looking", "streaming"].includes(turns[turns.length - 1].status);

  const ask = useCallback(async (question: string, sources: string[]) => {
    const previous = turnsRef.current;
    const index = previous.length;
    const history = buildHistory(previous);
    const to = (action: TurnAction) => dispatch({ type: "turn", index, action });
    dispatch({ type: "start", question });

    const abort = new AbortController();
    controller.current = abort;
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const ownKey = getOwnKey();
    if (ownKey) headers["X-Gemini-Key"] = ownKey;

    let finished = false;
    try {
      const response = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers,
        body: JSON.stringify({ question, sources: sources.length ? sources : null, history }),
        signal: abort.signal,
      });
      if (!response.ok || !response.body) {
        const error = await toApiError(response);
        to({ type: "failed", code: error.code, message: error.message });
        return;
      }
      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      const parser = createSSEParser();
      const handle = (messages: ReturnType<typeof parser.push>) => {
        for (const { event, data } of messages) {
          let parsed: unknown = null;
          try {
            parsed = JSON.parse(data);
          } catch {
            continue; // a malformed frame: skip it rather than break the answer
          }
          if (event === "done" || event === "error") finished = true;
          to({ type: "event", event, data: parsed });
        }
      };
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        handle(parser.push(value));
      }
      handle(parser.flush());
      if (!finished) to({ type: "failed", code: "internal", message: "The answer was cut off." });
    } catch (error) {
      if (abort.signal.aborted) to({ type: "stopped" });
      else to({ type: "failed", code: "network", message: String(error) });
    } finally {
      if (controller.current === abort) controller.current = null;
    }
  }, []);

  /** Abort the request; the server stops the agent when the connection closes. */
  const stop = useCallback(() => controller.current?.abort(), []);
  const clear = useCallback(() => {
    controller.current?.abort();
    dispatch({ type: "clear" });
  }, []);

  return { turns, busy, ask, stop, clear };
}
