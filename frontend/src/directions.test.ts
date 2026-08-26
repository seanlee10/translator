import { describe, expect, it } from "vitest";
import {
  DEFAULT_DIRECTION,
  DIRECTIONS,
  directionById,
  sourceTextOf,
  targetTextOf,
} from "./directions";
import type { Phrase } from "./hooks/useCaptionSocket";

const phrase = (over: Partial<Phrase> = {}): Phrase => ({
  phraseId: 0,
  en: "hello there",
  ko: "안녕하세요",
  status: "final",
  provider: "anthropic",
  latencyMs: 12,
  errorMessage: null,
  ...over,
});

describe("the direction constant", () => {
  it("mirrors the backend's two ids exactly", () => {
    // This list is a deliberate mirror of the backend's DIRECTIONS registry
    // rather than a fetched endpoint: unlike providers, whose availability
    // varies with configured API keys, the direction set has no runtime
    // variance. If the backend ever grows a third, this is the second of
    // the two places to edit.
    expect(DIRECTIONS.map((d) => d.id).sort()).toEqual(["en>ko", "ko>en"]);
  });

  it("defaults to English to Korean, matching the backend default", () => {
    expect(DEFAULT_DIRECTION.id).toBe("en>ko");
  });

  it("describes which language is spoken and which is produced", () => {
    const enKo = directionById("en>ko");
    expect(enKo.sourceLang).toBe("en");
    expect(enKo.targetLang).toBe("ko");

    const koEn = directionById("ko>en");
    expect(koEn.sourceLang).toBe("ko");
    expect(koEn.targetLang).toBe("en");
  });

  it("falls back to the default for an unknown id rather than throwing", () => {
    // A stale persisted preference or a hand-edited value must not blank
    // the whole UI.
    expect(directionById("fr>en").id).toBe("en>ko");
  });

  it("carries a placeholder in the language being produced", () => {
    expect(directionById("en>ko").translatingPlaceholder).toBe("번역 중…");
    expect(directionById("ko>en").translatingPlaceholder).toBe("Translating…");
  });
});

describe("reading a phrase by role", () => {
  it("reads English as the source when translating into Korean", () => {
    const d = directionById("en>ko");
    expect(sourceTextOf(phrase(), d)).toBe("hello there");
    expect(targetTextOf(phrase(), d)).toBe("안녕하세요");
  });

  it("reads Korean as the source when translating into English", () => {
    // The slots are language slots on the wire; role is derived here, in
    // exactly one place, mirroring the backend's Direction.to_slots.
    const d = directionById("ko>en");
    expect(sourceTextOf(phrase(), d)).toBe("안녕하세요");
    expect(targetTextOf(phrase(), d)).toBe("hello there");
  });

  it("returns null for a target that has not arrived yet", () => {
    const d = directionById("ko>en");
    const p = phrase({ en: null, status: "interim" });
    expect(sourceTextOf(p, d)).toBe("안녕하세요");
    expect(targetTextOf(p, d)).toBeNull();
  });
});
