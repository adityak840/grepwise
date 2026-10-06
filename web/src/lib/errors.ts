/** Friendly messages for the error codes /chat can return (HTTP 4xx bodies or `error` events). */

const MESSAGES: Record<string, string> = {
  daily_limit:
    "Grepwise has answered all the questions it can for today on its free quota. Come back " +
    "tomorrow, or add your own Gemini key in settings.",
  rate_limited: "You're asking questions a little too quickly. Wait a moment and try again.",
  quota_exhausted:
    "The AI model's free quota is used up for today. Try again tomorrow, or add your own " +
    "Gemini key in settings.",
  timeout: "The AI model took too long to respond. Please try again.",
  unavailable: "The AI model couldn't be reached. Please try again in a minute.",
  provider_error: "The AI model returned an error. Please try again.",
  invalid_key:
    "Your Gemini API key was rejected. Check it in settings, or remove it to use the shared " +
    "quota.",
  internal: "Something went wrong on our side. Please try again.",
  network: "Couldn't reach the Grepwise server. Check your connection and try again.",
  bad_request: "That request wasn't accepted. Try a shorter question.",
};

export function errorMessage(code: string, fallback?: string): string {
  return MESSAGES[code] ?? fallback ?? "Something went wrong. Please try again.";
}

/** Whether retrying later (not now) is the only fix, e.g. to show a "come back" style. */
export function isQuotaError(code: string): boolean {
  return code === "daily_limit" || code === "quota_exhausted";
}
