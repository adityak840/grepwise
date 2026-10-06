/**
 * Server-Sent Events parsing for POST /chat. EventSource only does GET, so the UI reads the
 * response body as a stream and parses frames itself.
 *
 * A frame is a block of `field: value` lines ended by a blank line. Network chunks can split a
 * frame (or a line) anywhere, so the parser keeps the unfinished tail between pushes.
 */

export interface SSEMessage {
  event: string; // "message" when the frame has no `event:` line
  data: string; // `data:` lines joined with "\n"
}

export interface SSEParser {
  /** Feed the next chunk of text; returns every frame it completed. */
  push(chunk: string): SSEMessage[];
  /** End of stream: returns a final frame if the stream didn't end with a blank line. */
  flush(): SSEMessage[];
}

export function createSSEParser(): SSEParser {
  let buffer = "";

  function parseFrame(frame: string): SSEMessage | null {
    let event = "message";
    const data: string[] = [];
    for (const line of frame.split("\n")) {
      if (line === "" || line.startsWith(":")) continue; // blank or comment (keep-alive)
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1); // one optional space after the colon
      if (field === "event") event = value;
      else if (field === "data") data.push(value);
      // `id` and `retry` aren't used here; unknown fields are ignored, as the spec requires.
    }
    return data.length ? { event, data: data.join("\n") } : null;
  }

  function drain(final: boolean): SSEMessage[] {
    const out: SSEMessage[] = [];
    let end: number;
    while ((end = buffer.indexOf("\n\n")) !== -1) {
      const message = parseFrame(buffer.slice(0, end));
      buffer = buffer.slice(end + 2);
      if (message) out.push(message);
    }
    if (final && buffer.trim()) {
      const message = parseFrame(buffer);
      if (message) out.push(message);
      buffer = "";
    }
    return out;
  }

  return {
    push(chunk) {
      // Line endings may be \n, \r\n or \r. A chunk ending in "\r" might be the first half of
      // "\r\n", so hold that one character back until the next chunk arrives.
      let text = buffer + chunk;
      const held = text.endsWith("\r") ? "\r" : "";
      if (held) text = text.slice(0, -1);
      buffer = text.replace(/\r\n?/g, "\n");
      const out = drain(false);
      buffer += held;
      return out;
    },
    flush() {
      buffer = buffer.replace(/\r\n?/g, "\n");
      return drain(true);
    },
  };
}

/** Parse a complete SSE text (convenience, and for tests). */
export function parseSSE(text: string): SSEMessage[] {
  const parser = createSSEParser();
  return [...parser.push(text), ...parser.flush()];
}
