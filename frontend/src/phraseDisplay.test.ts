import { describe, expect, it } from "vitest";
import type { Phrase } from "./hooks/useCaptionSocket";
import { hasInFlightPhrase, isTerminalPhrase, visiblePhrases } from "./phraseDisplay";
import { directionById, sourceTextOf } from "./directions";

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

});

const failedPhrase = (directionId: "en>ko" | "ko>en"): Phrase =>
  directionId === "en>ko"
    ? {
        phraseId: 2, en: "three", ko: null, status: "error",
        provider: null, latencyMs: null, errorMessage: "provider exploded",
      }
    : {
        phraseId: 2, en: null, ko: "셋", status: "error",
        provider: null, latencyMs: null, errorMessage: "provider exploded",
      };

describe.each(["en>ko", "ko>en"] as const)("in the %s direction", (id) => {
  const direction = directionById(id);

  it("keeps a failed phrase visible with its source text intact", () => {
    // If isTerminalPhrase were `status === "final"` alone, this phrase would
    // never become visible and its source text would vanish — the exact bug
    // the final's transcript event exists to prevent. Asserting the source
    // is non-empty is what makes a regression fail loudly instead of
    // quietly passing on an empty row.
    const phrase = failedPhrase(id);
    expect(isTerminalPhrase(phrase)).toBe(true);

    const visible = visiblePhrases([phrase]);
    expect(visible).toHaveLength(1);
    const source = sourceTextOf(visible[0], direction);
    expect(source).not.toBeNull();
    expect(source!.length).toBeGreaterThan(0);
  });

  it("never reveals a provisional translation", () => {
    const provisional: Phrase = {
      phraseId: 0, en: "partial", ko: "부분", status: "provisional",
      provider: "anthropic", latencyMs: 40, errorMessage: null,
    };
    expect(isTerminalPhrase(provisional)).toBe(false);
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
