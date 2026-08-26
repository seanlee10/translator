import { describe, expect, it } from "vitest";
import { applyPhraseError, mergePhrase } from "./useCaptionSocket";
import type { Phrase } from "./useCaptionSocket";

const existing = (over: Partial<Phrase> = {}): Phrase => ({
  phraseId: 0,
  en: null,
  ko: "안녕하세요",
  status: "interim",
  provider: null,
  latencyMs: null,
  errorMessage: null,
  ...over,
});

describe("merging a transcript update", () => {
  it("fills only the slot it carries and leaves the other alone", () => {
    // A KO→EN transcript event carries Korean only. Blanking `en` here
    // would wipe a translation that has already arrived.
    const current = existing({ en: "hello there", status: "final" });
    const merged = mergePhrase(current, {
      phraseId: 0,
      en: null,
      ko: "안녕하세요 반갑습니다",
      status: "interim",
      provider: null,
      latencyMs: null,
      errorMessage: null,
    });
    expect(merged.ko).toBe("안녕하세요 반갑습니다");
    expect(merged.en).toBe("hello there");
  });

  it("does not regress a final phrase's status", () => {
    const current = existing({ en: "hello", status: "final" });
    const merged = mergePhrase(current, {
      ...current,
      ko: "안녕",
      status: "interim",
    });
    expect(merged.status).toBe("final");
  });
});

describe("marking a phrase failed", () => {
  it("keeps the source text and clears the target side outright", () => {
    // KO→EN: Korean is the source and must survive; English is the target
    // and must be cleared to null, never left showing a stale provisional.
    const current = existing({
      en: "[stale provisional]",
      ko: "안녕하세요",
      status: "provisional",
      provider: "anthropic",
      latencyMs: 90,
    });
    const errored = applyPhraseError(current, "provider exploded", "ko>en");

    expect(errored.ko).toBe("안녕하세요");
    expect(errored.en).toBeNull();
    expect(errored.provider).toBeNull();
    expect(errored.latencyMs).toBeNull();
    expect(errored.status).toBe("error");
    expect(errored.errorMessage).toBe("provider exploded");
  });

  it("keeps English and clears Korean in the other direction", () => {
    const current = existing({
      en: "hello there",
      ko: "[stale provisional]",
      status: "provisional",
      provider: "groq",
      latencyMs: 90,
    });
    const errored = applyPhraseError(current, "timeout", "en>ko");

    expect(errored.en).toBe("hello there");
    expect(errored.ko).toBeNull();
    expect(errored.status).toBe("error");
  });
});
