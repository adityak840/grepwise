"use client";

import { useEffect, useState } from "react";

import { checkHealth } from "@/lib/api";

export type Health = "checking" | "ok" | "waking" | "down";

const WAKE_AFTER_MS = 2_000; // no answer by then: the free server is probably asleep
const POLL_MS = 3_000;
const GIVE_UP_MS = 120_000;

/** Calls /health on load. Free hosting sleeps when idle, so a slow first answer means "waking". */
export function useHealth(): Health {
  const [health, setHealth] = useState<Health>("checking");

  useEffect(() => {
    let cancelled = false;
    const started = Date.now();
    const wakeTimer = setTimeout(() => !cancelled && setHealth((h) => (h === "checking" ? "waking" : h)), WAKE_AFTER_MS);

    async function poll(): Promise<void> {
      while (!cancelled) {
        if (await checkHealth()) {
          if (!cancelled) setHealth("ok");
          return;
        }
        if (Date.now() - started > GIVE_UP_MS) {
          if (!cancelled) setHealth("down");
          return;
        }
        if (!cancelled) setHealth("waking");
        await new Promise((r) => setTimeout(r, POLL_MS));
      }
    }
    void poll();
    return () => {
      cancelled = true;
      clearTimeout(wakeTimer);
    };
  }, []);

  return health;
}
