import { describe, expect, it } from "vitest";

import { errorMessage, isQuotaError } from "./errors";

describe("errorMessage", () => {
  it.each(["daily_limit", "rate_limited", "quota_exhausted", "timeout", "invalid_key", "unavailable"])(
    "has a friendly message for %s",
    (code) => {
      const message = errorMessage(code);
      expect(message).not.toMatch(/_/); // no raw codes shown to people
      expect(message.length).toBeGreaterThan(20);
    },
  );

  it("points quota errors at the own-key option", () => {
    expect(errorMessage("daily_limit")).toMatch(/own Gemini key/);
    expect(isQuotaError("daily_limit") && isQuotaError("quota_exhausted")).toBe(true);
    expect(isQuotaError("timeout")).toBe(false);
  });

  it("falls back for unknown codes", () => {
    expect(errorMessage("brand_new_code", "Server said no.")).toBe("Server said no.");
    expect(errorMessage("brand_new_code")).toMatch(/went wrong/);
  });
});
