import { describe, expect, it } from "vitest";
import type { Phrase } from "../hooks/useCaptionSocket";
import {
  createOverlaySnapshot,
  selectOverlayPhrases,
} from "./overlay";

const phrase = (phraseId: number, overrides: Partial<Phrase> = {}): Phrase => ({
  phraseId,
  en: `English ${phraseId}`,
  ko: `한국어 ${phraseId}`,
  status: "final",
  provider: "anthropic",
  latencyMs: 320,
  ...overrides,
});

describe("selectOverlayPhrases", () => {
  it("keeps only the newest two phrases in phrase order", () => {
    expect(selectOverlayPhrases([phrase(1), phrase(2), phrase(3)])).toEqual([
      phrase(2),
      phrase(3),
    ]);
  });

  it("keeps an interim phrase so English appears before Korean is ready", () => {
    const interim = phrase(4, {
      ko: null,
      status: "interim",
      provider: null,
      latencyMs: null,
    });

    expect(selectOverlayPhrases([interim])).toEqual([interim]);
  });
});

describe("createOverlaySnapshot", () => {
  it("publishes presentation state without the full transcript history", () => {
    const snapshot = createOverlaySnapshot(
      [phrase(1), phrase(2), phrase(3)],
      "listening",
      28,
    );

    expect(snapshot).toEqual({
      type: "snapshot",
      phrases: [phrase(2), phrase(3)],
      status: "listening",
      fontSize: 28,
    });
  });
});
