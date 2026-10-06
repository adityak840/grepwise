/** The Grepwise backend (FastAPI). NEXT_PUBLIC_API_URL is inlined at build time. */

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");

export interface SourceInfo {
  ref: string; // name@version
  type: "repo" | "docs" | "openapi";
  status: string;
  documents: number;
  chunks: number;
  subpath: string | null;
  indexed_at: string;
  embedding_model: string | null;
}

export interface ReadResult {
  citation: string;
  start_line: number;
  end_line: number;
  total_lines: number;
  content: string;
  truncated: boolean;
  next_start_line?: number | null;
}

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

/** Turn a non-OK response into an ApiError, using the backend's {error: {code, message}}. */
export async function toApiError(response: Response): Promise<ApiError> {
  let code = response.status === 422 ? "bad_request" : `http_${response.status}`;
  let message = response.statusText;
  try {
    const body = await response.json();
    if (body?.error?.code) {
      code = body.error.code;
      message = body.error.message ?? message;
    }
  } catch {
    // not JSON: keep the HTTP status
  }
  return new ApiError(code, message, response.status);
}

async function getJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { signal });
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as T;
}

export async function getSources(signal?: AbortSignal): Promise<SourceInfo[]> {
  return (await getJSON<{ sources: SourceInfo[] }>("/api/sources", signal)).sources;
}

export function readLines(
  source: string,
  path: string,
  start: number,
  end: number,
  signal?: AbortSignal,
): Promise<ReadResult> {
  const query = new URLSearchParams({ source, path, start: String(start), end: String(end) });
  return getJSON<ReadResult>(`/api/read?${query}`, signal);
}

/** The latest eval report, or null if there isn't one yet. */
export async function getEvals(signal?: AbortSignal): Promise<Record<string, unknown> | null> {
  try {
    return await getJSON<Record<string, unknown>>("/api/evals", signal);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export async function checkHealth(signal?: AbortSignal): Promise<boolean> {
  try {
    const response = await fetch(`${API_URL}/health`, { signal, cache: "no-store" });
    return response.ok && Boolean((await response.json()).ok);
  } catch {
    return false;
  }
}
