"use client";

import { useEffect, useState } from "react";

import { highlight } from "@/lib/highlight";
import { cn } from "@/lib/utils";

interface CodeViewProps {
  code: string;
  lang: string;
  firstLine?: number; // number shown on the first line (line numbers on when set)
  mark?: { from: number; to: number }; // file line numbers to highlight
  className?: string;
  // false while an answer is still streaming: re-highlighting on every token makes the block
  // flicker (plain text and coloured HTML swapping), so colour it once it's complete.
  highlight?: boolean;
}

/** Highlighted code with optional line numbers; plain text until the highlighter loads. */
export function CodeView({ code, lang, firstLine, mark, className, highlight: enabled = true }: CodeViewProps) {
  const text = code.replace(/\n$/, "");
  const offset = (firstLine ?? 1) - 1;
  const relative = mark ? { from: mark.from - offset, to: mark.to - offset } : undefined;
  // The highlighted HTML is kept with the input it was made from, so stale colours for older
  // text are never shown while a newer version is being highlighted.
  const key = `${lang}|${relative?.from}|${relative?.to}|${text}`;
  const [result, setResult] = useState<{ key: string; html: string } | null>(null);
  const html = enabled && result?.key === key ? result.html : null;

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    highlight(text, lang, relative).then(
      (out) => !cancelled && setResult({ key, html: out }),
      () => !cancelled && setResult(null), // stay with plain text
    );
    return () => {
      cancelled = true;
    };
    // relative and key are derived from the inputs listed
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, text, lang, mark?.from, mark?.to, offset]);

  const numbered = firstLine !== undefined;
  const style = numbered ? { counterReset: `line ${offset}` } : undefined;
  return (
    <div
      className={cn("code-view bg-muted/40 overflow-x-auto rounded-md border text-[13px] leading-relaxed", numbered && "numbered", className)}
      style={style}
    >
      {html ? (
        // Shiki escapes the code; the HTML is its own markup, not the file's.
        <div dangerouslySetInnerHTML={{ __html: html }} />
      ) : (
        <pre className="shiki">
          <code>
            {text.split("\n").map((line, i) => (
              <span key={i} className={cn("line", relative && i + 1 >= relative.from && i + 1 <= relative.to && "marked")}>
                {line}
                {"\n"}
              </span>
            ))}
          </code>
        </pre>
      )}
    </div>
  );
}
