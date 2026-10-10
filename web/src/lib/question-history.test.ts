import { beforeEach, describe, expect, test } from "vitest";

import { addQuestion, clearQuestions, loadQuestions, MAX_SAVED, removeQuestion, STORAGE_KEY } from "./question-history";

describe("question history (localStorage)", () => {
  beforeEach(() => localStorage.clear());

  test("newest first, and asking again moves a question to the top instead of duplicating it", () => {
    addQuestion("first", 1);
    addQuestion("second", 2);
    addQuestion("  first ", 3);
    expect(loadQuestions().map((q) => q.question)).toEqual(["first", "second"]);
    expect(loadQuestions()[0].askedAt).toBe(3);
  });

  test("keeps only the most recent MAX_SAVED", () => {
    for (let i = 0; i < MAX_SAVED + 5; i++) addQuestion(`q${i}`, i);
    const saved = loadQuestions();
    expect(saved).toHaveLength(MAX_SAVED);
    expect(saved[0].question).toBe(`q${MAX_SAVED + 4}`);
  });

  test("remove one, or clear all", () => {
    addQuestion("a", 1);
    addQuestion("b", 2);
    removeQuestion("a");
    expect(loadQuestions().map((q) => q.question)).toEqual(["b"]);
    clearQuestions();
    expect(loadQuestions()).toEqual([]);
  });

  test("corrupt or foreign data in storage is ignored, not a crash", () => {
    localStorage.setItem(STORAGE_KEY, "{not json");
    expect(loadQuestions()).toEqual([]);
    localStorage.setItem(STORAGE_KEY, JSON.stringify([{ question: 42 }, { question: "ok", askedAt: 5 }]));
    expect(loadQuestions()).toEqual([{ question: "ok", askedAt: 5 }]);
  });

  test("empty questions are not saved", () => {
    addQuestion("   ", 1);
    expect(loadQuestions()).toEqual([]);
  });
});
