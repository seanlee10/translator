import type { Phrase } from "./hooks/useCaptionSocket";

export type DirectionId = "en>ko" | "ko>en" | "en>zh";
/** The wire's LANGUAGE slots (TranscriptOut / CaptionOut fields). */
export type LanguageSlot = "en" | "ko" | "zh";

export interface DirectionInfo {
  id: DirectionId;
  /** Shown on the direction toggle. */
  label: string;
  sourceLang: LanguageSlot;
  targetLang: LanguageSlot;
  /** BCP 47 tag for the target text's `lang` attribute, so the browser
   * picks a Traditional-Chinese (not Simplified/Japanese) glyph set. */
  targetHtmlLang: string;
  /** Column and legend copy. */
  sourceLabel: string;
  targetLabel: string;
  /** Overlay placeholder, written in the language being produced. */
  translatingPlaceholder: string;
}

/**
 * A deliberate mirror of the backend's DIRECTIONS registry, not a fetched
 * endpoint.
 *
 * `useProviders` fetches GET /providers because WHICH providers exist varies
 * at runtime with which API keys are configured. Directions have no such
 * variance — the set is fixed at build time — so an endpoint would imitate a
 * pattern whose justification does not apply. The accepted cost is that the
 * set is declared in two places; this file is the second one.
 */
export const DIRECTIONS: DirectionInfo[] = [
  {
    id: "en>ko",
    label: "English → 한국어",
    sourceLang: "en",
    targetLang: "ko",
    sourceLabel: "English",
    targetLabel: "한국어",
    targetHtmlLang: "ko",
    translatingPlaceholder: "번역 중…",
  },
  {
    id: "ko>en",
    label: "한국어 → English",
    sourceLang: "ko",
    targetLang: "en",
    sourceLabel: "한국어",
    targetLabel: "English",
    targetHtmlLang: "en",
    translatingPlaceholder: "Translating…",
  },
  {
    id: "en>zh",
    label: "English → 繁體中文",
    sourceLang: "en",
    targetLang: "zh",
    sourceLabel: "English",
    targetLabel: "繁體中文",
    targetHtmlLang: "zh-Hant-TW",
    translatingPlaceholder: "翻譯中…",
  },
];

export const DEFAULT_DIRECTION = DIRECTIONS[0];

/** Never throws: an unknown id must not blank the UI. */
export function directionById(id: string): DirectionInfo {
  return DIRECTIONS.find((d) => d.id === id) ?? DEFAULT_DIRECTION;
}

type Slotted = Pick<Phrase, "en" | "ko"> & { zh?: string | null };

/** Read one language slot; a missing `zh` reads as null. */
function slot(phrase: Slotted, lang: LanguageSlot): string | null {
  return phrase[lang] ?? null;
}

/**
 * The spoken side of a phrase. The wire's `en`/`ko`/`zh` are LANGUAGE
 * slots, so role is derived here — the frontend mirror of
 * Direction.to_slots, and the only place the frontend decides it.
 */
export function sourceTextOf(phrase: Slotted, direction: DirectionInfo): string | null {
  return slot(phrase, direction.sourceLang);
}

/** The produced side of a phrase, or null before it arrives. */
export function targetTextOf(phrase: Slotted, direction: DirectionInfo): string | null {
  return slot(phrase, direction.targetLang);
}

/**
 * Which direction the transcript header and empty state should describe.
 *
 * With no phrases on screen there is nothing to mislabel, so show the
 * PENDING selection — otherwise the empty state tells the user to speak the
 * wrong language. Once phrases exist they must be read under the direction
 * their session actually ran in, or every column swaps.
 *
 * Safe because the direction toggle is disabled while a session is active:
 * pending and session direction can only diverge while stopped.
 */
export function displayDirection(
  phraseCount: number,
  pending: DirectionInfo,
  session: DirectionInfo,
): DirectionInfo {
  return phraseCount === 0 ? pending : session;
}
