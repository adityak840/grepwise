/**
 * The visitor's recent questions, kept in this browser's localStorage only (never sent anywhere
 * or stored by Grepwise). Answers aren't saved: just the questions, to ask again with one click.
 */

export const STORAGE_KEY = "grepwise.questionHistory";
export const MAX_SAVED = 30;
const CHANGE_EVENT = "grepwise:question-history";

export interface SavedQuestion {
  question: string;
  askedAt: number; // ms since epoch
}

function read(): SavedQuestion[] {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
    if (!Array.isArray(raw)) return [];
    return raw.filter(
      (q): q is SavedQuestion => typeof q?.question === "string" && q.question.length > 0 && typeof q.askedAt === "number",
    );
  } catch {
    return []; // storage blocked, or not our data: start fresh
  }
}

function write(questions: SavedQuestion[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(questions.slice(0, MAX_SAVED)));
  } catch {
    // storage full or blocked: history just isn't remembered
  }
  window.dispatchEvent(new Event(CHANGE_EVENT)); // same-tab listeners (other tabs get "storage")
}

export function loadQuestions(): SavedQuestion[] {
  return read();
}

export function addQuestion(question: string, askedAt: number = Date.now()): void {
  const q = question.trim();
  if (!q) return;
  write([{ question: q, askedAt }, ...read().filter((s) => s.question !== q)]);
}

export function removeQuestion(question: string): void {
  write(read().filter((s) => s.question !== question));
}

export function clearQuestions(): void {
  write([]);
}

/** Call `onChange` whenever the history changes, in this tab or another one. Returns an unsubscribe. */
export function subscribe(onChange: () => void): () => void {
  const onStorage = (e: StorageEvent) => e.key === STORAGE_KEY && onChange();
  window.addEventListener(CHANGE_EVENT, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(CHANGE_EVENT, onChange);
    window.removeEventListener("storage", onStorage);
  };
}
