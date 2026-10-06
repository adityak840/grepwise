import { describe, expect, it } from "vitest";

import { githubUrl } from "./github";

describe("githubUrl", () => {
  it("links a repo citation at its tag and lines", () => {
    expect(githubUrl("pallets/itsdangerous@2.2.0:src/itsdangerous/timed.py:L22-L40", "repo")).toBe(
      "https://github.com/pallets/itsdangerous/blob/2.2.0/src/itsdangerous/timed.py#L22-L40",
    );
  });

  it("links docs and single lines", () => {
    expect(githubUrl("fastapi/typer@0.27.2:docs/tutorial/options/required.md:L7-L7", "docs")).toBe(
      "https://github.com/fastapi/typer/blob/0.27.2/docs/tutorial/options/required.md#L7",
    );
  });

  it("encodes bracketed and unusual path segments", () => {
    expect(githubUrl("vercel/next.js@v16.0.0:app/[slug]/page tsx#1.tsx:L1-L2", "repo")).toBe(
      "https://github.com/vercel/next.js/blob/v16.0.0/app/%5Bslug%5D/page%20tsx%231.tsx#L1-L2",
    );
  });

  it("returns null for API specs, non owner/repo names and malformed citations", () => {
    expect(githubUrl("petstore@1.0.27:GET /pet/{petId}:L1-L27", "openapi")).toBeNull();
    expect(githubUrl("petstore@1.0.27:GET /pet/{petId}:L1-L27")).toBeNull();
    expect(githubUrl("not a citation", "repo")).toBeNull();
  });
});
