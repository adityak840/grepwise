/**
 * The visitor's own Gemini key (optional). Kept in sessionStorage only: it disappears when the
 * tab closes, and is sent to Grepwise only as a header on each question.
 */

const STORAGE_KEY = "grepwise.geminiKey";

export function getOwnKey(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null; // storage blocked (private mode, sandboxed frames)
  }
}

export function setOwnKey(key: string | null): void {
  try {
    if (key) sessionStorage.setItem(STORAGE_KEY, key);
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // nothing to do: without storage the key just isn't remembered
  }
}
