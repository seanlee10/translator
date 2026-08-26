import { describe, expect, it } from "vitest";
import type { Phrase } from "./hooks/useCaptionSocket";
import { hasInFlightPhrase, isTerminalPhrase, visiblePhrases } from "./phraseDisplay";

function phrase(overrides: Partial<Phrase>): Phrase {
  return {
    phraseId: 0,
    en: "hello",
    ko: null,
    status: "interim",
    provider: null,
    latencyMs: null,
    errorMessage: null,
    ...overrides,
  };
}

describe("isTerminalPhrase", () => {
  it("is not terminal while the phrase is still an interim (not-yet-final)", () => {
    const p = phrase({ status: "interim", en: "hel", ko: null });
    expect(isTerminalPhrase(p)).toBe(false);
  });

  it("is not terminal for a provisional translation either — provisional Korean is never shown", () => {
    const p = phrase({
      status: "provisional",
      en: "hello th",
      ko: "[provisional ko]",
      provider: "anthropic",
      latencyMs: 120,
    });
    expect(isTerminalPhrase(p)).toBe(false);
  });

  it("is terminal once the final translation arrives, with Korean present", () => {
    const p = phrase({
      status: "final",
      en: "hello there",
      ko: "안녕하세요",
      provider: "anthropic",
      latencyMs: 340,
    });
    expect(isTerminalPhrase(p)).toBe(true);
    expect(p.ko).toBe("안녕하세요");
  });

  it("is terminal when translation failed, with the English present and no Korean — guards against silently losing the phrase", () => {
    // This is the case the whole design exists to protect: if "terminal"
    // were implemented as "status === 'final'" instead of "final or error",
    // this phrase would never become visible and its English would vanish
    // along with the failed translation. Asserting both isTerminalPhrase
    // AND the presence of `en` here means this test fails loudly under
    // that regression, not just silently under-counts rows.
    const p = phrase({
      status: "error",
      en: "hello there",
      ko: null,
      provider: null,
      latencyMs: null,
      errorMessage: "translation failed: timeout",
    });
    expect(isTerminalPhrase(p)).toBe(true);
    expect(p.en).toBe("hello there");
    expect(p.en?.length).toBeGreaterThan(0);
    expect(p.ko).toBeNull();
  });
});

describe("visiblePhrases", () => {
  it("excludes not-yet-final phrases and keeps final/error ones, preserving order", () => {
    const phrases: Phrase[] = [
      phrase({ phraseId: 0, status: "final", en: "one", ko: "일" }),
      phrase({ phraseId: 1, status: "interim", en: "in progress" }),
      phrase({
        phraseId: 2,
        status: "error",
        en: "three",
        ko: null,
        errorMessage: "translation failed: boom",
      }),
      phrase({ phraseId: 3, status: "provisional", en: "four", ko: "[prov]" }),
    ];

    const visible = visiblePhrases(phrases);

    expect(visible.map((p) => p.phraseId)).toEqual([0, 2]);
    // The failed-translation row survives filtering with its English intact.
    expect(visible[1].en).toBe("three");
    expect(visible[1].ko).toBeNull();
  });
});

describe("hasInFlightPhrase", () => {
  it("is false when there are no phrases yet", () => {
    expect(hasInFlightPhrase([])).toBe(false);
  });

  it("is true while a phrase exists that hasn't settled", () => {
    const phrases: Phrase[] = [
      phrase({ phraseId: 0, status: "final", en: "one", ko: "일" }),
      phrase({ phraseId: 1, status: "interim", en: "still speaking" }),
    ];
    expect(hasInFlightPhrase(phrases)).toBe(true);
  });

  it("is false once every known phrase has settled (final or error)", () => {
    const phrases: Phrase[] = [
      phrase({ phraseId: 0, status: "final", en: "one", ko: "일" }),
      phrase({
        phraseId: 1,
        status: "error",
        en: "two",
        ko: null,
        errorMessage: "translation failed: boom",
      }),
    ];
    expect(hasInFlightPhrase(phrases)).toBe(false);
  });
});
