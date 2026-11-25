import { describe, expect, it } from "vitest";
import {
  DEFAULT_DIRECTION,
  DIRECTIONS,
  directionById,
  displayDirection,
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
  it("mirrors the backend's three ids exactly", () => {
    // This list is a deliberate mirror of the backend's DIRECTIONS registry
    // rather than a fetched endpoint: unlike providers, whose availability
    // varies with configured API keys, the direction set has no runtime
    // variance. If the backend ever grows another, this is the second of
    // the two places to edit.
    expect(DIRECTIONS.map((d) => d.id).sort()).toEqual(["en>ko", "en>zh", "ko>en"]);
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
    expect(directionById("en>zh").translatingPlaceholder).toBe("翻譯中…");
  });

  it("describes English to Taiwanese Traditional Chinese", () => {
    const enZh = directionById("en>zh");
    expect(enZh.sourceLang).toBe("en");
    expect(enZh.targetLang).toBe("zh");
    expect(enZh.targetLabel).toBe("繁體中文");
    // Drives the glyph set: zh-Hant, not a bare "zh" the browser may
    // render with Simplified-Chinese fonts.
    expect(enZh.targetHtmlLang).toBe("zh-Hant-TW");
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

describe("displayDirection", () => {
  it("shows the pending selection when no phrases are on screen", () => {
    const pending = directionById("ko>en");
    const session = directionById("en>ko");
    expect(displayDirection(0, pending, session)).toBe(pending);
  });

  it("shows the session direction once phrases exist, pending ko>en / session en>ko", () => {
    const pending = directionById("ko>en");
    const session = directionById("en>ko");
    expect(displayDirection(1, pending, session)).toBe(session);
  });

  it("shows the session direction once phrases exist, the reverse ordering", () => {
    // Same assertion with the two directions swapped between the pending and
    // session arguments, so a swapped-argument bug in the implementation
    // (returning `pending` instead of `session`, or vice versa) fails.
    const pending = directionById("en>ko");
    const session = directionById("ko>en");
    expect(displayDirection(2, pending, session)).toBe(session);
  });

  it("treats any nonzero phrase count the same as one", () => {
    const pending = directionById("en>ko");
    const session = directionById("ko>en");
    expect(displayDirection(5, pending, session)).toBe(session);
  });
});

describe("reading an English-to-Chinese phrase", () => {
  const enZh = directionById("en>zh");

  it("reads the zh slot as the target and ignores ko", () => {
    const p = phrase({ en: "hello", ko: null, zh: "你好" });
    expect(sourceTextOf(p, enZh)).toBe("hello");
    expect(targetTextOf(p, enZh)).toBe("你好");
  });

  it("treats a missing zh field as no translation yet", () => {
    const p = phrase({ en: "hello", ko: null });
    expect(targetTextOf(p, enZh)).toBeNull();
  });
});
