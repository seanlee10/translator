import { describe, expect, it } from "vitest";
import { nextTheme, normalizeTheme } from "./theme";

describe("normalizeTheme", () => {
  it("restores only supported saved themes", () => {
    expect(normalizeTheme("light")).toBe("light");
    expect(normalizeTheme("dark")).toBe("dark");
    expect(normalizeTheme("sepia")).toBe("dark");
    expect(normalizeTheme(null)).toBe("dark");
  });
});

describe("nextTheme", () => {
  it("toggles between dark and light", () => {
    expect(nextTheme("dark")).toBe("light");
    expect(nextTheme("light")).toBe("dark");
  });
});
