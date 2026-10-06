/**
 * Syntax highlighting with Shiki, loaded on first use with only the languages Grepwise indexes
 * and the pure-JavaScript regex engine (no WebAssembly), to keep the bundle small.
 */

import type { HighlighterCore, ShikiTransformer } from "shiki/core";

let highlighter: Promise<HighlighterCore> | null = null;

const LANGS: Record<string, string> = {
  py: "python", pyi: "python", ts: "typescript", mts: "typescript", cts: "typescript",
  tsx: "tsx", js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "tsx",
  json: "json", md: "markdown", mdx: "mdx", yml: "yaml", yaml: "yaml", toml: "toml",
  sh: "bash", bash: "bash", css: "css", html: "html",
};
const ALIASES: Record<string, string> = { python: "python", typescript: "typescript",
  javascript: "javascript", shell: "bash", console: "bash", zsh: "bash", text: "text" };

export function langFromPath(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  return LANGS[ext] ?? "text";
}

export function langFromFence(name: string | undefined): string {
  if (!name) return "text";
  const lower = name.toLowerCase();
  return ALIASES[lower] ?? LANGS[lower] ?? (Object.values(LANGS).includes(lower) ? lower : "text");
}

function load(): Promise<HighlighterCore> {
  highlighter ??= (async () => {
    const [{ createHighlighterCore }, { createJavaScriptRegexEngine }] = await Promise.all([
      import("shiki/core"),
      import("shiki/engine/javascript"),
    ]);
    return createHighlighterCore({
      themes: [import("@shikijs/themes/github-light"), import("@shikijs/themes/github-dark")],
      langs: [
        import("@shikijs/langs/python"), import("@shikijs/langs/typescript"),
        import("@shikijs/langs/tsx"), import("@shikijs/langs/javascript"),
        import("@shikijs/langs/json"), import("@shikijs/langs/markdown"),
        import("@shikijs/langs/mdx"), import("@shikijs/langs/yaml"),
        import("@shikijs/langs/toml"), import("@shikijs/langs/bash"),
        import("@shikijs/langs/css"), import("@shikijs/langs/html"),
      ],
      engine: createJavaScriptRegexEngine(),
    });
  })();
  return highlighter;
}

/**
 * HTML for `code`, themed for light and dark (CSS variables). Each line is a `span.line`;
 * lines numbered within [markFrom, markTo] (1-based, relative to `code`) get `.marked`.
 */
export async function highlight(
  code: string,
  lang: string,
  mark?: { from: number; to: number },
): Promise<string> {
  const hl = await load();
  const transformers: ShikiTransformer[] = mark
    ? [{ line(node, line) { if (line >= mark.from && line <= mark.to) this.addClassToHast(node, "marked"); } }]
    : [];
  return hl.codeToHtml(code, {
    lang: hl.getLoadedLanguages().includes(lang) ? lang : "text",
    themes: { light: "github-light", dark: "github-dark" },
    defaultColor: false,
    transformers,
  });
}
