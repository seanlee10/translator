import { describe, expect, it } from "vitest";
import { labelFor, pickInitialProvider, toLabelMap, type ProviderInfo } from "./providers";

const THREE: ProviderInfo[] = [
  { id: "anthropic", label: "Claude Haiku 4.5", model: "claude-haiku-4-5" },
  { id: "groq", label: "Qwen 3.6 · Groq", model: "qwen/qwen3.6-27b" },
  { id: "xai", label: "Grok 4.20 · xAI", model: "grok-4.20-0309-non-reasoning" },
];

describe("pickInitialProvider", () => {
  it("picks the first provider the server offers", () => {
    expect(pickInitialProvider(THREE)).toBe("anthropic");
  });

  it("picks the first even when the list has a single entry", () => {
    expect(pickInitialProvider([THREE[1]])).toBe("groq");
  });

  it("returns null when the server has no provider configured", () => {
    expect(pickInitialProvider([])).toBeNull();
  });
});

describe("toLabelMap", () => {
  it("maps every provider id to its label", () => {
    expect(toLabelMap(THREE)).toEqual({
      anthropic: "Claude Haiku 4.5",
      groq: "Qwen 3.6 · Groq",
      xai: "Grok 4.20 · xAI",
    });
  });

  it("is empty for an empty provider list", () => {
    expect(toLabelMap([])).toEqual({});
  });
});

describe("labelFor", () => {
  it("returns the configured label for a known provider", () => {
    expect(labelFor(THREE, "groq")).toBe("Qwen 3.6 · Groq");
  });

  it("falls back to the raw id when the provider isn't in the current list", () => {
    // e.g. a caption translated in an earlier session, before a key was
    // removed from the server's configuration.
    expect(labelFor(THREE, "cohere")).toBe("cohere");
  });

  it("falls back to the raw id when the list is empty", () => {
    expect(labelFor([], "anthropic")).toBe("anthropic");
  });

  it("returns an empty string for a null provider id", () => {
    expect(labelFor(THREE, null)).toBe("");
  });
});
