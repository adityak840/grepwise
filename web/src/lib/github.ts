import { parseCitation } from "./citations";

const OWNER_REPO = /^[A-Za-z0-9-]+\/[A-Za-z0-9._-]+$/;

/**
 * "Open on GitHub" link for a citation into a GitHub repo or its docs, at the indexed tag:
 * https://github.com/<name>/blob/<version>/<path>#L<start>-L<end>
 * Returns null for API specs and for sources whose name isn't `owner/repo`.
 */
export function githubUrl(citation: string, sourceType?: string): string | null {
  if (sourceType === "openapi") return null;
  const c = parseCitation(citation);
  if (!c || !OWNER_REPO.test(c.name)) return null;
  // Encode each path segment, so brackets (Next.js routes like app/[slug]/page.tsx), spaces
  // and '#' can't break the URL.
  const path = c.path.split("/").map(encodeURIComponent).join("/");
  const lines = c.start === c.end ? `#L${c.start}` : `#L${c.start}-L${c.end}`;
  return `https://github.com/${c.name}/blob/${encodeURIComponent(c.version)}/${path}${lines}`;
}
