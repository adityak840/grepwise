/**
 * Streamed answer text can arrive dozens of times a second. Rendering each piece re-parses the
 * whole Markdown answer and makes the page jitter, so tokens are joined and rendered at most
 * once per animation frame. Any other event first flushes pending tokens, so order is kept
 * (a `reset` must come after the draft text it withdraws).
 */

type Emit = (event: string, data: unknown) => void;

export interface TokenBatcher {
  push(event: string, data: unknown): void;
  flush(): void;
}

export function createTokenBatcher(
  emit: Emit,
  schedule: (cb: () => void) => number = (cb) => requestAnimationFrame(cb),
  cancel: (id: number) => void = (id) => cancelAnimationFrame(id),
): TokenBatcher {
  let pending = "";
  let frame: number | null = null;

  function flush() {
    if (frame !== null) {
      cancel(frame);
      frame = null;
    }
    if (pending) {
      const text = pending;
      pending = "";
      emit("token", { text });
    }
  }

  return {
    push(event, data) {
      if (event === "token") {
        pending += String((data as { text?: unknown })?.text ?? "");
        if (frame === null) {
          frame = schedule(() => {
            frame = null;
            flush();
          });
        }
        return;
      }
      flush();
      emit(event, data);
    },
    flush,
  };
}
