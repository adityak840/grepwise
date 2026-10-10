"use client";

import { useEffect, useState } from "react";

import { loadQuestions, type SavedQuestion, subscribe } from "@/lib/question-history";

/** The saved questions, kept in sync across components and browser tabs. Empty on the server
 *  and the first render (localStorage only exists in the browser), so hydration matches. */
export function useQuestionHistory(): SavedQuestion[] {
  const [questions, setQuestions] = useState<SavedQuestion[]>([]);
  useEffect(() => {
    const refresh = () => setQuestions(loadQuestions());
    refresh();
    return subscribe(refresh);
  }, []);
  return questions;
}
